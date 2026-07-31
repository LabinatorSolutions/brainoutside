"""`manage.py rotate_keys <email>` — emergency revocation utility.

Revokes every active API key belonging to a single user. The trigger is
a credential leak: an `mcpsk_...` shows up in a paste site / a commit /
a shared screenshot and you want everything dead now, without deciding
which key it was. Revocation is immediate — `apps.api_keys.events` busts
the cached Principal on each revoke rather than waiting out the 60s TTL.

This stays a CLI tool rather than an ops-console button because it is an
"I know what I'm doing" operation: no confirmation, no per-key choice,
just every key for one user gone. Per-key revoke, rotate and tier
changes are the click-driven path, at `/<ops-prefix>/consumers/`.
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
                "The cached Principal for each was busted inline — the next "
                "call with any of them fails."
            )
        )
