"""Per-tier materialized snapshots — the visibility boundary (PLAN.md §5).

On every reindex the builder exports `/data/brain-views/<tier>/` for
public, agents-only, and private. Both serving layers read ONLY from
snapshots: the dumb endpoints serve snapshot bytes, and (M3) each SDK
agent gets `cwd` + Read scoped to its caller-tier snapshot — an agent
physically cannot read above tier, which is the structural fix for
grill C11 ("the tier simulator is a UI label, not a boundary").

Snapshot contents for tier T:
- every Entity whose visibility <= T (tier order: public < agents-only
  < private), copied at its repo-relative path;
- for the PUBLIC tier, inline `(agents-only: ...)` spans are STRIPPED
  from included files (CLAUDE.md §4 line-level rule, grill B6);
- `raw/` files reachable from an included entity (frontmatter `source`
  or a `raw/...md` mention in the body) — raw inherits the visibility
  of what links it and stays unreachable by browsing (grill B11);
  the private tier includes all of raw/;
- a GENERATED, tier-filtered `INDEX.md` built from the Entity table
  (the real INDEX.md is a repo-side view, never served — grill B7);
- `_MANIFEST.json` with head SHA, build time, and the file list.

NOT in any snapshot: CLAUDE.md, .claude/, eval/, PENDING.md, templates,
README — infrastructure, not content. Agent prompts are composed
server-side from the canonical clone, tier-appropriately (M3).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.brain.models import Entity
from apps.brain.services import gitrepo

log = logging.getLogger(__name__)

TIERS = ("public", "agents-only", "private")
TIER_ORDER = {"public": 0, "agents-only": 1, "private": 2}

#: Inline span convention from CLAUDE.md §4 — stripped below agents-only.
_SPAN_RE = re.compile(r"\(agents-only:[^)]*\)", re.DOTALL)
_RAW_REF_RE = re.compile(r"raw/[A-Za-z0-9_\-./]+?\.md")


def views_dir() -> Path:
    return Path(settings.BRAIN_VIEWS_DIR)


def strip_agents_only_spans(text: str) -> str:
    return _SPAN_RE.sub("", text)


def _visible(tier: str):
    max_rank = TIER_ORDER[tier]
    return [e for e in Entity.objects.all() if TIER_ORDER.get(e.visibility, 2) <= max_rank]


def _inside_raw(repo: Path, rel: str) -> bool:
    """True when `rel` names a real file that is genuinely under `repo/raw/`.

    A prefix test is not enough. `_RAW_REF_RE` accepts `.` and `/`, so a
    reference like `raw/../identity/core.md` matches it and `is_file()`
    succeeds — which used to copy that file into whatever tier was being
    built, including public. Resolving first is what makes the `raw/`
    restriction mean anything, and it closes symlinks out of the repo too.
    """
    raw_root = (repo / "raw").resolve()
    try:
        target = (repo / rel).resolve()
    except OSError:
        return False
    if raw_root not in target.parents:
        return False
    return target.is_file()


def _linked_raw_paths(repo: Path, entities: list[Entity]) -> set[str]:
    """raw/ files referenced by the given entities (source field or body)."""
    found: set[str] = set()
    for e in entities:
        candidates: set[str] = set()
        if e.source.startswith("raw/"):
            candidates.add(e.source)
        p = repo / e.path
        if p.exists():
            candidates.update(_RAW_REF_RE.findall(p.read_text(encoding="utf-8", errors="replace")))
        for c in candidates:
            if _inside_raw(repo, c):
                found.add(c)
            else:
                log.warning("ignoring raw ref %r in %s (outside raw/)", c, e.path)
    return found


def _generated_index(tier: str, entities: list[Entity], head: str) -> str:
    lines = [
        f"# INDEX — generated view for tier `{tier}` (HEAD {head[:12]})",
        "",
        "One line per entity visible at this tier. Format:",
        "`- [kind] title — description | status | last-verified | path`",
        "",
    ]
    by_section = {
        "Identity": [e for e in entities if e.kind == "identity"],
        "Projects": [e for e in entities if e.kind == "project"],
        "Knowledge": [e for e in entities if e.kind in {"take", "story", "lesson", "fact"}],
        "Catalogs & lenses": [e for e in entities if e.kind in {"catalog", "lens"}],
    }
    for section, ents in by_section.items():
        if not ents:
            continue
        lines.append(f"## {section}")
        for e in sorted(ents, key=lambda x: (x.kind, x.entity_id)):
            desc = e.description or e.title
            parts = [f"- [{e.kind}] {e.title} — {desc}"]
            if e.status and e.status != "current":
                parts.append(f"status: {e.status}")
            if e.last_verified:
                parts.append(f"last-verified: {e.last_verified.isoformat()}")
            parts.append(e.path)
            lines.append(" | ".join(parts))
        lines.append("")
    return "\n".join(lines)


def build_tier(tier: str) -> dict[str, object]:
    """Build one tier's snapshot into a temp dir, then atomically swap it in."""
    repo = gitrepo.repo_dir()
    head = gitrepo.head_sha()
    entities = _visible(tier)
    strip = tier == "public"

    tmp = views_dir() / f".tmp-{tier}"
    final = views_dir() / tier
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    files: list[str] = []

    def emit(rel: str, content: str | bytes) -> None:
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            dest.write_bytes(content)
        else:
            dest.write_text(content, encoding="utf-8", newline="\n")
        files.append(rel)

    for e in entities:
        src = repo / e.path
        if not src.is_file():
            continue  # drift; the sync layer logs it
        text = src.read_text(encoding="utf-8", errors="replace")
        emit(e.path, strip_agents_only_spans(text) if strip else text)

    for rel in sorted(_linked_raw_paths(repo, entities) if tier != "private" else _all_raw(repo)):
        text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        emit(rel, strip_agents_only_spans(text) if strip else text)

    emit("INDEX.md", _generated_index(tier, entities, head))
    manifest = {
        "tier": tier,
        "head": head,
        "built_at": timezone.now().isoformat(),
        "entity_count": len(entities),
        "files": sorted(files),
    }
    emit("_MANIFEST.json", json.dumps(manifest, indent=2))

    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    return manifest


def _all_raw(repo: Path) -> set[str]:
    """Every markdown file under raw/, at any depth.

    Was `glob("*.md")` — top level only — while `_RAW_REF_RE` explicitly
    matches nested paths. A note linking `raw/interviews/2024-06.md` put
    that file in the agents-only snapshot (via `_linked_raw_paths`) but not
    in the private one, so private-tier `get-raw` 404'd where agents-only
    succeeded, contradicting this module's own "private includes all of
    raw/" contract.
    """
    if not (repo / "raw").is_dir():
        return set()
    return {
        p.relative_to(repo).as_posix()
        for p in (repo / "raw").rglob("*.md")
        if p.name != "README.md" and _inside_raw(repo, p.relative_to(repo).as_posix())
    }


def build_all() -> dict[str, dict[str, object]]:
    """Build every tier under the repo lock (consistent read of the clone)."""
    with gitrepo.repo_lock():
        return {tier: build_tier(tier) for tier in TIERS}


def tier_dir(tier: str) -> Path:
    if tier not in TIER_ORDER:
        raise ValueError(f"unknown tier: {tier}")
    return views_dir() / tier


def manifest(tier: str) -> dict[str, object] | None:
    p = tier_dir(tier) / "_MANIFEST.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
