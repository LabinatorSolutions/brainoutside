"""Repair actions — the buttons that fix what the health panel reports.

Design rule for every one of these (SETUP-DESIGN.md build order #4):
**surface the real error, never a swallowed one.** Everything that can
fail here fails outside our process — git talking to GitHub, a clone that
turns out not to be a brain, a worker that isn't running. Replacing those
messages with "something went wrong" would leave an operator with no move
except to read our source, which is exactly the situation the setup
rebuild exists to remove. So each job returns, or raises with, the
underlying text.

The slow ones run on the worker rather than in the request: a clone
against a cold remote can take a minute, and a request that times out
mid-clone would leave the UI unable to say whether it worked.
"""
from __future__ import annotations

import logging

from apps.brainconfig.jobs import JobSpec, run

log = logging.getLogger(__name__)

VERIFY_READ = JobSpec(
    name="verify_read",
    label="Verify read access",
    task="apps.brainconfig.maintenance.job_verify_read",
)
PULL_NOW = JobSpec(
    name="pull_now",
    label="Pull from GitHub",
    task="apps.brainconfig.maintenance.job_pull_now",
)
REBUILD_INDEX = JobSpec(
    name="rebuild_index",
    label="Rebuild index and snapshots",
    task="apps.brainconfig.maintenance.job_rebuild_index",
)
REPLACE_CLONE = JobSpec(
    name="replace_clone",
    label="Replace the clone",
    task="apps.brainconfig.maintenance.job_replace_clone",
    danger=(
        "This deletes the server's copy of your brain and clones it again "
        "from the configured URL. Anything committed here but never pushed "
        "is lost. It will refuse if that is the case."
    ),
)
SETUP_BUILD = JobSpec(
    name="setup_build",
    label="Build the brain",
    task="apps.brainconfig.setup_services.run_build",
)
RECONCILE_APPROVALS = JobSpec(
    name="reconcile_approvals",
    label="Recover stuck approvals",
    task="apps.feeds.scheduled.run_reconcile_approvals",
)

#: Everything the Tasks page knows how to display.
ALL_JOBS = (
    SETUP_BUILD,
    VERIFY_READ,
    PULL_NOW,
    REBUILD_INDEX,
    REPLACE_CLONE,
    RECONCILE_APPROVALS,
)

#: What the health page is allowed to start, by name.
RUNNABLE = {j.name: j for j in (VERIFY_READ, PULL_NOW, REBUILD_INDEX, REPLACE_CLONE)}


# ---- job bodies ----------------------------------------------------------


def job_verify_read() -> None:
    def body(progress):
        from apps.brain.services import gitrepo
        from apps.brainconfig import setup_services, setup_state

        url = gitrepo.configured_url()
        progress(0, 1, f"Cloning {url} into a scratch directory")
        outcome = setup_services.verify_read_access(url)
        if outcome.ok:
            setup_state.mark_read_verified()
            return f"Read access confirmed at {outcome.head[:12]}."
        detail = outcome.git_error or ", ".join(outcome.missing_contract)
        raise RuntimeError(f"{outcome.message} {detail}".strip())

    run(VERIFY_READ.name, VERIFY_READ.label, body)


def job_pull_now() -> None:
    def body(progress):
        from apps.brain.services import sync

        progress(0, 1, "Fetching from origin")
        result = sync.sync(trigger="manual")
        return (
            f"{result.commit_sha[:12]} · +{result.added} ~{result.changed} "
            f"-{result.removed}"
        )

    run(PULL_NOW.name, PULL_NOW.label, body)


def job_rebuild_index() -> None:
    def body(progress):
        from apps.brain.services import indexer, snapshots

        progress(0, 2, "Reading frontmatter")
        result = indexer.rebuild(trigger="manual")
        progress(1, 2, "Materialising snapshots")
        snapshots.build_all()
        return f"+{result.added} ~{result.changed} -{result.removed} at {result.commit_sha[:12]}"

    run(REBUILD_INDEX.name, REBUILD_INDEX.label, body)


def job_replace_clone() -> None:
    def body(progress):
        from apps.brain.services import gitrepo, indexer, snapshots

        progress(0, 2, "Re-cloning from the configured URL")
        result = gitrepo.replace_clone()
        progress(1, 2, "Re-indexing")
        indexer.rebuild(trigger="replace-clone")
        snapshots.build_all()
        return f"Re-cloned at {result['head'][:12]}."

    run(REPLACE_CLONE.name, REPLACE_CLONE.label, body)
