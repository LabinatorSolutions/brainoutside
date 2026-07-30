"""Delete Claude CLI session transcripts past their retention window.

The bundled CLI writes JSONL session files under ~/.claude/projects/…
inside every container that runs SDK agents. They contain note content
in plaintext (grill C3: sensitive), so they get a retention window, not
immortality. Wire into cron/Q2 at deploy (DEPLOY.md); running it any
time is harmless.
"""
from __future__ import annotations

import time
from pathlib import Path

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Delete SDK session transcripts older than --days (default 7)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args: object, **options: object) -> None:
        days = int(options["days"])
        cutoff = time.time() - days * 86400
        root = Path.home() / ".claude" / "projects"
        removed = 0
        if root.is_dir():
            for p in root.rglob("*.jsonl"):
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                        removed += 1
                except OSError:
                    pass
        self.stdout.write(
            self.style.SUCCESS(f"removed {removed} transcript file(s) older than {days}d")
        )
