"""What the first-run wizard knows about how far setup has got.

**Step completion is derived from real state, not from a stored cursor.**
A cursor drifts: it says "deploy key installed" after someone rotates the
key, or "built" after the volume is wiped. Deriving means the wizard is
self-healing — delete the clone and the Build step lights up again — and
it means the same functions can drive the dashboard's setup-health panel,
so the two can never disagree about whether setup is finished.

Only two things genuinely cannot be derived, and both are decisions
rather than facts, so both are stored explicitly:

- that the operator *chose to skip* the write credential;
- that a Verify actually succeeded against a specific repo URL. A clone
  can be absent while the credential is perfectly good (nothing has
  cloned yet), so "did the key work" has to be remembered — but it is
  remembered *with the URL it was proved against*, so pointing the app at
  a different repo correctly un-verifies it.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache

from apps.core import runtime_setting_store as store

KEY_WRITE_SKIPPED = "setup.write_skipped"
KEY_READ_VERIFIED_URL = "setup.read_verified_url"

#: Where the Build step publishes progress. The cache, not a table: this
#: is ephemeral by nature, and a lost Redis costs a progress bar, not a
#: setup. TTL outlives the slowest plausible clone-and-index.
PROGRESS_CACHE_KEY = "setup:build:progress"
PROGRESS_TTL_SECONDS = 60 * 30


@dataclass(frozen=True)
class Step:
    slug: str
    title: str
    blurb: str
    optional: bool = False


STEPS: tuple[Step, ...] = (
    Step("account", "Create your account",
         "One operator account. This page is open to anyone until you do."),
    Step("repo", "Create your brain",
         "A normal GitHub repository full of markdown files. You own it."),
    Step("read", "Let the server read it",
         "Install a read-only deploy key so the server can clone and pull."),
    Step("write", "Let the server write back",
         "A token so approved feeds can be pushed to GitHub.", optional=True),
    Step("claude", "Connect Claude",
         "An API key, or a Claude subscription token — no API billing."),
    Step("build", "Build your brain",
         "Clone, index, and materialise the per-tier snapshots."),
)

STEP_BY_SLUG = {s.slug: s for s in STEPS}


# ---- individual step predicates -----------------------------------------


def has_admin() -> bool:
    from django.contrib.auth.models import User

    return User.objects.filter(is_superuser=True).exists()


def repo_configured() -> bool:
    from apps.brain.services import gitrepo

    return bool(gitrepo.configured_url())


def read_verified() -> bool:
    """True when this exact repo URL has been proved readable.

    Either a Verify run said so, or a valid clone of that same repo is
    already sitting on the volume — which is the case for anyone who
    mounted a clone themselves, and they should not be asked to install
    a deploy key to prove something already true.
    """
    from apps.brain.services import gitrepo

    url = gitrepo.configured_url()
    if not url:
        return False
    want = gitrepo.normalize_remote(url)
    if store.get_str(KEY_READ_VERIFIED_URL, "") == want:
        return True
    return gitrepo.is_valid_repo() and gitrepo.origin_probe()["ok"]


def mark_read_verified() -> None:
    from apps.brain.services import gitrepo

    store.set_value(KEY_READ_VERIFIED_URL, gitrepo.normalize_remote(gitrepo.configured_url()))


def clear_read_verified() -> None:
    """Drop the proof of read access — after a repo change or key rotation."""
    store.set_value(KEY_READ_VERIFIED_URL, "")


def write_configured() -> bool:
    from apps.brain.services import gitcreds

    return bool(gitcreds.status()["write"]["configured"])


def write_skipped() -> bool:
    return store.get_str(KEY_WRITE_SKIPPED, "") == "1"


def set_write_skipped(skipped: bool) -> None:
    store.set_value(KEY_WRITE_SKIPPED, "1" if skipped else "")


def claude_configured() -> bool:
    from apps.brainconfig import services as cfg

    return bool(cfg.get("ANTHROPIC_API_KEY"))


def brain_built() -> bool:
    from apps.brain.models import Entity
    from apps.brain.services import gitrepo

    return gitrepo.is_valid_repo() and Entity.objects.exists()


_PREDICATES = {
    "account": has_admin,
    "repo": repo_configured,
    "read": read_verified,
    "write": write_configured,
    "claude": claude_configured,
    "build": brain_built,
}


# ---- aggregate ----------------------------------------------------------


def step_states() -> list[dict]:
    """Every step with `done` / `skipped`, in order."""
    out = []
    for s in STEPS:
        done = _safe(_PREDICATES[s.slug])
        out.append({
            "step": s,
            "slug": s.slug,
            "title": s.title,
            "blurb": s.blurb,
            "optional": s.optional,
            "done": done,
            "skipped": s.slug == "write" and not done and _safe(write_skipped),
        })
    return out


def _safe(fn) -> bool:
    """A predicate that raises (DB down, clone missing) reads as not-done.

    The wizard's job is to be reachable when things are broken, so a
    failing probe must not 500 the page that would fix it.
    """
    try:
        return bool(fn())
    except Exception:
        return False


def first_incomplete(states: list[dict] | None = None) -> str:
    """Where the wizard should send someone next — optional steps included.

    A first-time operator should be *offered* the write credential, so an
    optional step still stops navigation here until it is done or
    explicitly skipped.
    """
    states = states if states is not None else step_states()
    for s in states:
        if not s["done"] and not s["skipped"]:
            return s["slug"]
    return ""


def is_complete(states: list[dict] | None = None) -> bool:
    """Is the server usable — i.e. is every REQUIRED step done?

    Optional steps are excluded on purpose. This drives the middleware,
    so counting them would drag every existing installation that never
    configured a write credential back into the wizard, which is exactly
    the opposite of what "optional" is supposed to mean. A missing
    optional piece belongs on the dashboard's health panel, not in a
    redirect.
    """
    states = states if states is not None else step_states()
    return all(s["done"] for s in states if not s["optional"])


def needs_first_admin() -> bool:
    """No operator account exists — the app is unusable and unowned."""
    return not _safe(has_admin)


# ---- Build-step progress ------------------------------------------------


def set_progress(**fields) -> None:
    """Merge fields into the published progress record."""
    current = cache.get(PROGRESS_CACHE_KEY) or {}
    current.update(fields)
    cache.set(PROGRESS_CACHE_KEY, current, PROGRESS_TTL_SECONDS)


def get_progress() -> dict:
    return cache.get(PROGRESS_CACHE_KEY) or {}


def clear_progress() -> None:
    cache.delete(PROGRESS_CACHE_KEY)


def build_running() -> bool:
    return get_progress().get("state") == "running"
