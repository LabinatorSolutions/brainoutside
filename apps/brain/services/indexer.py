"""Frontmatter → Entity indexer.

The repo has five frontmatter dialects (grill B8) — one parser per kind:

- knowledge notes  (`knowledge/<type>/*.md`): full note schema from
  CLAUDE.md §3 (`id`, `type`, `topics`, `projects`, `source`,
  `source_url`, `date`, `status`, `superseded_by`, `visibility`).
- project cards    (`projects/*.md`): `id`, `kind: project`, `topics`,
  `visibility`, `last-verified`, free-text status inside the body's
  Status line (kept free-text).
- identity files   (`identity/*.md`): `id`, `visibility`, `last-verified`.
- lenses           (`lenses/*.md`): `lens: <name>` + promoted fields.
- catalogs         (`content-catalog/*.md` minus README): no frontmatter;
  title from the first H1.

Visibility resolution follows CLAUDE.md §4's path-default table:
explicit frontmatter wins; identity/knowledge/projects missing →
agents-only; catalogs + lenses → public. Non-entity paths (raw/, eval/,
templates, INDEX.md, PENDING.md, CLAUDE.md, .claude/, README) are never
indexed; raw/ visibility is resolved at serve time via linking notes.

INDEX.md is read ONLY to harvest one-line prose descriptions (display
text for the generated index) — never status/visibility (grill B7).
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from django.conf import settings

from apps.brain.models import Entity, SyncRun
from apps.brain.services import gitrepo

KNOWLEDGE_TYPES = {"takes": "take", "stories": "story", "lessons": "lesson", "facts": "fact"}

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
# INDEX line: `- [kind] name — description | ... | path`
_INDEX_LINE_RE = re.compile(r"^- \[[a-z]+\] (?P<name>[^—|]+)—\s*(?P<desc>[^|]+)")


@dataclass
class ParsedEntity:
    entity_id: str
    kind: str
    path: str  # repo-relative POSIX
    title: str = ""
    description: str = ""
    status: str = "current"
    superseded_by: str = ""
    visibility: str = "agents-only"
    topics: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    source: str = ""
    source_url: str = ""
    date: str = ""
    last_verified: object = None  # datetime.date | None
    content_hash: str = ""


def split_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, text[m.end():]


def _title_of(body: str, fallback: str) -> str:
    m = _H1_RE.search(body)
    return m.group(1).strip() if m else fallback


def as_list(v: object) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.strip():
        return [s.strip() for s in v.strip("[]").split(",") if s.strip()]
    return []


def _parse_date(v: object):
    import datetime

    if isinstance(v, datetime.date):
        return v
    if isinstance(v, str):
        try:
            return datetime.date.fromisoformat(v.strip())
        except ValueError:
            return None
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _harvest_index_descriptions(repo: Path) -> dict[str, str]:
    """path -> one-line description, cosmetic only (never status/visibility)."""
    out: dict[str, str] = {}
    index = repo / "INDEX.md"
    if not index.exists():
        return out
    for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("- ["):
            continue
        # The path is the last `|`-segment (with or without a label prefix).
        last = line.rsplit("|", 1)[-1].strip()
        path = last.split(":", 1)[-1].strip() if ":" in last else last
        m = _INDEX_LINE_RE.match(line)
        if path.endswith(".md") and m:
            out[path] = m.group("desc").strip()
    return out


def _resolve_visibility(explicit: object, default: str) -> str:
    v = str(explicit).strip() if explicit else ""
    return v if v in {"public", "agents-only", "private"} else default


def parse_repo() -> list[ParsedEntity]:
    """Scan the clone and return every entity, per-kind parsed."""
    repo = gitrepo.repo_dir()
    descriptions = _harvest_index_descriptions(repo)
    entities: list[ParsedEntity] = []

    def read(p: Path) -> tuple[dict, str, str]:
        raw = p.read_bytes()
        fm, body = split_frontmatter(raw.decode("utf-8", errors="replace"))
        return fm, body, _sha256(raw)

    # --- knowledge notes ---
    for folder, kind in KNOWLEDGE_TYPES.items():
        for p in sorted((repo / "knowledge" / folder).glob("*.md")):
            if p.name == "_TEMPLATE.md":
                continue
            fm, body, digest = read(p)
            rel = p.relative_to(repo).as_posix()
            entities.append(
                ParsedEntity(
                    entity_id=str(fm.get("id") or p.stem),
                    kind=str(fm.get("type") or kind),
                    path=rel,
                    title=_title_of(body, p.stem),
                    description=descriptions.get(rel, ""),
                    status=str(fm.get("status") or "current"),
                    superseded_by=str(fm.get("superseded_by") or "") if fm.get("superseded_by") else "",
                    visibility=_resolve_visibility(fm.get("visibility"), "agents-only"),
                    topics=as_list(fm.get("topics")),
                    projects=as_list(fm.get("projects")),
                    source=str(fm.get("source") or ""),
                    source_url=str(fm.get("source_url") or ""),
                    date=str(fm.get("date") or ""),
                    content_hash=digest,
                )
            )

    # --- project cards ---
    for p in sorted((repo / "projects").glob("*.md")):
        if p.name == "_TEMPLATE.md":
            continue
        fm, body, digest = read(p)
        rel = p.relative_to(repo).as_posix()
        entities.append(
            ParsedEntity(
                entity_id=str(fm.get("id") or f"project-{p.stem}"),
                kind="project",
                path=rel,
                title=_title_of(body, p.stem),
                description=descriptions.get(rel, ""),
                status="current",
                visibility=_resolve_visibility(fm.get("visibility"), "agents-only"),
                topics=as_list(fm.get("topics")),
                last_verified=_parse_date(fm.get("last-verified")),
                content_hash=digest,
            )
        )

    # --- identity ---
    for p in sorted((repo / "identity").glob("*.md")):
        if p.name == "_TEMPLATE.md":
            continue
        fm, body, digest = read(p)
        rel = p.relative_to(repo).as_posix()
        entities.append(
            ParsedEntity(
                entity_id=str(fm.get("id") or f"identity-{p.stem}"),
                kind="identity",
                path=rel,
                title=_title_of(body, p.stem),
                description=descriptions.get(rel, ""),
                visibility=_resolve_visibility(fm.get("visibility"), "agents-only"),
                last_verified=_parse_date(fm.get("last-verified")),
                content_hash=digest,
            )
        )

    # --- lenses ---
    for p in sorted((repo / "lenses").glob("*.md")):
        if p.name == "_TEMPLATE.md":
            continue
        fm, body, digest = read(p)
        rel = p.relative_to(repo).as_posix()
        entities.append(
            ParsedEntity(
                entity_id=f"lens-{fm.get('lens') or p.stem}",
                kind="lens",
                path=rel,
                title=_title_of(body, p.stem),
                description=descriptions.get(rel, ""),
                visibility=_resolve_visibility(fm.get("visibility"), "public"),
                topics=as_list(fm.get("topics")),
                content_hash=digest,
            )
        )

    # --- catalogs (no frontmatter) ---
    for p in sorted((repo / "content-catalog").glob("*.md")):
        if p.name in {"README.md", "_TEMPLATE.md"}:
            continue
        raw = p.read_bytes()
        body = raw.decode("utf-8", errors="replace")
        rel = p.relative_to(repo).as_posix()
        entities.append(
            ParsedEntity(
                entity_id=f"catalog-{p.stem}",
                kind="catalog",
                path=rel,
                title=_title_of(body, f"{p.stem} catalog"),
                description=descriptions.get(rel, ""),
                visibility="public",
                content_hash=_sha256(raw),
            )
        )

    return entities


def state_hash(entities: list[ParsedEntity] | None = None) -> str:
    """Deterministic hash of repo-parsed state — the drift comparator."""
    ents = entities if entities is not None else parse_repo()
    lines = sorted(f"{e.entity_id}\t{e.path}\t{e.content_hash}\t{e.visibility}\t{e.status}" for e in ents)
    return _sha256("\n".join(lines).encode())


def db_state_hash() -> str:
    rows = Entity.objects.values_list("entity_id", "path", "content_hash", "visibility", "status")
    lines = sorted(f"{a}\t{b}\t{c}\t{d}\t{e}" for a, b, c, d, e in rows)
    return _sha256("\n".join(lines).encode())


def rebuild(trigger: str = "manual") -> SyncRun:
    """Full scan → upsert → prune. One SyncRun row per rebuild."""
    t0 = time.monotonic()
    run = SyncRun.objects.create(trigger=trigger)
    try:
        run.commit_sha = gitrepo.head_sha()
        parsed = parse_repo()
        seen_paths = set()
        existing = {o.path: o for o in Entity.objects.all()}
        added = changed = 0
        for e in parsed:
            seen_paths.add(e.path)
            values = dict(
                entity_id=e.entity_id,
                kind=e.kind,
                title=e.title,
                description=e.description,
                status=e.status,
                superseded_by=e.superseded_by,
                visibility=e.visibility,
                topics=e.topics,
                projects=e.projects,
                source=e.source,
                source_url=e.source_url,
                date=e.date,
                last_verified=e.last_verified,
                content_hash=e.content_hash,
            )
            obj = existing.get(e.path)
            if obj is None:
                Entity.objects.create(path=e.path, **values)
                added += 1
                continue
            # Field-by-field, not content_hash: a parser change must still
            # repair rows whose file bytes didn't move (drift self-repair
            # rebuilds through here). Identical rows are skipped so `changed`
            # counts real changes, not upserts.
            dirty = [f for f, v in values.items() if getattr(obj, f) != v]
            if dirty:
                for f in dirty:
                    setattr(obj, f, values[f])
                obj.save(update_fields=dirty)
                changed += 1
        removed, _ = Entity.objects.exclude(path__in=seen_paths).delete()
        run.added, run.changed, run.removed = added, changed, removed
        run.ok = True
    except Exception as exc:
        run.ok = False
        run.error = str(exc)
        raise
    finally:
        import django.utils.timezone as tz

        run.finished_at = tz.now()
        run.duration_ms = int((time.monotonic() - t0) * 1000)
        run.save()
    return run
