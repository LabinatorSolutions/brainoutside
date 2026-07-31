"""The work behind the wizard's buttons.

Two things here earn their keep:

- `normalise_repo_input` accepts what people actually paste (`me/brain`,
  a browser URL, an ssh remote) and, for GitHub, settles on the **ssh**
  form. That is not cosmetic: the app hands out an ssh deploy key, and an
  https remote would ignore it and fail on a private repo with an
  unhelpful credential prompt. The resolved URL is shown back to the user
  rather than silently swapped.

- `verify_read_access` does a real `git clone` into a throwaway
  directory and returns git's own stderr on failure. "Something went
  wrong" is useless here — the whole failure surface is *other people's*
  systems (key not installed yet, wrong repo name, repo is private and
  the key is on a different repo), and only git knows which.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

#: `owner/name`, the shorthand GitHub itself shows.
_SHORTHAND_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_GITHUB_HTTPS_RE = re.compile(
    r"^https?://(?:[^@/]+@)?github\.com/(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


class SetupError(RuntimeError):
    """A setup action failed in a way worth showing the operator verbatim."""


def normalise_repo_input(raw: str) -> str:
    """Turn pasted text into the git URL this server should use.

    Raises `SetupError` with a usable message rather than storing
    something that will fail later in a less obvious place.
    """
    value = (raw or "").strip()
    if not value:
        raise SetupError("Enter your repository, for example `your-name/brain`.")
    # A pasted browser URL often carries a branch path or a trailing bit.
    value = re.sub(r"/(?:tree|blob)/[^\s]*$", "", value)

    if _SHORTHAND_RE.match(value):
        owner, name = value.split("/")
        return f"git@github.com:{owner}/{name}.git"

    m = _GITHUB_HTTPS_RE.match(value)
    if m:
        # https -> ssh on purpose: the deploy key we issue is an ssh key.
        return f"git@github.com:{m.group('owner')}/{m.group('name')}.git"

    # `file://` is here for mirrors, air-gapped installs, and the tests that
    # exercise this whole path without a GitHub account.
    if value.startswith(("git@", "ssh://", "https://", "http://", "git://", "file://")):
        return value

    raise SetupError(
        f"{value!r} doesn't look like a repository. Use `owner/name`, or "
        "paste the full URL from your repository's page."
    )


def repo_web_url(git_url: str) -> str:
    """Best-effort https page for a git remote, for deep links. "" if unknown."""
    m = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", git_url or "")
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", git_url or "")
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    return ""


@dataclass
class VerifyResult:
    ok: bool
    message: str = ""
    git_error: str = ""
    head: str = ""
    missing_contract: list[str] = field(default_factory=list)


def verify_read_access(url: str) -> VerifyResult:
    """Clone the repo into a temp dir with the read credential. Real errors.

    Shallow, and thrown away immediately — this must be safe to press
    repeatedly while someone is still fiddling with GitHub's deploy-key
    page, so it never touches the live clone directory.
    """
    from apps.brain.services import gitrepo

    if not url:
        return VerifyResult(ok=False, message="No repository is configured yet.")

    tmp = Path(tempfile.mkdtemp(prefix="brain-verify-"))
    target = tmp / "clone"
    try:
        try:
            gitrepo._git("clone", "--depth", "1", "--single-branch", url, str(target), timeout=180)
        except gitrepo.BrainRepoError as exc:
            return VerifyResult(
                ok=False,
                message="The server could not read that repository.",
                git_error=str(exc),
            )
        missing = [p for p in gitrepo.CONTRACT_PATHS if not (target / p).exists()]
        head = ""
        try:
            head = gitrepo._git("rev-parse", "HEAD", cwd=target)
        except gitrepo.BrainRepoError:  # pragma: no cover - defensive
            pass
        if missing:
            return VerifyResult(
                ok=False,
                message=(
                    "The server can reach that repository, but it isn't a brain "
                    "yet — these files are missing."
                ),
                missing_contract=missing,
                head=head,
            )
        return VerifyResult(ok=True, message="The server can read your brain.", head=head)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- the Build step ------------------------------------------------------


def run_build() -> None:
    """Clone (or reuse), index, and build snapshots, publishing progress.

    Runs on the Q2 worker. Every exit path writes a terminal state to the
    progress record — a Build that dies silently would leave the wizard
    spinning forever, which is worse than a visible failure.
    """
    from apps.brain.models import Entity
    from apps.brain.services import gitrepo, indexer, snapshots
    from apps.brainconfig import setup_state as state
    from apps.events.models import emit

    def step(n, label):
        state.set_progress(state="running", step=n, total=3, label=label, error="")
        log.info("setup: build step %s/3 — %s", n, label)

    try:
        step(1, "Cloning your brain")
        result = gitrepo.bootstrap()

        step(2, "Reading and indexing your notes")
        run = indexer.rebuild(trigger="setup")

        step(3, "Building the per-tier snapshots")
        snapshots.build_all()

        state.set_progress(
            state="done",
            step=3,
            label=f"{Entity.objects.count()} entities indexed",
            head=result.get("head", "")[:12],
            entities=Entity.objects.count(),
            added=run.added,
            error="",
        )
        emit("settings_change", key="setup.build", entities=Entity.objects.count())
        log.info("setup: build finished — %s entities", Entity.objects.count())
    except Exception as exc:
        log.exception("setup: build failed")
        state.set_progress(
            state="failed",
            label="Build failed",
            error=f"{exc.__class__.__name__}: {exc}",
        )
