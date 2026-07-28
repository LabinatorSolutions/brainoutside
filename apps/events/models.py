"""Structured event log — every dashboard is a query over this table.

Typed rows, not log lines (PLAN.md §3): indexed columns for what the UI
filters by (type, consumer, created_at), JSON `details` for the variable
parts, `entity_ids` for which-notes-were-touched queries.

PRIMARY data: unlike Entity/SyncRun this is not rebuildable from the
repo — it's covered by the DB backup policy (PLAN.md §10).
"""
from __future__ import annotations

from django.db import models


class Event(models.Model):
    TYPES = [
        ("read", "read"),
        ("feed", "feed"),
        ("drift", "drift"),
        ("sync", "sync"),
        ("auth_denied", "auth_denied"),
        ("settings_change", "settings_change"),
        ("degraded", "degraded"),
    ]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    type = models.CharField(max_length=24, choices=TYPES, db_index=True)
    # Nullable: UI/admin actions and system events have no consumer key.
    consumer = models.ForeignKey(
        "api_keys.APIKey", null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    entity_ids = models.JSONField(default=list, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["type", "created_at"])]

    def __str__(self) -> str:  # pragma: no cover - repr only
        return f"{self.type} @ {self.created_at:%Y-%m-%d %H:%M:%S}"


def emit(type: str, *, consumer=None, entity_ids: list[str] | None = None, **details: object) -> Event:
    """One-liner event writer: `emit("drift", entity_ids=[...], reason=...)`."""
    return Event.objects.create(
        type=type, consumer=consumer, entity_ids=entity_ids or [], details=details
    )
