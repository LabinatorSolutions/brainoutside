"""Idempotent brain-clone bootstrap — run at container start and in dev.

`python manage.py brain_bootstrap` clones the brain repo when absent,
reuses a valid clone, and hard-fails (non-zero exit) when the contract
files are missing — a server without the contract must not come up.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.brain.services import gitrepo


class Command(BaseCommand):
    help = "Clone (or reuse) the brain repo and verify the operating contract."

    def handle(self, *args: object, **options: object) -> None:
        try:
            result = gitrepo.bootstrap()
        except gitrepo.BrainRepoError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"brain repo {result['action']} at {result['dir']} (HEAD {result['head'][:12]})"
            )
        )
