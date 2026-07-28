"""Background-jobs hook registry.

apps.core can't import apps.jobs (Contract 1: core has no reverse deps
on feature apps). Same pattern as `apps.core.bearer` / `apps.core.charging`
/ `apps.core.throttling` — feature app calls `register_enqueue(...)`
from its `AppConfig.ready()`, apps.core.ctx dispatches without knowing
the impl.

When apps.jobs isn't installed, `enqueue()` raises a clear error rather
than silently no-opping — async-job endpoints depend on the queue
existing, and silent-no-op would surface as a "job stuck in queued
forever" support ticket.

The duck-typed return value the feature app provides has to expose
`.id`, `.status`, `.status_url` so `Ctx.enqueue()` can hand the
caller a frozen JobHandle. apps.core.ctx.JobHandle
already has the right shape; we just route the data into it.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Protocol


class EnqueueFn(Protocol):
    def __call__(
        self,
        task_path: str,
        *,
        user_id: int | None = None,
        payload: dict[str, Any] | None = None,
        request_id: str = "",
        endpoint_slug: str = "",
        credits_charged: int = 0,
        idempotency_key: str = "",
        async_timeout_seconds: int = 0,
    ) -> object: ...


class ReportProgressFn(Protocol):
    def __call__(
        self,
        tracked_id: str,
        *,
        percent: int,
        message: str | None = None,
        eta_seconds: int | None = None,
    ) -> None: ...


class DeferToWebhookFn(Protocol):
    def __call__(self, tracked_id: str, provider: str, provider_id: str) -> bool: ...


_enqueue: Optional[EnqueueFn] = None
_report_progress: Optional[ReportProgressFn] = None
_defer_to_webhook: Optional[DeferToWebhookFn] = None


def register_enqueue(fn: EnqueueFn) -> None:
    """Install the enqueue impl. Called from `JobsConfig.ready()`."""
    global _enqueue
    _enqueue = fn


def register_report_progress(fn: ReportProgressFn) -> None:
    """Install the report-progress impl. Called from `JobsConfig.ready()`."""
    global _report_progress
    _report_progress = fn


def register_defer_to_webhook(fn: DeferToWebhookFn) -> None:
    """Install the defer-to-webhook impl. Called from `JobsConfig.ready()`."""
    global _defer_to_webhook
    _defer_to_webhook = fn


def is_enabled() -> bool:
    return _enqueue is not None


def enqueue(
    task_path: str,
    *,
    user_id: int | None = None,
    payload: dict[str, Any] | None = None,
    request_id: str = "",
    endpoint_slug: str = "",
    credits_charged: int = 0,
    idempotency_key: str = "",
    async_timeout_seconds: int = 0,
) -> object:
    """Hand a task to the registered backend. Raises when no backend
    is registered — a missing queue is a deployment problem, not
    something to swallow.

    `credits_charged` + `idempotency_key` propagate
    the original endpoint consume's metadata onto the TrackedTask row
    so the dead-letter refund subscriber can rebuild the refund key.

    `async_timeout_seconds` overrides the cluster's
    default per-task timeout. `0` means "use the cluster default".
    """
    if _enqueue is None:
        raise RuntimeError(
            "Background jobs are not configured. Add 'apps.jobs' to "
            "INSTALLED_APPS or run `make worker` (Phase 6.4)."
        )
    return _enqueue(
        task_path,
        user_id=user_id,
        payload=payload,
        request_id=request_id,
        endpoint_slug=endpoint_slug,
        credits_charged=credits_charged,
        idempotency_key=idempotency_key,
        async_timeout_seconds=async_timeout_seconds,
    )


def report_progress(
    tracked_id: str,
    *,
    percent: int,
    message: str | None = None,
    eta_seconds: int | None = None,
) -> None:
    """Update the TrackedTask progress columns. No-op when apps.jobs
    isn't installed — progress reporting is informational, not load-
    bearing, so a missing backend should not crash a running task.
    """
    if _report_progress is None:
        return
    _report_progress(
        tracked_id,
        percent=percent,
        message=message,
        eta_seconds=eta_seconds,
    )


def defer_to_webhook(tracked_id: str, provider: str, provider_id: str) -> bool:
    """Park the running task in `awaiting_callback` (WEBHOOK_COMPLETION_PLAN.md
    Pattern 4, D7).

    Records the `(provider, provider_id)` mapping and flips status so the
    inbound webhook view can later resolve the provider's callback back to
    this job. Raises when no backend is registered — a Pattern 4 endpoint
    that can't defer would otherwise leave the caller's job neither blocking
    nor awaiting (silently broken), so we fail loud like `enqueue`.
    """
    if _defer_to_webhook is None:
        raise RuntimeError(
            "Background jobs are not configured. Add 'apps.jobs' to "
            "INSTALLED_APPS or run `make worker` (Phase 6.4)."
        )
    return _defer_to_webhook(tracked_id, provider, provider_id)


__all__ = [
    "defer_to_webhook",
    "enqueue",
    "is_enabled",
    "register_defer_to_webhook",
    "register_enqueue",
    "register_report_progress",
    "report_progress",
]
