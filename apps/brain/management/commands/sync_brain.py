"""Manual/beat sync: pull → drift check → reindex → snapshots.

The 15-minute fallback beat (PLAN.md §4) schedules this via django-q2 at
deploy; it is also the operator's by-hand sync.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.brain.services import gitrepo, sync


class Command(BaseCommand):
    help = "Run the full brain sync pipeline (pull, reindex, snapshots, drift check)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--trigger", default="manual", choices=["manual", "beat"])
        parser.add_argument(
            "--no-pull",
            action="store_true",
            help="Skip the git pull (credential-less local dev: the host "
            "owns the pull; this reindexes + rebuilds snapshots only).",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            run = sync.sync(trigger=str(options["trigger"]), pull=not options["no_pull"])
        except (gitrepo.BrainRepoError, sync.SyncError) as exc:
            raise CommandError(str(exc)) from exc
        flag = " [DRIFT repaired]" if run.drift_detected else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"synced at {run.commit_sha[:12]}: +{run.added} ~{run.changed} "
                f"-{run.removed} in {run.duration_ms}ms{flag}"
            )
        )
