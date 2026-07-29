"""Approval handler — the ONLY code that writes the brain repo (M2.5).

Runs on the Q2 worker (the write PAT exists only there — grill C13).
One approval = the locked sequence from PLAN.md §4:

    lock → sync to origin → apply proposal → validate → commit
    `feed: <source-id>` → push → (unlock) → reindex + rebuild snapshots

Push rejection is a ROUTINE race with a local push, not a failure: the
whole locked sequence retries up to MAX_PUSH_ATTEMPTS — the proposal is
declarative (full file contents + index/supersede ops), so replay after
a fresh sync is safe (grill C20). Any terminal failure rolls the tree
back to origin and marks the Feed failed; the working tree is never left
dirty.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from django.conf import settings as dj_settings
from django.utils import timezone

from apps.brain.services import gitrepo
from apps.events.models import emit
from apps.feeds.models import Feed
from apps.feeds.services import validator

log = logging.getLogger(__name__)

MAX_PUSH_ATTEMPTS = 3
COMMIT_NAME = "brain-app"
COMMIT_EMAIL = "brain@learnwithhasan.com"

# INDEX.md section per path prefix, for appending brand-new entity lines.
_INDEX_SECTIONS = (
    ("knowledge/", "## Knowledge"),
    ("projects/", "## Projects"),
    ("content-catalog/", "## Content"),
)


class ApplyFailure(RuntimeError):
    """Terminal apply problem — rolls back and marks the Feed failed."""


# ---- write credential (worker-only, grill C13) ---------------------------


def _write_pat() -> str:
    pat = dj_settings.BRAIN_GIT_WRITE_PAT.get_secret_value().strip()
    if pat:
        return pat
    path = dj_settings.BRAIN_GIT_WRITE_PAT_PATH
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip()
    return ""


def _push_target(branch: str) -> list[str]:
    """`git push` argv tail. With a PAT: a one-shot tokenized https URL
    (never written to git config). Without: plain origin (dev)."""
    pat = _write_pat()
    if not pat:
        return ["origin", f"HEAD:{branch}"]
    origin = gitrepo.run("remote", "get-url", "origin")
    m = re.match(r"^git@([^:]+):(.+?)(\.git)?$", origin) or re.match(
        r"^(?:ssh|https?)://(?:[^@/]+@)?([^/]+)/(.+?)(\.git)?$", origin
    )
    if not m:
        raise ApplyFailure(f"cannot derive an https push URL from origin {origin!r}")
    host, repo_path = m.group(1), m.group(2)
    return [f"https://x-access-token:{pat}@{host}/{repo_path}.git", f"HEAD:{branch}"]


# ---- proposal application (plain file edits, no agent) -------------------


def _safe_target(repo: Path, relpath: str) -> Path:
    target = (repo / relpath).resolve()
    if not str(target).startswith(str(repo.resolve())):
        raise ApplyFailure(f"path escapes the repo: {relpath}")
    return target


def _apply_files(repo: Path, proposal: dict) -> None:
    for f in proposal.get("files") or []:
        target = _safe_target(repo, str(f.get("path", "")))
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(f.get("content", ""))
        if not content.endswith("\n"):
            content += "\n"
        target.write_text(content, encoding="utf-8", newline="\n")


def _entity_paths(proposal: dict) -> dict[str, str]:
    """entity_id -> path for the files this proposal carries."""
    out: dict[str, str] = {}
    for f in proposal.get("files") or []:
        m = re.search(r"^id:\s*(\S+)", str(f.get("content", "")), re.MULTILINE)
        if m:
            out[m.group(1)] = str(f.get("path", ""))
    return out


def _apply_index_lines(repo: Path, proposal: dict) -> None:
    index = repo / "INDEX.md"
    lines = index.read_text(encoding="utf-8").splitlines()
    for entry in proposal.get("index_lines") or []:
        new_line = str(entry.get("line", "")).rstrip()
        path = new_line.rsplit("|", 1)[-1].strip()
        replaced = False
        for i, line in enumerate(lines):
            if line.startswith("- ") and line.rstrip().endswith(path):
                lines[i] = new_line
                replaced = True
                break
        if not replaced:
            section = next((s for pfx, s in _INDEX_SECTIONS if path.startswith(pfx)), None)
            at = len(lines)
            if section is not None:
                try:
                    start = next(i for i, l in enumerate(lines) if l.startswith(section))
                    at = next(
                        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
                        len(lines),
                    )
                    while at > start + 1 and not lines[at - 1].strip():
                        at -= 1
                except StopIteration:
                    at = len(lines)
            lines.insert(at, new_line)
    index.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _apply_supersedes(repo: Path, proposal: dict, entities: dict[str, dict]) -> None:
    """Trusted-code frontmatter edit: old note → superseded (+ INDEX token)."""
    index = repo / "INDEX.md"
    index_text = index.read_text(encoding="utf-8")
    for s in proposal.get("supersedes") or []:
        old_id = str(s.get("old_entity_id", ""))
        new_id = str(s.get("new_entity_id", ""))
        info = entities.get(old_id)
        if info is None:
            raise ApplyFailure(f"supersede target {old_id!r} not in the entity index")
        target = _safe_target(repo, info["path"])
        text = target.read_text(encoding="utf-8")
        m = validator._FRONTMATTER_RE.match(text)
        if not m:
            raise ApplyFailure(f"{info['path']}: no frontmatter to mark superseded")
        fm = m.group(0)
        fm = re.sub(r"^status:.*$", "status: superseded", fm, count=1, flags=re.MULTILINE)
        if re.search(r"^superseded_by:", fm, flags=re.MULTILINE):
            fm = re.sub(r"^superseded_by:.*$", f"superseded_by: {new_id}", fm, count=1, flags=re.MULTILINE)
        else:
            fm = fm.replace("\n---", f"\nsuperseded_by: {new_id}\n---", 1)
        target.write_text(fm + text[m.end():], encoding="utf-8", newline="\n")
        # Keep the old note's INDEX line truthful too.
        index_text = "\n".join(
            line.replace("status: current", "status: superseded")
            if line.startswith("- ") and line.rstrip().endswith(info["path"])
            else line
            for line in index_text.splitlines()
        ) + "\n"
    index.write_text(index_text, encoding="utf-8", newline="\n")


def _apply_taxonomy(repo: Path, proposal: dict) -> None:
    """Contract §7: extending the taxonomy edits CLAUDE.md in the same commit."""
    additions = [str(t) for t in (proposal.get("taxonomy_additions") or [])]
    if not additions:
        return
    claude = repo / "CLAUDE.md"
    text = claude.read_text(encoding="utf-8")
    m = re.search(r"^##.*Topic taxonomy.*$", text, re.MULTILINE)
    if not m:
        raise ApplyFailure("CLAUDE.md has no Topic taxonomy section to extend")
    section_end = re.search(r"^## ", text[m.end():], re.MULTILINE)
    end = m.end() + (section_end.start() if section_end else len(text) - m.end())
    spans = list(re.finditer(r"`([^`]+)`", text[m.end():end]))
    if not spans:
        raise ApplyFailure("CLAUDE.md taxonomy list (backtick span) not found")
    last = spans[-1]
    existing = {t.strip() for t in last.group(1).split(",")}
    new_tags = [t for t in additions if t not in existing]
    if not new_tags:
        return
    abs_end = m.end() + last.end(1)
    text = text[:abs_end] + ", " + ", ".join(new_tags) + text[abs_end:]
    claude.write_text(text, encoding="utf-8", newline="\n")


# ---- the locked sequence -------------------------------------------------


def _rollback(branch: str) -> None:
    """Return the clone to pristine origin state. Never raises."""
    for args in (("rebase", "--abort"), ("reset", "--hard", f"origin/{branch}"), ("clean", "-fd")):
        try:
            gitrepo.run(*args)
        except gitrepo.BrainRepoError:
            pass


def _push(branch: str) -> None:
    gitrepo.run("push", *_push_target(branch), timeout=120)


def apply_feed(feed_id: int) -> str:
    """Q2 task entry: perform the approval for a claimed (`approving`) Feed."""
    feed = Feed.objects.filter(pk=feed_id).first()
    if feed is None:
        return f"feed {feed_id} gone"
    if feed.status != "approving":
        return f"feed {feed_id} is {feed.status} — not applying"
    if not feed.proposal:
        _finalize_failed(feed, "no proposal to apply")
        return "no proposal"

    proposal = feed.proposal
    attempts = 0
    try:
        with gitrepo.repo_lock():
            branch = gitrepo.run("rev-parse", "--abbrev-ref", "HEAD")
            repo = gitrepo.repo_dir()
            while True:
                attempts += 1
                # Start every attempt from exact origin state — replay-safe.
                gitrepo.run("fetch", "origin", timeout=120)
                gitrepo.run("reset", "--hard", f"origin/{branch}")
                gitrepo.run("clean", "-fd")
                try:
                    ctx = validator.context_from_repo()
                    _apply_files(repo, proposal)
                    _apply_index_lines(repo, proposal)
                    _apply_supersedes(repo, proposal, ctx.entities)
                    _apply_taxonomy(repo, proposal)
                    res = validator.validate(proposal, ctx)
                    if not res.valid:
                        raise ApplyFailure(
                            "pre-commit validation failed: "
                            + "; ".join(str(v) for v in res.violations[:5])
                        )
                    gitrepo.run("add", "-A")
                    gitrepo.run(
                        "-c", f"user.name={COMMIT_NAME}",
                        "-c", f"user.email={COMMIT_EMAIL}",
                        "commit",
                        "-m", f"feed: {feed.source_id}",
                        "-m", f"Feed-Id: {feed.pk}\nChannel: {feed.channel}",
                    )
                    _push(branch)
                    commit = gitrepo.run("rev-parse", "HEAD")
                    break
                except ApplyFailure:
                    _rollback(branch)
                    raise
                except gitrepo.BrainRepoError as exc:
                    # Push/transport trouble — routine race territory.
                    _rollback(branch)
                    if attempts >= MAX_PUSH_ATTEMPTS:
                        raise ApplyFailure(
                            f"push failed after {attempts} attempts: {exc}"
                        ) from exc
                    log.warning("feed %s: attempt %s failed (%s), retrying", feed.pk, attempts, exc)
    except (ApplyFailure, gitrepo.BrainRepoError) as exc:
        _finalize_failed(feed, str(exc), attempts)
        return f"failed: {exc}"

    feed.status = "edited" if feed.proposal_edited else "approved"
    feed.commit_hash = commit
    feed.decided_at = timezone.now()
    feed.retries = attempts - 1
    feed.error = ""
    feed.save(update_fields=["status", "commit_hash", "decided_at", "retries", "error"])
    emit(
        "feed",
        action="approved",
        feed_id=feed.pk,
        source_id=feed.source_id,
        commit=commit,
        retries=attempts - 1,
        edited=feed.proposal_edited,
    )

    # Post-push (lock released): reindex + rebuild snapshots so the new
    # notes serve immediately; the webhook echo will no-op on the SHA.
    try:
        from apps.brain.services import indexer, snapshots

        indexer.rebuild(trigger="feed-approval")
        snapshots.build_all()
    except Exception:
        log.exception("feed %s: post-approval reindex failed (sync beat will repair)", feed.pk)

    return f"approved as {commit[:12]} after {attempts} attempt(s)"


def _finalize_failed(feed: Feed, error: str, attempts: int = 0) -> None:
    feed.status = "failed"
    feed.error = error[:2000]
    feed.decided_at = timezone.now()
    feed.retries = max(0, attempts - 1)
    feed.save(update_fields=["status", "error", "decided_at", "retries"])
    emit("feed", action="apply_failed", feed_id=feed.pk, source_id=feed.source_id, error=error[:500])
