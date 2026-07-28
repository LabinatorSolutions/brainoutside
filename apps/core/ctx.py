"""Per-call execution context.

Every `Endpoint.run(inp, ctx)` receives a `Ctx` instance built fresh per
request. It carries everything the endpoint might need that isn't part of
the typed input: identity, request id, transport source, current time,
a handle to the background job queue, and a `.trace` proxy for logging
handled-but-noteworthy errors to the ErrorLog table.

Identity (`user`, `credential`) is resolved from the authenticated
principal — magic-link sessions, API keys, or OAuth tokens — before
`run()` is called.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, Optional

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

Source = Literal["rest", "mcp", "test", "playground"]


class _TraceProxy:
    """Namespace exposed via `ctx.trace.*`.

    Lives on the parent `Ctx` so the call site stays readable:

        ctx.trace.exception(exc, message="couldn't reach foo, falling back")

    Records a handled `ErrorLog` row (`handled=True`, `status_code=200`).
    The endpoint kept running and returned a real response — this is for
    operator visibility into degraded paths, not for marking the request
    as failed.
    """

    __slots__ = ("_ctx",)

    def __init__(self, ctx: "Ctx") -> None:
        self._ctx = ctx

    def exception(
        self,
        exc: BaseException,
        *,
        endpoint_slug: str = "",
        message: str | None = None,
    ) -> None:
        """Persist `exc` as a handled ErrorLog row.

        `message` is appended to `exc.args[0]` so the recorded
        `exc_message` reflects what the endpoint chose to surface.
        """
        from apps.core import error_hook

        if message:
            try:
                exc.args = ((str(exc.args[0]) if exc.args else "") + " | " + message,) + exc.args[1:]
            except Exception:
                pass

        user_id = (
            self._ctx.user.pk
            if (self._ctx.user is not None and self._ctx.user.is_authenticated)
            else None
        )
        try:
            error_hook.record_error(
                exc=exc,
                request_id=self._ctx.request_id,
                source=self._ctx.source,
                endpoint_slug=endpoint_slug or self._ctx.meta.get("endpoint_slug", ""),
                status_code=200,
                user_id=user_id,
                handled=True,
            )
        except Exception:
            # Tracing must never break the request path.
            pass


@dataclass(frozen=True, slots=True)
class JobHandle:
    """Returned by `Ctx.enqueue()`. Phase 6.4 promotes this to a Q2-backed handle.

    Phase P5 adds `progress_percent` / `progress_message` /
    `eta_seconds`. These are populated from the underlying `TrackedTask`
    row whenever a snapshot is read; on first enqueue they default to
    0 / "" / None so the freshly-created handle still constructs
    cleanly. Workers move them forward via `ctx.report_progress(...)`.
    """

    id: str
    status: Literal["queued", "running", "completed", "failed", "dead"] = "queued"
    progress_percent: int = 0
    progress_message: str = ""
    eta_seconds: int | None = None


@dataclass(slots=True)
class Ctx:
    """Per-call execution context handed to `Endpoint.run`."""

    request_id: str
    source: Source
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    user: Optional["AbstractBaseUser"] = None
    credential: Any = None  # APIKey | OAuthToken | None — typed in Phase 3.3 / 4.3
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def trace(self) -> "_TraceProxy":
        """`ctx.trace.exception(exc, message="...")` writes a
        handled ErrorLog row. For operator visibility into degraded paths
        the endpoint chose to recover from."""
        return _TraceProxy(self)

    def report_progress(
        self,
        percent: int,
        *,
        message: str | None = None,
        eta_seconds: int | None = None,
    ) -> None:
        """Update progress on the currently-running Q2 task.

        Callable from inside a Q2 task body — the worker entry point
        (`apps.jobs.services.enqueue.run_tracked`) builds a Ctx whose
        `meta["tracked_id"]` points at the active row, and this helper
        writes `percent` / `message` / `eta_seconds` onto that row so
        clients polling `/api/v1/_jobs/<id>` see live updates.

        `percent` is clamped to 0..100 at write time. `message` is
        truncated to 200 chars. When called outside a task body
        (`meta["tracked_id"]` missing) the call is a silent no-op so
        endpoints that share a code path with workers don't crash.
        """
        tracked_id = str(self.meta.get("tracked_id") or "")
        if not tracked_id:
            return
        from apps.core import jobs_hook

        jobs_hook.report_progress(
            tracked_id,
            percent=percent,
            message=message,
            eta_seconds=eta_seconds,
        )

    def defer_to_webhook(self, provider: str, provider_id: str) -> None:
        """Park THIS task in `awaiting_callback`, freeing the Q2 slot
        (WEBHOOK_COMPLETION_PLAN.md Pattern 4).

        Call from inside a Q2 kickoff task body, AFTER submitting work to a
        webhook-capable provider and obtaining its `provider_id` — the order
        is load-bearing (D7): the `(provider, provider_id)` mapping must be
        recorded before the provider's callback can arrive, so the inbound
        view can resolve it. The task then returns ~immediately; the
        provider's later callback (or the reaper) completes the job.

        `meta["tracked_id"]` identifies the row, the same way
        `report_progress` finds it. Raises outside a task body (no
        `tracked_id`) — deferring a non-tracked call can't work and would
        silently strand the caller, so it's a programming error to surface
        at dev time. Both `provider` and `provider_id` must be non-empty.
        """
        provider = (provider or "").strip()
        provider_id = (provider_id or "").strip()
        if not provider or not provider_id:
            raise ValueError(
                "defer_to_webhook(provider, provider_id) requires both to be "
                f"non-empty (got provider={provider!r}, provider_id={provider_id!r})."
            )
        tracked_id = str(self.meta.get("tracked_id") or "")
        if not tracked_id:
            raise RuntimeError(
                "ctx.defer_to_webhook() is only valid inside a Q2 task body "
                "(no meta['tracked_id']). Call it from the kickoff task after "
                "submitting to the provider, not from a sync endpoint run()."
            )
        from apps.core import jobs_hook

        jobs_hook.defer_to_webhook(tracked_id, provider, provider_id)

    def enqueue(self, task: str, /, **kwargs: Any) -> JobHandle:
        """Hand a task to the background-jobs queue.

        `task` is the dotted path of a callable; `kwargs` are passed
        as the task function's keyword arguments. Returns a frozen
        `JobHandle` carrying the tracked task id + initial status.
        Callers serialize the handle into their endpoint Output so
        the user can poll `/api/v1/_jobs/<id>`.

        SYNC ONLY — call this from sync contexts (webhook handlers,
        management commands, sync endpoints). Async `@endpoint.run()`
        bodies must `await ctx.aenqueue(...)` instead, since the
        underlying queue write hits the DB.

        Phase 5.2.6 will wire credit charging on enqueue (charge here,
        refund-on-terminal-failure via the dead-letter task) — for
        Phase 6.4 we only deliver the queue surface.
        """
        return self._do_enqueue(task, kwargs)

    async def aenqueue(self, task: str, /, **kwargs: Any) -> JobHandle:
        """Async wrapper around `enqueue()` for use inside async views.

        The hook chain ultimately writes to the DB synchronously, so we
        wrap the call in `sync_to_async(thread_sensitive=True)` to keep
        the request loop non-blocking.
        """
        from asgiref.sync import sync_to_async

        return await sync_to_async(self._do_enqueue, thread_sensitive=True)(
            task, kwargs
        )

    def _do_enqueue(self, task: str, kwargs: dict[str, Any]) -> JobHandle:
        # Late import — keeps this module free of an import-time dep
        # on the registry, and lets tests register fakes by patching
        # apps.core.jobs_hook directly.
        from apps.core.jobs_hook import enqueue as _enqueue

        user_id = self.user.pk if (self.user is not None and self.user.is_authenticated) else None
        # the REST view stashes the original consume's
        # idempotency key + credits magnitude on `ctx.meta` when it
        # charges before calling `run()`. Forward to the jobs backend so
        # the TrackedTask carries the refund correlation; the dead-letter
        # subscriber uses these to issue a compensating credit. Anonymous
        # endpoints / free endpoints leave the meta keys absent → defaults
        # of 0 / "" mean "nothing to refund".
        consume_key = str(self.meta.get("consume_idempotency_key", "") or "")
        consume_credits = int(self.meta.get("consume_credits", 0) or 0)
        # per-endpoint override of the Q2 task timeout
        # (in seconds). The REST view stashes spec.async_timeout_seconds
        # on ctx.meta when the spec set a non-zero value; we forward it
        # so the enqueue impl can pass it to async_task. 0 = use cluster
        # default (currently 60s).
        async_timeout = int(self.meta.get("async_timeout_seconds", 0) or 0)
        raw = _enqueue(
            task,
            user_id=user_id,
            payload=kwargs or None,
            request_id=self.request_id,
            endpoint_slug=self.meta.get("endpoint_slug", ""),
            credits_charged=consume_credits,
            idempotency_key=consume_key,
            async_timeout_seconds=async_timeout,
        )
        # Duck-typed pass-through — apps.jobs returns a JobHandle dataclass
        # whose shape happens to match the apps.core.ctx one. We rebuild
        # ours so the frozen-slots invariant holds across the boundary.
        eta_raw = getattr(raw, "eta_seconds", None)
        return JobHandle(
            id=str(getattr(raw, "id", "")),
            status=str(getattr(raw, "status", "queued")),  # type: ignore[arg-type]
            progress_percent=int(getattr(raw, "progress_percent", 0) or 0),
            progress_message=str(getattr(raw, "progress_message", "") or ""),
            eta_seconds=int(eta_raw) if eta_raw is not None else None,
        )


def build_ctx(
    *,
    request_id: str,
    source: Source,
    user: Optional["AbstractBaseUser"] = None,
    credential: Any = None,
) -> Ctx:
    """Factory used by `EndpointView` (REST) and the MCP bridge.

    Kept as a factory rather than calling `Ctx(...)` directly so later phases
    can inject defaults (locale, request-scoped DB hints) without churning
    every call site.
    """
    return Ctx(request_id=request_id, source=source, user=user, credential=credential)
