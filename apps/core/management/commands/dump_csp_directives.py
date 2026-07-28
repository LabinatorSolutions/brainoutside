"""``manage.py dump_csp_directives`` — regenerate the auto-generated CSP
directives reference table in docs/SECURITY.md (UPDATES.md #11 codegen).

The hand-written "Default directives" table in the SECURITY.md Concepts
section stays hand-written — it carries a "Why" column that codegen
can't reproduce. This command instead populates a separate
**Reference** subsection that is the authoritative "what is actually
configured in source right now" view.

Output is wrapped in HTML sentinel comments so the rest of the doc
is untouched:

    <!-- BEGIN dump_csp_directives:table -->
    ... directive | configured value table ...
    <!-- END dump_csp_directives:table -->

Two modes:
  - Default: rewrite docs/SECURITY.md in place. Exits 0 on success.
  - ``--check``: regenerate in memory, compare against the file on
    disk, exit 0 when identical / exit 1 with a unified diff when
    drift exists. Wired to ``make csp-directives-check`` for CI.

Inputs come from a direct import of
`apps.core.security.headers._DEFAULT_DIRECTIVES` — that's the dict the
middleware actually substitutes the nonce into and ships in every
response header, so the table can't drift from prod behavior.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SECURITY_DOC = _REPO_ROOT / "docs" / "SECURITY.md"

_BEGIN = "<!-- BEGIN dump_csp_directives:table -->"
_END = "<!-- END dump_csp_directives:table -->"


def render_table() -> str:
    """Render the alphabetical (Directive | Configured value) table.

    Imports `_DEFAULT_DIRECTIVES` at call time so a `manage.py` invocation
    that doesn't need this command doesn't pay the import cost.
    """
    from apps.core.security.headers import _DEFAULT_DIRECTIVES, _PERMISSIONS_POLICY

    lines: list[str] = []
    lines.append(
        "_Source: [`apps/core/security/headers.py`](../apps/core/security/headers.py). "
        "`<random>` is the per-request 128-bit nonce minted by "
        "`SecurityHeadersMiddleware`._"
    )
    lines.append("")
    lines.append("| Directive | Configured value |")
    lines.append("|---|---|")
    for name in sorted(_DEFAULT_DIRECTIVES):
        value = _DEFAULT_DIRECTIVES[name].replace("__CSP_NONCE__", "<random>")
        lines.append(f"| `{name}` | `{value}` |")
    lines.append("")
    lines.append(
        f"**Permissions-Policy** (`SecurityHeadersMiddleware` adds the same "
        f"value on every response):"
    )
    lines.append("")
    lines.append(f"`{_PERMISSIONS_POLICY}`")
    return "\n".join(lines).rstrip() + "\n"


def rewrite_doc(text: str, table: str) -> str:
    pattern = re.compile(
        re.escape(_BEGIN) + r"\n.*?\n" + re.escape(_END),
        re.DOTALL,
    )
    if not pattern.search(text):
        raise CommandError(
            f"dump_csp_directives: missing sentinel pair {_BEGIN!r} / {_END!r} "
            f"in docs/SECURITY.md. Add them so the codegen has a target block."
        )
    new_block = f"{_BEGIN}\n{table}\n{_END}"
    return pattern.sub(lambda m: new_block, text, count=1)


class Command(BaseCommand):
    help = (
        "Regenerate the CSP-directives reference table in docs/SECURITY.md "
        "from apps.core.security.headers._DEFAULT_DIRECTIVES. Use --check in "
        "CI to fail when drift exists."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Don't write — regenerate in memory and diff against the "
                "file on disk. Exits 1 with a unified diff when drift is "
                "found, 0 on clean."
            ),
        )

    def handle(self, *args, **options) -> None:
        if not _SECURITY_DOC.exists():
            raise CommandError(
                f"dump_csp_directives: docs/SECURITY.md not found at {_SECURITY_DOC}"
            )

        table = render_table()
        current = _SECURITY_DOC.read_text(encoding="utf-8")
        regenerated = rewrite_doc(current, table)

        if options["check"]:
            if regenerated == current:
                self.stdout.write(
                    self.style.SUCCESS("dump_csp_directives --check: clean.")
                )
                return
            diff = "".join(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    regenerated.splitlines(keepends=True),
                    fromfile="docs/SECURITY.md (on disk)",
                    tofile="docs/SECURITY.md (regenerated)",
                    n=3,
                )
            )
            self.stdout.write(diff)
            raise CommandError(
                "dump_csp_directives --check: drift detected. Run "
                "`manage.py dump_csp_directives` to regenerate, then commit."
            )

        _SECURITY_DOC.write_text(regenerated, encoding="utf-8")
        from apps.core.security.headers import _DEFAULT_DIRECTIVES

        n = len(_DEFAULT_DIRECTIVES)
        self.stdout.write(
            self.style.SUCCESS(
                f"dump_csp_directives: wrote {_SECURITY_DOC.relative_to(_REPO_ROOT)} "
                f"({n} directives)."
            )
        )
