"""Request-ID propagation middleware.

Reads `X-Request-ID` from inbound or mints a uuid4 hex string. The same
value rides every later hop:

- Stored on `request.request_id` (read by `EndpointView` and the proxy view).
- Bound into a `ContextVar` (`apps.core.log_context.request_id_var`) so any
  log record emitted during this request — anywhere in the call chain —
  picks it up via the `RequestContextFilter`.
- Echoed on the response as `X-Request-ID` (always — for client-side
  support tickets).
- Forwarded to the MCP subprocess as `X-MCP-Request-Id`.
- Recorded on every `APICallLog` row.

A single id traces a request from nginx → uvicorn → Django view → MCP
subprocess → APICallLog. The dedicated middleware lives here (in core)
because it's a cross-cutting concern, not specific to observability.
"""
from __future__ import annotations

import uuid
from typing import Awaitable, Callable

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import HttpRequest, HttpResponse

from apps.core import log_context


class RequestIdMiddleware:
    """Outermost middleware. Always sets request.request_id + response header."""

    sync_capable = False
    async_capable = True

    def __init__(self, get_response: Callable[[HttpRequest], Awaitable[HttpResponse]]) -> None:
        self.get_response = get_response
        markcoroutinefunction(self)
        if not iscoroutinefunction(get_response):
            raise RuntimeError(
                "RequestIdMiddleware requires the async ASGI stack. "
                "Uvicorn wires this automatically."
            )

    async def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.request_id = request_id  # type: ignore[attr-defined]
        # Bind the contextvar so log records emitted during this request
        # auto-carry request_id. user_id is left None here — bearer
        # resolution happens later in the chain (apps.core.rest +
        # apps.mcp_proxy.views) and those layers re-bind once the
        # Principal is known.
        token = log_context.bind(request_id=request_id, user_id=None)
        try:
            response = await self.get_response(request)
        finally:
            log_context.unbind(token)
        # Set unconditionally — overwriting any inner-middleware echo so the
        # client always sees the canonical id we recorded.
        response.headers["X-Request-ID"] = request_id
        return response
