"""`manage.py rotate_keys <email>` — emergency revocation utility.

Revokes every active API key belonging to a single user. The typical
trigger is a credential leak: support flags that a customer's
`mcpsk_...` showed up in a paste site / GitHub commit / shared
screenshot, the operator runs this command, all that user's keys go
dead within the cache TTL (≤30s), the customer mints a fresh one
from /dashboard/keys/.

This is intentionally a CLI tool rather than an admin-panel button
because it's an "I know what I'm doing" operation — there's no
confirmation modal, no two-step approval, just immediate revocation
of every key for one user. Audit row is written so the action is
attributable even if the operator's session is later compromised.

Use the admin Users page (10.2.1.3) for the click-driven per-key
revocation path.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Revoke every active API key for a single user (emergency procedure)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("email", help="Email of the user whose keys to revoke.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be revoked without actually revoking.",
        )

    def handle(self, *args: object, email: str, dry_run: bool, **options: object) -> None:
        # Late-import the api surface so this module is importable without
        # apps.api_keys ready (e.g. during a manage.py check) — keeps the
        # error message helpful instead of an obscure import error.
        from apps.api_keys import api as api_keys

        User = get_user_model()
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(f"No user with email {email!r}.")

        active_keys = [
            k for k in api_keys.list_for_user(user) if k.revoked_at is None
        ]
        if not active_keys:
            self.stdout.write(self.style.WARNING(f"{email}: no active keys."))
            return

        self.stdout.write(f"User: {user.email} (id={user.pk})")
        self.stdout.write(f"Active keys to revoke ({len(active_keys)}):")
        for k in active_keys:
            self.stdout.write(f"  - {k.prefix}…{k.last_4}  name={k.name!r}  last_used={k.last_used_at}")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes made."))
            return

        revoked_count = api_keys.revoke_all_for_user(user)
        self.stdout.write(
            self.style.SUCCESS(
                f"Revoked {revoked_count} key(s) for {user.email}. "
                "Cache invalidation propagates within ~30s."
            )
        )
