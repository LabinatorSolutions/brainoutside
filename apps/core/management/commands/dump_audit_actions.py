"""``manage.py dump_audit_actions`` — regenerate the auto-generated
audit-actions reference table in docs/RATE_LIMIT_AND_AUDIT.md (UPDATES.md
#11 codegen).

The hand-written "Who writes audit rows automatically" table in the
Concepts section stays hand-written — it carries an `is_compliance`
column and per-event narrative codegen can't reproduce. This command
instead populates a separate **Reference** subsection that lists every
audit-action string the framework actually emits, regardless of whether
it goes through a subscriber, a service module, or a view.

The walker has two passes:

1. **Direct literal calls** — any `record(action="x.y.z", ...)` or
   `audit_hook.record(action="x.y.z", ...)` call site in
   `apps/**/*.py` (skipping tests + migrations). The action string
   is the literal value of the `action=` kwarg.

2. **Event-derived calls** — calls of the shape
   `record(action=event.event_name, ...)` (the standard pattern in
   `apps/audit/events.py` subscribers). The action string comes
   from the `event_name: ClassVar[str] = "..."` on the event class
   that the surrounding `@subscribe(EventCls)` decorator pins.

Both passes union into one alphabetical table. Output is wrapped in:

    <!-- BEGIN dump_audit_actions:table -->
    ... action | source table ...
    <!-- END dump_audit_actions:table -->

Two modes:
  - Default: rewrite docs/RATE_LIMIT_AND_AUDIT.md in place.
  - ``--check``: regenerate in memory, diff against the file on disk,
    exit 1 with a unified diff when drift exists. Wired to
    ``make audit-actions-check`` for CI.

Pure AST — no Django app loading required.
"""
from __future__ import annotations

import ast
import difflib
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from django.core.management.base import BaseCommand, CommandError

_REPO_ROOT = Path(__file__).resolve().parents[4]
_AUDIT_DOC = _REPO_ROOT / "docs" / "RATE_LIMIT_AND_AUDIT.md"
_APPS_DIR = _REPO_ROOT / "apps"

_BEGIN = "<!-- BEGIN dump_audit_actions:table -->"
_END = "<!-- END dump_audit_actions:table -->"

# Names that count as "the audit record() call" at a call site. Matches
# both `from apps.audit.api import record` (most common) and
# `from apps.core import audit_hook; audit_hook.record(...)` shapes.
_RECORD_CALL_NAMES = {"record"}

# Files we skip — tests + migrations + scratch verification gates. Tests
# create audit rows constantly; surfacing them in the reference table
# would drown the real production-emitted actions in noise.
_SKIP_DIR_PARTS = {"tests", "migrations"}
_SKIP_FILE_NAMES = {"tests.py", "conftest.py"}


# ---- AST primitives ---------------------------------------------------


@dataclass(frozen=True)
class ActionEmit:
    """One call site that writes an audit row with a known action."""

    action: str
    module_path: str  # e.g. "apps.audit.events"
    lineno: int

    @property
    def file_path(self) -> str:
        """Repo-relative path, e.g. `apps/audit/events.py`."""
        return self.module_path.replace(".", "/") + ".py"


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return None


def _is_record_call(node: ast.Call) -> bool:
    """`record(...)`, `audit_hook.record(...)`, `record.record(...)` etc."""
    func = node.func
    if isinstance(func, ast.Name) and func.id in _RECORD_CALL_NAMES:
        return True
    if isinstance(func, ast.Attribute) and func.attr in _RECORD_CALL_NAMES:
        return True
    return False


def _action_kwarg(node: ast.Call) -> Optional[ast.expr]:
    """Return the `action=` keyword's value expression, or None."""
    for kw in node.keywords:
        if kw.arg == "action":
            return kw.value
    return None


def _literal_str(expr: ast.expr) -> Optional[str]:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _event_name_attr(expr: ast.expr) -> Optional[str]:
    """If the expression is `event.event_name` (or any `X.event_name`),
    return the receiver name so the caller can look the event class up
    from a surrounding `@subscribe(...)` decorator.
    """
    if (
        isinstance(expr, ast.Attribute)
        and expr.attr == "event_name"
        and isinstance(expr.value, ast.Name)
    ):
        return expr.value.id
    return None


def _surrounding_subscribed_event(func: ast.FunctionDef) -> Optional[str]:
    """If `func` has `@subscribe(EventCls)` decorator, return `"EventCls"`."""
    for dec in func.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        target = dec.func
        is_subscribe = (
            (isinstance(target, ast.Name) and target.id == "subscribe")
            or (isinstance(target, ast.Attribute) and target.attr == "subscribe")
        )
        if not is_subscribe or not dec.args:
            continue
        arg0 = dec.args[0]
        if isinstance(arg0, ast.Name):
            return arg0.id
    return None


# ---- Project-wide event_name catalog ----------------------------------


def _build_event_name_catalog() -> dict[str, str]:
    """Walk every `apps/**/events.py` and build {EventCls -> event_name}.

    Same shape as `dump_events.py`'s `_extract_event` but we only care
    about the class name + `event_name` ClassVar string. The dict
    populates lookup for `action=event.event_name` call sites.
    """
    catalog: dict[str, str] = {}
    for events_py in sorted(_APPS_DIR.glob("**/events.py")):
        tree = _parse(events_py)
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == "event_name"
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    catalog[node.name] = stmt.value.value
                    break
    return catalog


# ---- Walker -----------------------------------------------------------


def scan_audit_actions() -> list[ActionEmit]:
    """Walk apps/ and return every audit-action emit site."""
    event_names = _build_event_name_catalog()
    found: list[ActionEmit] = []

    for py in sorted(_APPS_DIR.glob("**/*.py")):
        rel = py.relative_to(_REPO_ROOT)
        if py.name == "__init__.py":
            continue
        if py.name in _SKIP_FILE_NAMES:
            continue
        if any(part in _SKIP_DIR_PARTS for part in rel.parts):
            continue

        module_path = ".".join(rel.with_suffix("").parts)
        tree = _parse(py)
        if tree is None:
            continue

        # Build a per-function map of `@subscribe(EventCls)` → EventCls so
        # we can resolve `action=event.event_name` inside the function body.
        # ast.walk loses parent context, so do a recursive descent.
        _scan_node(tree, module_path, event_names, found, in_subscribe=None)

    return found


def _scan_node(
    node: ast.AST,
    module_path: str,
    event_names: dict[str, str],
    out: list[ActionEmit],
    *,
    in_subscribe: Optional[str],
) -> None:
    """Recursive descent. Tracks the currently-entered
    `@subscribe(EventCls)` function so nested calls resolve
    `action=event.event_name` against the right class."""
    if isinstance(node, ast.FunctionDef):
        bound = _surrounding_subscribed_event(node) or in_subscribe
        for child in node.body:
            _scan_node(child, module_path, event_names, out, in_subscribe=bound)
        return
    if isinstance(node, ast.AsyncFunctionDef):
        bound = _surrounding_subscribed_event(node) or in_subscribe
        for child in node.body:
            _scan_node(child, module_path, event_names, out, in_subscribe=bound)
        return

    if isinstance(node, ast.Call) and _is_record_call(node):
        kw = _action_kwarg(node)
        if kw is not None:
            literal = _literal_str(kw)
            if literal:
                out.append(
                    ActionEmit(action=literal, module_path=module_path, lineno=node.lineno)
                )
            else:
                receiver = _event_name_attr(kw)
                if receiver == "event" and in_subscribe and in_subscribe in event_names:
                    out.append(
                        ActionEmit(
                            action=event_names[in_subscribe],
                            module_path=module_path,
                            lineno=node.lineno,
                        )
                    )

    # Recurse into child nodes (handles Module body, class bodies, try/except,
    # if/else, etc.). Function bodies are handled above so we can carry
    # the in_subscribe context.
    for child in ast.iter_child_nodes(node):
        _scan_node(child, module_path, event_names, out, in_subscribe=in_subscribe)


# ---- Render -----------------------------------------------------------


def render_table(emits: list[ActionEmit]) -> str:
    """Alphabetical (Action | Source) table. Multiple source sites for
    one action collapse into one row with comma-separated links."""
    by_action: dict[str, list[ActionEmit]] = defaultdict(list)
    for e in emits:
        by_action[e.action].append(e)

    lines: list[str] = []
    lines.append(
        "_Source: AST walk of every `record(action=...)` and "
        "`audit_hook.record(action=...)` call site under `apps/`, excluding "
        "tests + migrations. Subscribers that pass `action=event.event_name` "
        "resolve through the surrounding `@subscribe(EventCls)` decorator._"
    )
    lines.append("")
    lines.append(f"_{len(by_action)} distinct actions across {len(emits)} call sites._")
    lines.append("")
    lines.append("| Action | Source |")
    lines.append("|---|---|")
    for action in sorted(by_action):
        sites = by_action[action]
        links = ", ".join(
            f"[`{s.file_path}:{s.lineno}`](../{s.file_path}#L{s.lineno})"
            for s in sorted(sites, key=lambda x: (x.module_path, x.lineno))
        )
        lines.append(f"| `{action}` | {links} |")
    return "\n".join(lines).rstrip() + "\n"


# ---- File rewrite + check ---------------------------------------------


def rewrite_doc(text: str, table: str) -> str:
    pattern = re.compile(
        re.escape(_BEGIN) + r"\n.*?\n" + re.escape(_END),
        re.DOTALL,
    )
    if not pattern.search(text):
        raise CommandError(
            f"dump_audit_actions: missing sentinel pair {_BEGIN!r} / {_END!r} "
            f"in docs/RATE_LIMIT_AND_AUDIT.md. Add them so the codegen has a "
            f"target block."
        )
    new_block = f"{_BEGIN}\n{table}\n{_END}"
    return pattern.sub(lambda m: new_block, text, count=1)


# ---- Command ----------------------------------------------------------


class Command(BaseCommand):
    help = (
        "Regenerate the audit-actions reference table in "
        "docs/RATE_LIMIT_AND_AUDIT.md from an AST walk of `record(action=...)` "
        "call sites. Use --check in CI to fail when drift exists."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Don't write — regenerate in memory and diff against the file "
                "on disk. Exits 1 with a unified diff when drift is found, "
                "0 on clean."
            ),
        )

    def handle(self, *args, **options) -> None:
        if not _AUDIT_DOC.exists():
            raise CommandError(
                f"dump_audit_actions: docs/RATE_LIMIT_AND_AUDIT.md not found "
                f"at {_AUDIT_DOC}"
            )

        emits = scan_audit_actions()
        table = render_table(emits)
        current = _AUDIT_DOC.read_text(encoding="utf-8")
        regenerated = rewrite_doc(current, table)

        if options["check"]:
            if regenerated == current:
                self.stdout.write(
                    self.style.SUCCESS("dump_audit_actions --check: clean.")
                )
                return
            diff = "".join(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    regenerated.splitlines(keepends=True),
                    fromfile="docs/RATE_LIMIT_AND_AUDIT.md (on disk)",
                    tofile="docs/RATE_LIMIT_AND_AUDIT.md (regenerated)",
                    n=3,
                )
            )
            self.stdout.write(diff)
            raise CommandError(
                "dump_audit_actions --check: drift detected. Run "
                "`manage.py dump_audit_actions` to regenerate, then commit."
            )

        _AUDIT_DOC.write_text(regenerated, encoding="utf-8")
        n_actions = len({e.action for e in emits})
        self.stdout.write(
            self.style.SUCCESS(
                f"dump_audit_actions: wrote {_AUDIT_DOC.relative_to(_REPO_ROOT)} "
                f"({n_actions} actions · {len(emits)} call sites)."
            )
        )
