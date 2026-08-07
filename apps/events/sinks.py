"""Concrete backends for the `apps.core` hook registries.

`apps.core` is the vendored framework half of this app and must not
import a feature app (Contract 1), so it dispatches through registries —
`apps.core.error_hook`, `apps.core.audit_hook` — that some feature app is
expected to fill in at boot. Upstream that app was `apps.observability`.
It was never vendored, nothing ever called `register()`, and so roughly a
dozen call sites across the request pipeline have been silent no-ops for
the life of the project: endpoint 500s recorded nothing, and
`/_csp-report/` accepted browser violation reports and dropped them.

This module is the missing half, pointed at the table this product
already has. `EventsConfig.ready()` registers it.

**Why `Event` and not a new `ErrorLog` model.** The hook's docstrings
describe an `ErrorLog` row with its own admin panel. That model does not
exist here and adding it would mean a second table, a second retention
cron, and a second ops page — for a single-operator server whose event
log is already the thing `/ops/logs/` renders, already filterable by
type, and already pruned by `run_prune_event_log`. The row lands as
`Event(type="error")` and shows up next to reads, syncs and feeds on the
timeline the operator already watches.

**What is deliberately not stored.** No traceback. `log.exception` has
already written one to the application log at every call site, the row
carries the same `request_id`, and a JSONField of tracebacks in a table
that also feeds a polling dashboard is a size problem for no gain. An
exception raised while serving a note can also quote note content, and
this table is backed up (PLAN.md §10) — the message is truncated for
that reason too, not only for size.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Truncation limits. `details` is JSON in Postgres, so nothing here is a
# hard constraint — these keep one pathological row from dominating a
# backup, and keep `/ops/logs/` (which slices details_json to 300 chars
# for display) from paging in far more than it renders.
_MAX_MESSAGE = 500
_MAX_PATH = 300
_MAX_USER_AGENT = 200
_MAX_SLUG = 64


def _clip(value: object, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def record_error(
    *,
    exc: BaseException,
    request_id: str = "",
    source: str = "",
    endpoint_slug: str = "",
    request_path: str = "",
    request_method: str = "",
    status_code: int = 500,
    user_id: int | None = None,
    ip: str | None = None,
    user_agent: str = "",
    handled: bool = False,
) -> str | None:
    """Persist one error as `Event(type="error")`. Returns the row's pk as
    a string, or None if the write failed.

    Fulfils `apps.core.error_hook.ErrorRecorder`. Callers treat this as
    best-effort — a failure to record must never turn a handled error into
    an unhandled one — so every exception is swallowed and logged here
    rather than propagating to the request path.

    `request_path` arrives pre-scrubbed on the paths that can carry a
    secret: `apps.core.rest` passes `request._scrubbed_path`, which
    `URLTokenScrubMiddleware` set with the `/mcp/k/<token>/` segment
    replaced. We do not re-scrub, because doing so here would imply the
    other call sites are safe to leave unscrubbed.
    """
    try:
        from apps.events.models import Event

        event = Event.objects.create(
            type="error",
            details={
                "exc_class": type(exc).__name__,
                "message": _clip(exc, _MAX_MESSAGE),
                # "rest" | "mcp" | "system" — which pipeline raised.
                "source": source,
                "endpoint_slug": _clip(endpoint_slug, _MAX_SLUG),
                "path": _clip(request_path, _MAX_PATH),
                "method": request_method,
                "status": status_code,
                # True: the endpoint caught this and still returned a real
                # response (`ctx.trace.exception`), or it is a CSP report.
                # False: the request died. The distinction is the first
                # thing an operator triages by.
                "handled": bool(handled),
                "request_id": request_id,
                "ip": ip or "",
                "user_agent": _clip(user_agent, _MAX_USER_AGENT),
                "user_id": user_id,
            },
        )
        return str(event.pk)
    except Exception:
        # Includes the DB being the thing that broke — which is exactly
        # when a 500 handler calls in here.
        log.exception(
            "events.sinks.record_error: could not persist error event",
            extra={"request_id": request_id, "endpoint_slug": endpoint_slug},
        )
        return None


__all__ = ["record_error"]
