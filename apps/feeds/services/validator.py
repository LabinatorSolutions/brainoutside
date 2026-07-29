"""Proposal validator — rules 1–8 as pure functions (PLAN.md §4, M2.3).

Runs on every feed proposal pre-commit: after extraction (annotating the
approval UI), after every human edit (M2.4 re-validate), and as the last
gate before the M2.5 commit. Note rules apply to `knowledge/` only
(grill B8); other paths get their own per-kind checks.

Pure core: `validate(proposal, ctx)` touches no DB and no filesystem —
the caller builds a `ValidationContext` (taxonomy from the clone's
CLAUDE.md, the entity index, private file texts) via `context_from_repo()`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

# Folder ↔ note type mapping (mirror of the indexer's).
KNOWLEDGE_TYPES = {"takes": "take", "stories": "story", "lessons": "lesson", "facts": "fact"}
VISIBILITIES = {"public", "agents-only", "private"}
NOTE_STATUSES = {"current", "superseded"}
# Proposals may only touch content surfaces. INDEX.md and CLAUDE.md are
# edited by trusted code (index_lines / taxonomy_additions); identity/,
# lenses/, eval/ and infrastructure are never feed-written.
ALLOWED_PREFIXES = ("knowledge/", "projects/", "content-catalog/", "raw/")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
# Arabic, Arabic Supplement, Arabic Extended-A, presentation forms A+B.
_ARABIC_RE = re.compile(
    "[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}$")
_WS_RE = re.compile(r"\s+")
# Minimum normalized-line length considered "quoting" for rule 8 — short
# strings collide by chance, whole sentences don't.
PRIVATE_QUOTE_MIN_CHARS = 30


@dataclass(frozen=True)
class Violation:
    rule: int
    path: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - repr only
        return f"rule {self.rule} [{self.path}]: {self.message}"


@dataclass
class ValidationResult:
    violations: list[Violation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.violations


@dataclass
class ValidationContext:
    """Everything the pure rules need from the outside world."""

    taxonomy: set[str] = field(default_factory=set)
    # entity_id -> {"path", "visibility", "status"} for the CURRENT repo state.
    entities: dict[str, dict] = field(default_factory=dict)
    # Full text of every `visibility: private` entity (rule 8).
    private_texts: list[str] = field(default_factory=list)


# ---- context builder (the only impure part, kept at the edge) ------------


def taxonomy_from_claude_md(text: str) -> set[str]:
    """Parse §7's backtick-quoted tag list. Anchored to the heading so a
    stray inline-code span elsewhere never widens the vocabulary."""
    m = re.search(r"^##.*Topic taxonomy.*$", text, re.MULTILINE)
    if not m:
        return set()
    section = text[m.end():]
    nxt = re.search(r"^## ", section, re.MULTILINE)
    if nxt:
        section = section[: nxt.start()]
    tags: set[str] = set()
    for span in re.findall(r"`([^`]+)`", section):
        for tok in span.split(","):
            tok = tok.strip()
            if re.fullmatch(r"[a-z0-9][a-z0-9-]*", tok):
                tags.add(tok)
    return tags


def context_from_repo() -> ValidationContext:
    from apps.brain.models import Entity
    from apps.brain.services import gitrepo

    repo = gitrepo.repo_dir()
    claude_md = repo / "CLAUDE.md"
    taxonomy = taxonomy_from_claude_md(
        claude_md.read_text(encoding="utf-8", errors="replace") if claude_md.is_file() else ""
    )
    entities: dict[str, dict] = {}
    private_texts: list[str] = []
    for e in Entity.objects.all():
        entities[e.entity_id] = {"path": e.path, "visibility": e.visibility, "status": e.status}
        if e.visibility == "private":
            p = repo / e.path
            if p.is_file():
                private_texts.append(p.read_text(encoding="utf-8", errors="replace"))
    return ValidationContext(taxonomy=taxonomy, entities=entities, private_texts=private_texts)


# ---- helpers -------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None, text
    return (fm if isinstance(fm, dict) else None), text[m.end():]


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", s).strip().lower()


def _as_list(v: object) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.strip():
        return [s.strip() for s in v.strip("[]").split(",") if s.strip()]
    return []


# ---- the rules -----------------------------------------------------------


def validate(proposal: dict, ctx: ValidationContext) -> ValidationResult:
    res = ValidationResult()
    files = proposal.get("files") or []
    index_lines = proposal.get("index_lines") or []
    supersedes = proposal.get("supersedes") or []
    taxonomy_additions = [str(t) for t in (proposal.get("taxonomy_additions") or [])]
    allowed_topics = ctx.taxonomy | set(taxonomy_additions)
    index_by_entity = {str(l.get("entity_id", "")): str(l.get("line", "")) for l in index_lines}
    proposed_note_ids: set[str] = set()

    if taxonomy_additions:
        res.warnings.append(
            "proposes NEW taxonomy tags (contract §7 — extend deliberately): "
            + ", ".join(taxonomy_additions)
        )

    for f in files:
        path = str(f.get("path", ""))
        action = str(f.get("action", ""))
        content = str(f.get("content", ""))

        # -- rule 5: no deletions (and only content surfaces are writable) --
        if action not in {"create", "update"}:
            res.violations.append(Violation(5, path, f"unknown action {action!r} — only create/update exist"))
            continue
        if ".." in path or path.startswith(("/", "\\")) or ":" in path.split("/")[0]:
            res.violations.append(Violation(5, path, "path escapes the repo"))
            continue
        if not path.startswith(ALLOWED_PREFIXES):
            res.violations.append(
                Violation(5, path, f"proposals may only touch {', '.join(ALLOWED_PREFIXES)}")
            )
            continue
        if not content.strip():
            res.violations.append(Violation(5, path, "empty content — emptying a file is a deletion"))
            continue
        if action == "update" and not any(e["path"] == path for e in ctx.entities.values()) and not path.startswith(("raw/", "content-catalog/")):
            res.violations.append(Violation(5, path, "update targets a file that does not exist"))
        if action == "create" and any(e["path"] == path for e in ctx.entities.values()):
            res.violations.append(Violation(5, path, "create targets a file that already exists — use update"))

        # -- rule 6: no Arabic-script content --
        if _ARABIC_RE.search(content):
            res.violations.append(
                Violation(6, path, "Arabic-script content — the corpus never enters the mind (contract §2)")
            )

        # -- per-kind frontmatter (rules 1-4) --
        if path.startswith("knowledge/"):
            _validate_note(path, content, allowed_topics, res, proposed_note_ids, ctx)
        elif path.startswith("projects/"):
            _validate_project_card(path, content, res)
        # content-catalog/ and raw/ carry no frontmatter schema.

        # -- rule 7: index line exists + agrees (all entity files) --
        if path.startswith(("knowledge/", "projects/")):
            _validate_index_agreement(path, content, index_by_entity, res)

    # -- supersedes are declarative; check both ends --
    for s in supersedes:
        old = str(s.get("old_entity_id", ""))
        new = str(s.get("new_entity_id", ""))
        if old not in ctx.entities:
            res.violations.append(Violation(1, old, "supersedes a note that does not exist"))
        if new not in proposed_note_ids:
            res.violations.append(
                Violation(1, new, "superseded_by points at a note this proposal does not create")
            )

    # -- rule 8: no quoting from private entities --
    all_content = _norm("\n".join(str(f.get("content", "")) for f in files))
    for text in ctx.private_texts:
        for line in text.splitlines():
            n = _norm(line)
            if len(n) >= PRIVATE_QUOTE_MIN_CHARS and n in all_content:
                res.violations.append(
                    Violation(8, "", f"proposal quotes private content: “{line.strip()[:80]}…”")
                )
                break  # one violation per private file is enough signal

    return res


def _validate_note(path, content, allowed_topics, res, proposed_note_ids, ctx):
    parts = path.split("/")
    folder = parts[1] if len(parts) == 3 else ""
    expected_type = KNOWLEDGE_TYPES.get(folder)
    if expected_type is None or not path.endswith(".md"):
        res.violations.append(Violation(1, path, "notes live at knowledge/<takes|stories|lessons|facts>/<id>.md"))
        return
    fm, body = _split_frontmatter(content)
    if fm is None:
        res.violations.append(Violation(1, path, "missing or unparseable YAML frontmatter"))
        return

    stem = parts[-1][:-3]
    note_id = str(fm.get("id") or "")
    proposed_note_ids.add(note_id)
    if note_id != stem:
        res.violations.append(Violation(1, path, f"id {note_id!r} ≠ filename {stem!r}"))
    ntype = str(fm.get("type") or "")
    if ntype != expected_type:
        res.violations.append(Violation(1, path, f"type {ntype!r} ≠ folder type {expected_type!r}"))
    status = str(fm.get("status") or "")
    if status not in NOTE_STATUSES:
        res.violations.append(Violation(1, path, f"status must be current|superseded, got {status!r}"))
    if status == "superseded" and not fm.get("superseded_by"):
        res.violations.append(Violation(1, path, "status superseded requires superseded_by"))
    vis = str(fm.get("visibility") or "")
    if vis not in VISIBILITIES:
        res.violations.append(Violation(1, path, f"visibility must be one of {sorted(VISIBILITIES)}, got {vis!r}"))
    date = str(fm.get("date") or "")
    if not _DATE_RE.match(date):
        res.warnings.append(f"{path}: date should be YYYY-MM, got {date!r}")

    # -- rule 2: provenance is mandatory --
    if not str(fm.get("source") or "").strip():
        res.violations.append(Violation(2, path, "source missing — no note without provenance"))
    if not str(fm.get("source_url") or "").strip():
        res.violations.append(Violation(2, path, "source_url missing — no note without provenance"))

    # -- rule 3: topics ⊆ taxonomy --
    topics = _as_list(fm.get("topics"))
    if not topics:
        res.violations.append(Violation(3, path, "at least one topic tag required"))
    for t in topics:
        if t not in allowed_topics:
            res.violations.append(Violation(3, path, f"topic {t!r} is not in the CLAUDE.md taxonomy"))
    if len(topics) > 4:
        res.warnings.append(f"{path}: {len(topics)} topics (contract says 1-4)")

    # -- rule 4: voice is sacred --
    if ntype in {"take", "story"} and "> VERBATIM:" not in body:
        res.violations.append(Violation(4, path, "takes/stories must carry a `> VERBATIM:` quote"))


def _validate_project_card(path, content, res):
    fm, _body = _split_frontmatter(content)
    if fm is None:
        res.violations.append(Violation(1, path, "project card missing or unparseable YAML frontmatter"))
        return
    if not str(fm.get("id") or "").strip():
        res.violations.append(Violation(1, path, "project card requires an id"))
    lv = str(fm.get("last-verified") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", lv):
        res.violations.append(
            Violation(1, path, "touched project cards must update last-verified (YYYY-MM-DD, compiler rule 7)")
        )


def _validate_index_agreement(path, content, index_by_entity, res):
    """Rule 7 — the line must exist and agree on status + visibility.

    Non-public entities MUST carry an explicit `visibility:` token in
    their line: the silent-omission form is exactly the voice.md mismatch
    the old rule missed (grill B5/B7)."""
    fm, _ = _split_frontmatter(content)
    if fm is None:
        return  # rule 1 already fired
    entity_id = str(fm.get("id") or "")
    line = index_by_entity.get(entity_id, "")
    if not line:
        res.violations.append(Violation(7, path, f"no index line proposed for {entity_id!r}"))
        return
    if not line.rstrip().endswith(path):
        res.violations.append(Violation(7, path, "index line does not end with the entity's path"))

    if path.startswith("knowledge/"):
        status = str(fm.get("status") or "current")
        if f"status: {status}" not in line:
            res.violations.append(
                Violation(7, path, f"index line disagrees with frontmatter status {status!r}")
            )

    vis = str(fm.get("visibility") or "agents-only")
    m = re.search(r"visibility:\s*([a-z-]+)", line)
    line_vis = m.group(1) if m else ""
    if vis != "public" and line_vis != vis:
        res.violations.append(
            Violation(7, path, f"non-public entity must carry `visibility: {vis}` in its index line")
        )
    elif vis == "public" and line_vis not in ("", "public"):
        res.violations.append(
            Violation(7, path, f"index line claims visibility {line_vis!r} but frontmatter says public")
        )


# ---- orchestrator --------------------------------------------------------


def validate_feed(feed) -> ValidationResult:
    """Validate a Feed's stored proposal against the current repo state."""
    if not feed.proposal:
        res = ValidationResult()
        res.warnings.append("no proposal to validate yet")
        return res
    return validate(feed.proposal, context_from_repo())
