"""Registry → FastMCP tool bridge.

Each `EndpointSpec` becomes one FastMCP tool with a flat input schema
matching the endpoint's `Input` Pydantic model. The handler:

1. Reads the request envelope from the identity contextvars (set by the
   loopback ASGI middleware on the inbound hop).
2. Builds a `Ctx` with `source="mcp"`.
3. Validates the JSON-RPC payload against `spec.input_model`.
4. Awaits `spec.cls().run(inp, ctx)`.
5. Returns the output as a dict; FastMCP wraps it in JSON-RPC + content blocks.

The handler is dynamically signatured: each Pydantic field becomes a
KEYWORD_ONLY parameter so the tool's MCP input schema is flat (e.g.
`{"name": "phase2"}` rather than `{"args": {"name": "phase2"}}`).
"""
from __future__ import annotations

import inspect
import logging
import uuid
from typing import Any, Callable

from fastmcp import FastMCP
from pydantic import ValidationError

from apps.core import endpoint_gating
from apps.core.ctx import build_ctx
from apps.core.mcp.identity import (
    mcp_credential_var,
    mcp_request_id_var,
    mcp_user_id_var,
)
from apps.core.registry import EndpointSpec, registry

log = logging.getLogger(__name__)


def register_all(mcp: FastMCP) -> int:
    """Register every endpoint in the global registry as a FastMCP tool.

    Returns the number of tools registered. Called once from
    `apps/core/mcp/server.py` at boot.
    """
    count = 0
    for spec in registry.all():
        handler = build_handler(spec)
        mcp.tool(
            name=spec.mcp_tool_name,
            description=spec.description or spec.slug,
            tags=set(spec.tags) if spec.tags else None,
        )(handler)
        count += 1
    return count


def build_handler(spec: EndpointSpec) -> Callable[..., Any]:
    """Build a FastMCP-introspectable async handler for one endpoint."""
    input_model = spec.input_model
    output_model = spec.output_model

    async def handler(**kwargs: Any) -> dict[str, Any]:
        request_id = mcp_request_id_var.get() or uuid.uuid4().hex

        # runtime endpoint disable. Same
        # gate the REST view applies; MCP callers see the failure as
        # a tool error so the LLM can surface it cleanly.
        from asgiref.sync import sync_to_async

        gated = await sync_to_async(
            endpoint_gating.is_disabled, thread_sensitive=True
        )(spec.slug)
        if gated:
            raise RuntimeError(
                f"The tool '{spec.mcp_tool_name}' is temporarily disabled by "
                f"an operator. Try again later."
            )

        # FinalPolish F3 — defense-in-depth sunset gate. The Django proxy
        # short-circuits sunset calls with a JSON-RPC tool error before
        # they reach the loopback subprocess, but a loopback-direct caller
        # (test harness, internal tool) bypasses that path. Raising here
        # gives FastMCP a tool error so the loopback contract stays the
        # single source of truth.
        from django.utils import timezone

        if spec.is_sunset_at(timezone.now()):
            sunset_iso = spec.sunset_at.isoformat() if spec.sunset_at else ""
            message = (
                spec.deprecation_message
                or f"This endpoint was sunset on {sunset_iso} and is no longer available."
            )
            raise RuntimeError(message)

        ctx = build_ctx(
            request_id=request_id,
            source="mcp",
            user=None,  # Phase 3 resolves accounts.User from mcp_user_id_var
            credential=None,  # Phase 3.3 / 4.3 resolves APIKey or AccessToken
        )

        try:
            inp = input_model.model_validate(kwargs)
        except ValidationError as exc:
            # FastMCP renders raised exceptions as MCP tool errors. Keep the
            # surface stable by re-raising with a clear message; the underlying
            # ValidationError carries structured `errors()` for the client.
            raise ValueError(f"Input validation failed: {exc.errors(include_url=False)}") from exc

        instance = spec.cls()
        result = await instance.run(inp, ctx)

        if not isinstance(result, output_model):
            log.error(
                "mcp tool %s returned %r, expected %s",
                spec.mcp_tool_name,
                type(result).__name__,
                output_model.__name__,
                extra={"request_id": request_id},
            )
            raise RuntimeError(
                f"Endpoint {spec.slug} returned {type(result).__name__}, "
                f"expected {output_model.__name__}"
            )
        return result.model_dump()

    handler.__name__ = spec.mcp_tool_name
    handler.__doc__ = spec.description or spec.slug
    handler.__signature__ = _flat_signature(input_model)  # type: ignore[attr-defined]
    handler.__annotations__ = _flat_annotations(input_model)
    return handler


def _flat_signature(input_model: type) -> inspect.Signature:
    """Build an inspect.Signature with one KEYWORD_ONLY param per model field.

    FastMCP introspects the handler signature to derive the JSON Schema MCP
    clients see. A flat shape (`{"name": "..."}`) is much more LLM-friendly
    than a nested one (`{"args": {"name": "..."}}`).
    """
    params: list[inspect.Parameter] = []
    for field_name, field_info in input_model.model_fields.items():  # type: ignore[attr-defined]
        default = inspect.Parameter.empty if field_info.is_required() else field_info.default
        params.append(
            inspect.Parameter(
                name=field_name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=field_info.annotation,
            )
        )
    return inspect.Signature(parameters=params, return_annotation=dict)


def _flat_annotations(input_model: type) -> dict[str, Any]:
    """Pydantic's TypeAdapter inspects __annotations__ rather than __signature__,
    so we mirror the signature here to keep the schema generator happy.
    """
    out: dict[str, Any] = {}
    for field_name, field_info in input_model.model_fields.items():  # type: ignore[attr-defined]
        out[field_name] = field_info.annotation
    out["return"] = dict
    return out
