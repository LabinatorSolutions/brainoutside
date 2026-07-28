"""Rotate the FIELD_ENCRYPTION_KEY across every django-cryptography column.

Phase P3 / PLAN2. Operator workflow:

    # 1. Generate a NEW Fernet key.
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    # 2. Run the rotation. The OLD value is whatever's currently in env;
    #    the NEW value is what you'll deploy next.
    python manage.py rotate_field_encryption_key \\
        --old="<current FIELD_ENCRYPTION_KEY>" \\
        --new="<the key you just generated>" \\
        --batch-size=100

    # 3. Once the rotation completes (process exits 0), update env to the
    #    NEW key and redeploy. Subsequent reads now find ciphertext that
    #    was rewritten with the NEW key during step 2.

The command walks every model in INSTALLED_APPS, finds fields wrapped by
`django_cryptography.fields.encrypt(...)`, and re-encrypts each row's
ciphertext in batches:

  - Reads the RAW ciphertext via the DB cursor (bypassing
    `from_db_value`'s decrypt-on-read).
  - Decrypts with the OLD key.
  - Re-encrypts with the NEW key.
  - Writes back via raw UPDATE (bypassing `get_db_prep_value`'s
    encrypt-on-write).

Idempotent. If a row's ciphertext is already encrypted with the NEW
key (e.g. a previous interrupted run already migrated it), the OLD
key fails to decrypt → we try the NEW key → success → skip the row.
This makes `--resume-from` semantically unnecessary; just re-run the
command.

`--dry-run` reports what would change without writing.
`--batch-size N` controls the row-fetch + UPDATE batch (default 100).

The underlying `django-cryptography-django5` envelope is AES-CBC with
HMAC-SHA256 signing. Keys are run through PBKDF2-SHA256 (30000
iterations, salt `b"django-cryptography"`) to derive the 32-byte AES
key — the same derivation `CryptographyConf.configure()` does at boot
when the setting `CRYPTOGRAPHY_KEY` is set. Re-implementing the
derivation here keeps the command independent of Django's settings
state (we don't need to override-settings + reload models mid-process).
"""
from __future__ import annotations

import logging
import pickle
import sys
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from django.apps import apps as django_apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils.encoding import force_bytes
from django_cryptography.fields import EncryptedMixin
from django_cryptography.utils.crypto import FernetBytes, InvalidToken

log = logging.getLogger(__name__)

# Mirrors `django_cryptography.conf.CryptographyConf` defaults so the
# derivation here matches what the library uses at boot. If a forker
# customizes either (via `CRYPTOGRAPHY_DIGEST` / `CRYPTOGRAPHY_SALT`),
# they'll need to mirror the change here — at v1.0 we don't override
# either, so the constants are safe to hard-code.
_KDF_SALT = b"django-cryptography"
_KDF_ITERATIONS = 30000


def _derive_aes_key(input_key: str | bytes) -> bytes:
    """Replicate `CryptographyConf.configure`'s KDF for the AES key bytes.

    The library digest is SHA-256 with a 32-byte output, so we derive
    32 bytes from the input key + salt + iteration count. The result
    is what `FernetBytes` will use as its AES key.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=hashes.SHA256().digest_size,
        salt=_KDF_SALT,
        iterations=_KDF_ITERATIONS,
    )
    return kdf.derive(force_bytes(input_key))


def _encrypted_fields() -> list[tuple[type, Any]]:
    """Discover every concrete `(model, field)` pair where `field` is an
    EncryptedMixin subclass. Skips abstract + proxy models — they have
    no DB table to rewrite."""
    pairs: list[tuple[type, Any]] = []
    for model in django_apps.get_models():
        if model._meta.abstract or model._meta.proxy:
            continue
        for field in model._meta.get_fields():
            if isinstance(field, EncryptedMixin):
                pairs.append((model, field))
    return pairs


class Command(BaseCommand):
    help = (
        "Rotate FIELD_ENCRYPTION_KEY across every django-cryptography-encrypted "
        "column. Reads each row with the OLD key, re-encrypts with the NEW key, "
        "writes back. Idempotent — a second run is a no-op (rows already on the "
        "NEW key fail OLD-key decrypt, get skipped via NEW-key probe)."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--old",
            required=True,
            help="The current FIELD_ENCRYPTION_KEY (Fernet base64 string).",
        )
        parser.add_argument(
            "--new",
            required=True,
            help="The new FIELD_ENCRYPTION_KEY to migrate every encrypted row to.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Rows per fetch + UPDATE batch (default 100).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        old_key = opts["old"]
        new_key = opts["new"]
        batch_size: int = int(opts["batch_size"])
        dry_run: bool = bool(opts["dry_run"])

        if not old_key or not new_key:
            raise CommandError("--old and --new are both required.")
        if old_key == new_key:
            raise CommandError("--old and --new must differ.")

        old_fernet = FernetBytes(_derive_aes_key(old_key))
        new_fernet = FernetBytes(_derive_aes_key(new_key))

        pairs = _encrypted_fields()
        if not pairs:
            self.stdout.write("No encrypted fields discovered. Nothing to do.")
            return

        total_re_encrypted = 0
        total_already_new = 0
        total_corrupt = 0

        for model, field in pairs:
            label = f"{model._meta.label}.{field.name}"
            self.stdout.write(f"== {label} ==")
            re_encrypted, already_new, corrupt = self._rotate_field(
                model,
                field,
                old_fernet=old_fernet,
                new_fernet=new_fernet,
                batch_size=batch_size,
                dry_run=dry_run,
            )
            self.stdout.write(
                f"   re-encrypted: {re_encrypted}  already-new: {already_new}  corrupt: {corrupt}"
            )
            total_re_encrypted += re_encrypted
            total_already_new += already_new
            total_corrupt += corrupt

        self.stdout.write(self.style.SUCCESS("Rotation complete."))
        self.stdout.write(
            f"Totals: re-encrypted={total_re_encrypted} "
            f"already-new={total_already_new} corrupt={total_corrupt}"
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("(dry-run — no writes were issued)"))
        if total_corrupt:
            # Non-fatal — surface to the operator so they can investigate.
            self.stdout.write(
                self.style.WARNING(
                    f"WARNING: {total_corrupt} row(s) failed BOTH old-key and new-key "
                    "decrypt. Investigate before swapping env to the new key."
                )
            )
            sys.exit(2)

    def _rotate_field(
        self,
        model: type,
        field: Any,
        *,
        old_fernet: FernetBytes,
        new_fernet: FernetBytes,
        batch_size: int,
        dry_run: bool,
    ) -> tuple[int, int, int]:
        """Walk one (model, field) pair. Returns (re_encrypted, already_new, corrupt)."""
        table = model._meta.db_table
        pk_column = model._meta.pk.column
        col = field.column

        re_encrypted = 0
        already_new = 0
        corrupt = 0

        # Cursor over PKs only first — keeps the working set small.
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT "{pk_column}" FROM "{table}" ORDER BY "{pk_column}"')
            all_pks = [row[0] for row in cursor.fetchall()]

        for start in range(0, len(all_pks), batch_size):
            batch_pks = all_pks[start : start + batch_size]
            placeholders = ",".join(["%s"] * len(batch_pks))
            with connection.cursor() as cursor:
                cursor.execute(
                    f'SELECT "{pk_column}", "{col}" FROM "{table}" '
                    f'WHERE "{pk_column}" IN ({placeholders})',
                    batch_pks,
                )
                rows = cursor.fetchall()

            updates: list[tuple[bytes, Any]] = []
            for pk, ciphertext in rows:
                if ciphertext is None:
                    continue
                raw = bytes(ciphertext) if not isinstance(ciphertext, (bytes, bytearray)) else bytes(ciphertext)
                # Attempt OLD-key decrypt first.
                try:
                    plaintext = old_fernet.decrypt(raw)
                except (InvalidToken, Exception):
                    # Fall back to NEW-key probe — this catches the "already
                    # migrated" case so reruns become no-ops.
                    try:
                        new_fernet.decrypt(raw)
                        already_new += 1
                    except Exception:
                        corrupt += 1
                        log.warning(
                            "rotate_field_encryption_key: row %s.%s pk=%s ciphertext "
                            "failed BOTH old-key and new-key decrypt",
                            model._meta.label,
                            field.name,
                            pk,
                        )
                    continue

                # Successfully decrypted with OLD. Re-encrypt with NEW.
                # `plaintext` is bytes (it's the inner pickle stream that
                # EncryptedMixin handed to FernetBytes.encrypt at write time).
                # We DON'T need to unpickle / repickle — preserving the
                # exact bytes round-trips through any base_class.
                # Cross-check: confirm the pickle stream is well-formed
                # so we don't silently propagate a corrupt row.
                try:
                    pickle.loads(plaintext)
                except Exception:
                    log.warning(
                        "rotate_field_encryption_key: row %s.%s pk=%s decrypted "
                        "successfully but pickle.loads failed — skipping to avoid corruption",
                        model._meta.label,
                        field.name,
                        pk,
                    )
                    corrupt += 1
                    continue

                new_ciphertext = new_fernet.encrypt(plaintext)
                updates.append((new_ciphertext, pk))

            if updates and not dry_run:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        for new_ct, pk in updates:
                            cursor.execute(
                                f'UPDATE "{table}" SET "{col}" = %s WHERE "{pk_column}" = %s',
                                [connection.Database.Binary(new_ct), pk],
                            )
            re_encrypted += len(updates)

        return re_encrypted, already_new, corrupt
