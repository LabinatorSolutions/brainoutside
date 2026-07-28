"""Snapshot file access — the only door the serving layers read through.

Everything serves from `/data/brain-views/<tier>/` (PLAN.md §5): if a
file isn't in the caller's tier snapshot, it does not exist for that
caller. That single rule carries the whole visibility model — including
raw/ link-inheritance and the stripped public spans — because the
snapshot builder already enforced it at build time.
"""
from __future__ import annotations

from pathlib import Path

from apps.brain.services import snapshots


class SnapshotMiss(ValueError):
    """Requested path absent from the caller's tier snapshot."""


def read(tier: str, relpath: str) -> str:
    base = snapshots.tier_dir(tier).resolve()
    target = (base / relpath).resolve()
    # Traversal guard: the resolved target must stay inside the snapshot.
    if base not in target.parents and target != base:
        raise SnapshotMiss(f"unknown path: {relpath}")
    if not target.is_file():
        raise SnapshotMiss(f"unknown path: {relpath}")
    return target.read_text(encoding="utf-8", errors="replace")


def exists(tier: str, relpath: str) -> bool:
    try:
        read(tier, relpath)
        return True
    except SnapshotMiss:
        return False
