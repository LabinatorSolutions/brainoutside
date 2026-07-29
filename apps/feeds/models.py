"""Feed proposals — the write door's queue (PLAN.md §3 `apps/feeds`).

A Feed is a captured source waiting for the gated write path: proposed
(M2.1, here) → extracted into `proposal` by the feeder agent (M2.2) →
validated (M2.3) → approved/edited/rejected by Hasan in the ops UI
(M2.4) → committed + pushed by the approval handler (M2.5). Creating or
deciding a Feed row NEVER touches the brain repo; only the approval
handler writes there.

PRIMARY data (not rebuildable from the repo) — covered by the DB backup
policy (PLAN.md §10).
"""
from __future__ import annotations

from django.db import models


class Feed(models.Model):
    CHANNELS = [
        ("ui", "ui"),
        ("api", "api"),
        ("mcp", "mcp"),
    ]

    # pending covers the whole pre-decision life (including extraction —
    # the M2.2 worker fills `proposal` while status stays pending).
    STATUSES = [
        ("pending", "pending"),
        ("approved", "approved"),
        ("edited", "edited"),  # approved with human edits
        ("rejected", "rejected"),
        ("failed", "failed"),  # extraction or commit/push failed terminally
    ]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # The mind's source identifier — becomes the `feed: <source-id>` commit
    # subject on approval. Not unique: the same source may be legitimately
    # re-fed (dedupe is conservative, per contract §5.4).
    source_id = models.CharField(max_length=120, db_index=True)
    channel = models.CharField(max_length=8, choices=CHANNELS)
    # Which API key proposed it (NULL for the ops UI form). SET_NULL: the
    # capture outlives key rotation.
    consumer = models.ForeignKey(
        "api_keys.APIKey", null=True, blank=True, on_delete=models.SET_NULL, related_name="feeds"
    )
    # The captured source: source_kind/title/source_url/content/notes +
    # `fetch` metadata when the URL was fetched here in trusted code
    # (grill C12 — the feeder agent gets no network).
    raw_payload = models.JSONField(default=dict)
    # The feeder agent's schema-valid extraction (M2.2). NULL until then.
    proposal = models.JSONField(null=True, blank=True)
    # True once Hasan has edited the proposal — approval then lands as
    # status "edited" instead of "approved" (M2.4).
    proposal_edited = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=STATUSES, default="pending", db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    # Human note on the decision: the reject reason, or context on approve.
    decision_note = models.TextField(blank=True, default="")
    # Commit that landed this feed in the brain repo (M2.5).
    commit_hash = models.CharField(max_length=64, blank=True, default="")
    # Locked-sequence push retries consumed (M2.5, grill C20).
    retries = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True, default="")
    # Token-ledger row for the extraction run (M2.2).
    sdk_operation = models.ForeignKey(
        "events.SdkOperation", null=True, blank=True, on_delete=models.SET_NULL, related_name="feeds"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self) -> str:  # pragma: no cover - repr only
        return f"feed:{self.source_id} [{self.status}] via {self.channel}"

    @property
    def payload_bytes(self) -> int:
        return len((self.raw_payload.get("content") or "").encode("utf-8"))
