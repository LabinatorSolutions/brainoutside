"""The server's clone of the brain repo — the single source of truth.

Design contract (PLAN.md §4):
- The clone lives on a volume shared by web + mcp + worker containers.
- EVERY mutating git operation runs under `repo_lock()` — a file lock on
  the shared volume, so the single-writer guarantee holds across
  containers, not just threads.
- Bootstrap is idempotent: an empty/invalid directory becomes a fresh
  clone; a valid clone is reused untouched (Coolify volume-rename safety).
- After any clone/pull, `verify_contract()` fails LOUDLY if the operating
  contract (CLAUDE.md, skills, lenses) is missing from the clone — a
  server without the contract must not serve.
- The standing remote credential is the READ-ONLY deploy key
  (`BRAIN_GIT_SSH_KEY_PATH`). The write credential arrives only in the
  M2 approval handler, worker-side.
"""
from __future__ import annotations

import logging
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from django.conf import settings
from filelock import FileLock, Timeout

log = logging.getLogger(__name__)

#: Paths (relative to the clone root) that MUST exist for the server to
#: consider the clone a usable brain. Kept in sync with the brain repo's
#: layout — if the contract grows, extend here deliberately.
CONTRACT_PATHS: tuple[str, ...] = (
    "CLAUDE.md",
    "INDEX.md",
    ".claude/skills/mind-feeder/SKILL.md",
    ".claude/skills/mind-feeder/server-mode.md",
    ".claude/skills/mind-reader/SKILL.md",
    ".claude/skills/mind-reader/server-mode.md",
    "lenses",
    "identity",
    "knowledge",
)

LOCK_TIMEOUT_SECONDS = 120


class BrainRepoError(RuntimeError):
    """Raised when the clone is unusable (missing, invalid, or contractless)."""


def repo_dir() -> Path:
    return Path(settings.BRAIN_REPO_DIR)


def _lock_path() -> Path:
    # Sibling of the clone so it lives on the same shared volume but is
    # never part of the git working tree.
    d = repo_dir().parent
    d.mkdir(parents=True, exist_ok=True)
    return d / "brain-repo.lock"


@contextmanager
def repo_lock(timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Cross-container single-writer lock for ALL mutating git operations."""
    lock = FileLock(str(_lock_path()))
    try:
        with lock.acquire(timeout=timeout):
            yield
    except Timeout as exc:
        raise BrainRepoError(
            f"Could not acquire brain-repo lock within {timeout}s — "
            "another git operation is stuck or long-running."
        ) from exc


def _git_env() -> dict[str, str] | None:
    """Extra env for git subprocesses; wires the deploy key when configured."""
    key_path = settings.BRAIN_GIT_SSH_KEY_PATH
    if not key_path:
        return None
    import os

    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    )
    return env


def _git(*args: str, cwd: Path | None = None, timeout: int = 300) -> str:
    """Run a git command; raise BrainRepoError with stderr on failure."""
    cmd = ["git", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=_git_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise BrainRepoError("git binary not found in this container/image.") from exc
    except subprocess.TimeoutExpired as exc:
        raise BrainRepoError(f"git {' '.join(args)} timed out after {timeout}s.") from exc
    if proc.returncode != 0:
        raise BrainRepoError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


def is_valid_repo() -> bool:
    d = repo_dir()
    if not (d / ".git").is_dir():
        return False
    try:
        _git("rev-parse", "--is-inside-work-tree", cwd=d)
        return True
    except BrainRepoError:
        return False


def head_sha() -> str:
    return _git("rev-parse", "HEAD", cwd=repo_dir())


def verify_contract() -> None:
    """Fail loudly when the clone is missing the operating contract."""
    d = repo_dir()
    missing = [p for p in CONTRACT_PATHS if not (d / p).exists()]
    if missing:
        raise BrainRepoError(
            "Brain clone is missing contract paths — refusing to serve. "
            f"Missing: {', '.join(missing)}. "
            "Did the brain repo push include CLAUDE.md/.claude/lenses?"
        )


def bootstrap() -> dict[str, str]:
    """Idempotent: clone when absent/invalid, reuse when valid. Verified either way."""
    url = settings.BRAIN_REPO_URL
    d = repo_dir()
    with repo_lock():
        if is_valid_repo():
            action = "reused"
        else:
            if not url:
                raise BrainRepoError(
                    "BRAIN_REPO_URL is not set and no valid clone exists at "
                    f"{d} — cannot bootstrap."
                )
            if d.exists() and any(d.iterdir()):
                raise BrainRepoError(
                    f"{d} exists, is non-empty, and is not a valid git repo — "
                    "refusing to clobber it. Inspect and remove it manually."
                )
            d.parent.mkdir(parents=True, exist_ok=True)
            log.info("brain: cloning %s -> %s", url, d)
            _git("clone", "--single-branch", url, str(d))
            action = "cloned"
        verify_contract()
        sha = head_sha()
    log.info("brain: bootstrap %s at %s", action, sha)
    return {"action": action, "head": sha, "dir": str(d)}


def pull_rebase() -> dict[str, str]:
    """Fetch + rebase under the lock. Returns old/new HEAD for sync logging."""
    d = repo_dir()
    with repo_lock():
        if not is_valid_repo():
            raise BrainRepoError(f"No valid clone at {d} — run bootstrap first.")
        old = head_sha()
        _git("pull", "--rebase", "--autostash", cwd=d)
        new = head_sha()
        verify_contract()
    return {"old": old, "new": new, "changed": str(old != new).lower()}


def status_probe() -> dict[str, object]:
    """Cheap health snapshot for /readyz and the dashboard tile."""
    try:
        valid = is_valid_repo()
        info: dict[str, object] = {"valid": valid, "dir": str(repo_dir())}
        if valid:
            info["head"] = head_sha()
            missing = [p for p in CONTRACT_PATHS if not (repo_dir() / p).exists()]
            info["contract_ok"] = not missing
            if missing:
                info["contract_missing"] = missing
        return info
    except BrainRepoError as exc:  # pragma: no cover - defensive
        return {"valid": False, "error": str(exc)}
