"""``manage.py dump_events`` — regenerate the auto-generated parts of
docs/EVENTS.md from the project's `events.py` files.

The hand-written narrative in EVENTS.md (How the bus works, Webhook
event delivery, Adding a new event, Adding a subscriber) stays
hand-written. Only two sections are regenerated, each wrapped in HTML
sentinel comments so the rest of the file is untouched:

    <!-- BEGIN dump_events:catalog -->
    ... per-app event tables (Event | Payload | Emitted from) ...
    <!-- END dump_events:catalog -->

    <!-- BEGIN dump_events:subscribers -->
    ... event-by-app subscriber matrix ...
    <!-- END dump_events:subscribers -->

Inputs are pure AST walks (no Django app loading needed for the data
extraction itself), so a stale `.pyc` or a refactor that bypasses the
runtime registry still reflects the current source.

Two modes:
  - Default: rewrite docs/EVENTS.md in place. Exits 0 on success.
  - ``--check``: regenerate in memory, compare against the file on
    disk, exit 0 when identical / exit 1 with a unified diff when
    drift exists. Wired to ``make events-check`` for CI.
"""
from __future__ import annotations

import ast
import difflib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from django.core.management.base import BaseCommand, CommandError

# Project root — this file lives at apps/core/management/commands/dump_events.py
# so parents[4] points at the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_EVENTS_DOC = _REPO_ROOT / "docs" / "EVENTS.md"
_APPS_DIR = _REPO_ROOT / "apps"

_CATALOG_BEGIN = "<!-- BEGIN dump_events:catalog -->"
_CATALOG_END = "<!-- END dump_events:catalog -->"
_SUBSCRIBERS_BEGIN = "<!-- BEGIN dump_events:subscribers -->"
_SUBSCRIBERS_END = "<!-- END dump_events:subscribers -->"


# ---- AST extraction primitives ----------------------------------------


@dataclass(frozen=True)
class EventField:
    name: str
    annotation: str
    has_default: bool

    def render(self) -> str:
        suffix = "?" if self.has_default else ""
        return f"`{self.name}{suffix}: {self.annotation}`"


@dataclass(frozen=True)
class EventDefinition:
    """One Event subclass found in an app's events.py."""

    cls_name: str
    event_name: str  # the ClassVar[str], e.g. "billing.credits.low"
    app_label: str  # e.g. "billing", "accounts"
    module_path: str  # e.g. "apps.billing.events"
    fields: tuple[EventField, ...]
    docstring_first_line: str


@dataclass(frozen=True)
class Subscriber:
    """One @subscribe(EventCls) handler."""

    event_cls_name: str
    app_label: str
    module_path: str
    func_name: str
    lineno: int


@dataclass(frozen=True)
class EmitSite:
    """One fire(EventCls(...)) call site."""

    event_cls_name: str
    module_path: str  # e.g. "apps.billing.services.credit_service"
    lineno: int


@dataclass
class ProjectIndex:
    events: dict[str, EventDefinition] = field(default_factory=dict)  # cls_name → def
    subscribers: list[Subscriber] = field(default_factory=list)
    emit_sites: list[EmitSite] = field(default_factory=list)


def scan_project() -> ProjectIndex:
    """Walk apps/ and build the project index."""
    index = ProjectIndex()

    # Step 1: discover Event classes + @subscribe decorators in every
    # events.py we can find under apps/.
    for events_py in sorted(_APPS_DIR.glob("**/events.py")):
        rel = events_py.relative_to(_REPO_ROOT)
        module_path = ".".join(rel.with_suffix("").parts)
        app_label = _app_label_from_module(module_path)
        tree = _parse(events_py)
        if tree is None:
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and _is_event_subclass(node):
                ev = _extract_event(node, app_label=app_label, module_path=module_path)
                if ev is not None:
                    index.events[ev.cls_name] = ev
            elif isinstance(node, ast.FunctionDef):
                for sub in _extract_subscribers(node, app_label=app_label, module_path=module_path):
                    index.subscribers.append(sub)

    # Step 2: discover fire(EventCls(...)) sites across all apps/**/*.py.
    # Skip __init__.py (noise) and any tests/ directory (test-only emit
    # sites would clutter the "where this event is emitted from" column
    # without telling buyers anything about the production wiring).
    known_cls = set(index.events.keys())
    for py in sorted(_APPS_DIR.glob("**/*.py")):
        if py.name == "__init__.py":
            continue
        rel = py.relative_to(_REPO_ROOT)
        # Filter out test modules — anywhere under a `tests/` package OR
        # a top-level `tests.py` inside an app. apps/app_endpoints/*/tests.py
        # is the standard student-extensible test location.
        parts = rel.parts
        if "tests" in parts or rel.name in {"tests.py", "conftest.py"}:
            continue
        module_path = ".".join(rel.with_suffix("").parts)
        tree = _parse(py)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _fire_target_cls(node)
            if target and target in known_cls:
                index.emit_sites.append(
                    EmitSite(
                        event_cls_name=target,
                        module_path=module_path,
                        lineno=node.lineno,
                    )
                )

    return index


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return None


def _app_label_from_module(module_path: str) -> str:
    """`apps.billing.events` → `billing`. `apps.app_endpoints.foo.events` → `foo`."""
    parts = module_path.split(".")
    # apps.<label>.events or apps.<group>.<label>.events
    if len(parts) >= 3 and parts[0] == "apps":
        # If parts[1] is a known grouping (app_endpoints), use parts[2].
        if parts[1] == "app_endpoints" and len(parts) >= 4:
            return parts[2]
        return parts[1]
    return parts[0]


def _is_event_subclass(node: ast.ClassDef) -> bool:
    """Does this ClassDef inherit (directly) from `Event`?"""
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Event":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Event":
            return True
    return False


def _extract_event(
    node: ast.ClassDef, *, app_label: str, module_path: str
) -> EventDefinition | None:
    event_name = ""
    fields: list[EventField] = []
    for stmt in node.body:
        # Look for `event_name: ClassVar[str] = "..."`
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            name = stmt.target.id
            if name == "event_name":
                if isinstance(stmt.value, ast.Constant) and isinstance(
                    stmt.value.value, str
                ):
                    event_name = stmt.value.value
                continue
            # Skip ClassVar annotations — those are class-level metadata,
            # not Pydantic instance fields.
            if _is_classvar_annotation(stmt.annotation):
                continue
            fields.append(
                EventField(
                    name=name,
                    annotation=_render_annotation(stmt.annotation),
                    has_default=stmt.value is not None,
                )
            )
    if not event_name:
        # Not a real domain event (could be a re-import alias)
        return None
    return EventDefinition(
        cls_name=node.name,
        event_name=event_name,
        app_label=app_label,
        module_path=module_path,
        fields=tuple(fields),
        docstring_first_line=_docstring_first_line(node),
    )


def _is_classvar_annotation(annotation: ast.expr | None) -> bool:
    """Detect `ClassVar[...]` annotations."""
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        return annotation.value.id == "ClassVar"
    if isinstance(annotation, ast.Name) and annotation.id == "ClassVar":
        return True
    return False


def _render_annotation(node: ast.expr | None) -> str:
    """Best-effort string of an annotation. ast.unparse works in 3.9+."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "?"


def _docstring_first_line(node: ast.ClassDef) -> str:
    doc = ast.get_docstring(node)
    if not doc:
        return ""
    first = doc.strip().splitlines()[0].strip()
    # Trim very long lines so the catalog rows stay readable.
    return first if len(first) <= 120 else first[:117] + "…"


def _extract_subscribers(
    node: ast.FunctionDef, *, app_label: str, module_path: str
) -> list[Subscriber]:
    out: list[Subscriber] = []
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        # Match @subscribe(EventCls)
        func = dec.func
        is_subscribe = (
            (isinstance(func, ast.Name) and func.id == "subscribe")
            or (isinstance(func, ast.Attribute) and func.attr == "subscribe")
        )
        if not is_subscribe or not dec.args:
            continue
        arg0 = dec.args[0]
        target = arg0.id if isinstance(arg0, ast.Name) else None
        if not target:
            continue
        out.append(
            Subscriber(
                event_cls_name=target,
                app_label=app_label,
                module_path=module_path,
                func_name=node.name,
                lineno=node.lineno,
            )
        )
    return out


# Two `_afire = sync_to_async(fire, thread_sensitive=True)` aliases
# live in apps/observability/middleware.py and apps/mcp_proxy/views.py
# so the async middleware path can await dispatch. Treat them as fire
# sites for catalog purposes — they're functionally `fire` with a
# coroutine wrapper.
_FIRE_CALL_NAMES = {"fire", "_afire"}


def _fire_target_cls(node: ast.Call) -> Optional[str]:
    """If this is `fire(EventCls(...))`, `events.fire(EventCls(...))`,
    or one of the recognized async aliases (`_afire(EventCls(...))`),
    return the EventCls name. Otherwise None.
    """
    func = node.func
    is_fire_call = (
        (isinstance(func, ast.Name) and func.id in _FIRE_CALL_NAMES)
        or (isinstance(func, ast.Attribute) and func.attr in _FIRE_CALL_NAMES)
    )
    if not is_fire_call or not node.args:
        return None
    arg0 = node.args[0]
    # fire(EventCls(...))
    if isinstance(arg0, ast.Call):
        inner = arg0.func
        if isinstance(inner, ast.Name):
            return inner.id
        if isinstance(inner, ast.Attribute):
            return inner.attr
    return None


# ---- Markdown rendering -----------------------------------------------


def render_catalog(index: ProjectIndex) -> str:
    """Per-app tables of (Event | Payload | Emitted from)."""
    # Bucket events by their owning app, alphabetical.
    by_app: dict[str, list[EventDefinition]] = defaultdict(list)
    for ev in index.events.values():
        by_app[ev.app_label].append(ev)

    # Build emit-site lookup: event cls_name -> [module_path, ...]
    emit_lookup: dict[str, set[str]] = defaultdict(set)
    for site in index.emit_sites:
        emit_lookup[site.event_cls_name].add(site.module_path)

    out: list[str] = []
    for app_label in sorted(by_app):
        out.append(f"### {app_label}\n")
        out.append("| Event | Payload | Emitted from |")
        out.append("|---|---|---|")
        for ev in sorted(by_app[app_label], key=lambda e: e.cls_name):
            payload = ", ".join(f.render() for f in ev.fields) or "_(no fields)_"
            sites = sorted(emit_lookup.get(ev.cls_name, set()))
            emitter = ", ".join(f"`{s}`" for s in sites) or "_(not emitted yet)_"
            row = f"| `{ev.cls_name}` <br><sub>`{ev.event_name}`</sub> | {payload} | {emitter} |"
            out.append(row)
        out.append("")  # trailing blank line per section
    return "\n".join(out).rstrip() + "\n"


def render_subscribers(index: ProjectIndex) -> str:
    """One row per event, one column per subscribing app."""
    by_event: dict[str, list[Subscriber]] = defaultdict(list)
    for sub in index.subscribers:
        by_event[sub.event_cls_name].append(sub)

    # Subscriber apps — alphabetical, only those that actually subscribe.
    sub_apps = sorted({sub.app_label for sub in index.subscribers})
    if not sub_apps:
        return "_(no subscribers found)_\n"

    out: list[str] = []
    out.append("| Event | " + " | ".join(sub_apps) + " |")
    out.append("|" + "---|" * (len(sub_apps) + 1))

    # Every event in the catalog gets a row (even if no subscriber);
    # that's the value-add — operators see at a glance which events
    # nobody cares about yet.
    for cls_name in sorted(index.events.keys()):
        cells: list[str] = []
        ev = index.events[cls_name]
        for app in sub_apps:
            hits = [s for s in by_event.get(cls_name, ()) if s.app_label == app]
            if not hits:
                cells.append("—")
                continue
            parts = [
                f"[`{s.func_name}`]"
                f"(../{_module_to_path(s.module_path)}#L{s.lineno})"
                for s in hits
            ]
            cells.append(" / ".join(parts))
        out.append(f"| `{cls_name}` <br><sub>`{ev.event_name}`</sub> | " + " | ".join(cells) + " |")

    return "\n".join(out) + "\n"


def _module_to_path(module: str) -> str:
    return module.replace(".", "/") + ".py"


# ---- File rewrite + check ---------------------------------------------


def rewrite_doc(text: str, catalog: str, subscribers: str) -> str:
    """Replace the two sentinel blocks in `text`. Raises if either is
    missing — the contract is that EVENTS.md ships with both block
    pairs already in place."""
    new_text = _replace_block(
        text, _CATALOG_BEGIN, _CATALOG_END, catalog, label="catalog"
    )
    new_text = _replace_block(
        new_text, _SUBSCRIBERS_BEGIN, _SUBSCRIBERS_END, subscribers, label="subscribers"
    )
    return new_text


def _replace_block(text: str, begin: str, end: str, replacement: str, *, label: str) -> str:
    pattern = re.compile(
        re.escape(begin) + r"\n.*?\n" + re.escape(end),
        re.DOTALL,
    )
    if not pattern.search(text):
        raise CommandError(
            f"dump_events: missing sentinel pair for '{label}' in docs/EVENTS.md "
            f"({begin!r} / {end!r}). Add them so the codegen has a target block."
        )
    new_block = f"{begin}\n{replacement}\n{end}"
    return pattern.sub(lambda m: new_block, text, count=1)


# ---- Command ----------------------------------------------------------


class Command(BaseCommand):
    help = (
        "Regenerate the auto-generated sections of docs/EVENTS.md from "
        "apps/**/events.py introspection. Use --check in CI to fail when "
        "drift exists."
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
        if not _EVENTS_DOC.exists():
            raise CommandError(f"dump_events: docs/EVENTS.md not found at {_EVENTS_DOC}")

        index = scan_project()
        catalog_md = render_catalog(index)
        subscribers_md = render_subscribers(index)
        current = _EVENTS_DOC.read_text(encoding="utf-8")
        regenerated = rewrite_doc(current, catalog_md, subscribers_md)

        if options["check"]:
            if regenerated == current:
                self.stdout.write(self.style.SUCCESS("dump_events --check: clean."))
                return
            diff = "".join(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    regenerated.splitlines(keepends=True),
                    fromfile="docs/EVENTS.md (on disk)",
                    tofile="docs/EVENTS.md (regenerated)",
                    n=3,
                )
            )
            self.stdout.write(diff)
            raise CommandError(
                "dump_events --check: drift detected. Run `manage.py dump_events` "
                "to regenerate, then commit the result."
            )

        _EVENTS_DOC.write_text(regenerated, encoding="utf-8")
        n_events = len(index.events)
        n_subs = len(index.subscribers)
        n_sites = len(index.emit_sites)
        self.stdout.write(
            self.style.SUCCESS(
                f"dump_events: wrote {_EVENTS_DOC.relative_to(_REPO_ROOT)} "
                f"({n_events} events · {n_subs} subscribers · {n_sites} emit sites)."
            )
        )
