"""Generic async REST view for registered endpoints.

`make_endpoint_view(spec)` returns a Django async view that:

1. Verifies the HTTP method.
2. Parses the JSON body into `spec.input_model` (pydantic v2).
3. Resolves a `Principal` from `Authorization: Bearer mcpsk_...` if present
  . Anonymous calls remain allowed at this phase
   wires plan/credit gating that requires auth.
4. Builds a `Ctx` carrying request_id + user + credential.
5. Awaits `spec.cls().run(inp, ctx)`.
6. Serializes the output via `spec.output_model.model_dump_json()`.
7. Echoes `X-Request-ID` on the response.

Error contract — kept simple in Phase 2; Phase 7 introduces the ErrorLog model
and a global exception handler:

- 400 `invalid_json`           — body is not valid JSON
- 401 `auth_required`          — endpoint charges credits but no bearer
- 401 `invalid_credential`     — Authorization header present but bad
- 402 `insufficient_credits`   — balance < endpoint's per-call cost
- 405 `method_not_allowed`     — wrong HTTP method
- 422 `input_validation_error` — pydantic input validation failed
- 500 `internal_error`         — anything else; safe message; no stacktrace leak
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Awaitable, Callable

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from pydantic import ValidationError

from apps.core import endpoint_gating, error_hook, idempotency, log_context
from apps.core.bearer import resolve as resolve_bearer
from apps.core.charging import charge as charge_credits
from apps.core.charging import make_idempotency_key
from apps.core.ctx import build_ctx
from apps.core.registry import EndpointSpec
from apps.core.security.client_ip import client_ip
from apps.core.security.lockout import is_token_locked
from apps.core.throttling import check as throttle_check

log = logging.getLogger(__name__)

AsyncView = Callable[[HttpRequest], Awaitable[HttpResponse]]


def make_endpoint_view(spec: EndpointSpec) -> AsyncView:
    """Build a per-spec async view. Used by `apps.core.urls`."""

    async def _dispatch(request: HttpRequest) -> HttpResponse:
        # RequestIdMiddleware sets request.request_id on every
        # inbound request. The fallback handles in-process callers (tests
        # using the test client without the middleware).
        request_id = getattr(request, "request_id", None) or uuid.uuid4().hex

        if request.method != spec.method:
            return _err(
                request_id,
                status=405,
                code="method_not_allowed",
                message=f"This endpoint only accepts {spec.method}.",
            )

        # runtime endpoint disable. The
        # registry is import-time only; this row gives operators a
        # knob to take a buggy endpoint offline without a redeploy.
        # Sits before auth/charge so a disabled endpoint never burns
        # cycles on credentials or credits. 503 (not 4xx) tells well-
        # behaved clients + status-page monitors this is a server-side
        # decision, not a client error.
        gated = await sync_to_async(
            endpoint_gating.is_disabled, thread_sensitive=True
        )(spec.slug)
        if gated:
            response = _err(
                request_id,
                status=503,
                code="endpoint_disabled",
                message=(
                    "This endpoint is temporarily disabled by an operator. "
                    "Try again later."
                ),
            )
            response.headers["Retry-After"] = "60"
            return response

        # resolve Principal from Authorization: Bearer.
        # The resolver registry in `apps.core.bearer` lets feature apps
        # (api_keys today, mcp_oauth) plug in without
        # apps.core importing them — Contract 1 stays clean.
        principal = None
        bearer = _bearer_token(request)
        if bearer is not None:
            # fail fast when the bearer's `mcpsk_<8>`
            # prefix is in lockout. Returns 429 with Retry-After even
            # when the secret half is correct — the lockout is at the
            # prefix level, so a stolen-prefix attacker gets stopped
            # without leaking whether the secret was right.
            gate = is_token_locked(bearer)
            if not gate.allowed:
                response = _err(
                    request_id,
                    status=429,
                    code="rate_limit_exceeded",
                    message=(
                        "Too many failed authentication attempts on this key prefix. "
                        f"Retry after {gate.retry_after_s}s."
                    ),
                    extra={"retry_after_s": gate.retry_after_s},
                )
                response.headers["Retry-After"] = str(gate.retry_after_s)
                return response

            principal = await resolve_bearer(bearer)
            if principal is None:
                return _err(
                    request_id,
                    status=401,
                    code="invalid_credential",
                    message="Bearer token is invalid, revoked, or expired.",
                )
        # Stash the resolved principal so the observability middleware can
        # populate user_id + credential_id on the EndpointCalled event
        # without re-doing the auth lookup.
        request._principal = principal  # type: ignore[attr-defined]

        # backfill `user_id` on the log contextvar now that
        # bearer resolution succeeded. `RequestIdMiddleware` bound
        # (request_id, user_id=None) on entry; this update lets every log
        # record emitted inside `run()` carry the resolved user. Reset is
        # handled by the outer middleware's `unbind()`.
        if principal is not None:
            log_context.update_user_id(principal.user.pk)

        # admin-only gate. An endpoint flagged `admin_only` (runtime
        # toggle on the EndpointFlag row) is hidden from non-staff: it
        # behaves as if it doesn't exist. We return 404 (not 403) so the
        # surface stays invisible — a non-staff caller can't even tell
        # the endpoint is registered. Anonymous callers (principal is
        # None) are by definition non-staff; staff (is_staff=True) fall
        # through and use the endpoint normally. The read goes through
        # the same Redis-cached path as the disable gate above.
        is_staff = principal is not None and bool(
            getattr(principal.user, "is_staff", False)
        )
        if not is_staff:
            admin_only = await sync_to_async(
                endpoint_gating.is_admin_only, thread_sensitive=True
            )(spec.slug)
            if admin_only:
                return _err(
                    request_id,
                    status=404,
                    code="not_found",
                    message="No endpoint matches this path.",
                )

        # BRAIN-SERVER FORK DIVERGENCE: every endpoint requires an
        # authenticated caller, credits or not. The upstream template
        # allows anonymous calls when credits_cost == 0; this server
        # fronts a private knowledge base, so there is no anonymous tier
        # (PLAN.md §5 "No anonymous access, any layer").
        if principal is None:
            return _err(
                request_id,
                status=401,
                code="auth_required",
                message=(
                    "Authentication required. "
                    "Provide an `Authorization: Bearer <token>` header."
                ),
            )

        # rate limit. Sync DB-bound (entitlement lookup +
        # bucket consume), so wrap in sync_to_async. The throttle hook
        # returns "allowed" when rate_limit isn't installed (no-op
        # default) so unit tests of EndpointView still work.
        principal_user = principal.user if principal else None
        # BRAIN-SERVER FORK DIVERGENCE: pass the credential so the
        # throttle can be per-KEY (all consumers share one user here;
        # the upstream per-user bucket would let one chatty consumer
        # starve the rest — PLAN.md grill A3).
        throttle = await sync_to_async(throttle_check, thread_sensitive=True)(
            user=principal_user,
            endpoint_slug=spec.slug,
            ip=client_ip(request),
            credential=principal.credential if principal else None,
        )
        if not throttle.allowed:
            response = _err(
                request_id,
                status=429,
                code="rate_limit_exceeded",
                message=(
                    f"Rate limit exceeded ({throttle.limit_per_min}/min). "
                    f"Retry after {throttle.retry_after_s}s."
                ),
                extra={
                    "limit_per_min": throttle.limit_per_min,
                    "retry_after_s": throttle.retry_after_s,
                },
            )
            response.headers["Retry-After"] = str(throttle.retry_after_s)
            response.headers["X-RateLimit-Limit"] = str(throttle.limit_per_min)
            response.headers["X-RateLimit-Remaining"] = "0"
            return response

        # Idempotency-Key dispatch. Runs BEFORE charge +
        # execute so a replay never re-charges credits. Anonymous
        # callers SKIP processing (no user scope = collision risk).
        # Body bytes are read here once (we'll re-use them for json.loads
        # below). Cache hits short-circuit with the previously-stored
        # response verbatim; mismatch → 422; in-flight → 409.
        body_bytes = request.body or b""
        idem_record: Any = None
        if bearer is not None and principal is not None:
            outcome = await sync_to_async(
                idempotency.process_request, thread_sensitive=True
            )(
                key=request.headers.get("Idempotency-Key", ""),
                user=principal.user,
                method=request.method or "",
                path=request.path or "",
                body=body_bytes,
            )
            if isinstance(outcome, idempotency.InvalidKey):
                return _err(
                    request_id,
                    status=400,
                    code="idempotency_key_invalid",
                    message=outcome.reason,
                )
            if isinstance(outcome, idempotency.Mismatch):
                return _err(
                    request_id,
                    status=422,
                    code="idempotency_key_mismatch",
                    message=(
                        "An Idempotency-Key with a different request body "
                        "was used recently. Use a fresh key or re-send the "
                        "original body."
                    ),
                )
            if isinstance(outcome, idempotency.InFlight):
                response = _err(
                    request_id,
                    status=409,
                    code="idempotency_request_in_flight",
                    message=(
                        "A request with this Idempotency-Key is currently "
                        "being processed. Retry in a moment."
                    ),
                )
                response.headers["Retry-After"] = "1"
                return response
            if isinstance(outcome, idempotency.Replay):
                response = HttpResponse(
                    outcome.response_body,
                    content_type=outcome.response_content_type or "application/json",
                    status=outcome.response_status,
                )
                response.headers["X-Request-ID"] = request_id
                response.headers["Idempotent-Replayed"] = "true"
                return response
            if isinstance(outcome, idempotency.Pending):
                idem_record = outcome.record
            # Skipped → idem_record stays None; flow continues normally.

        try:
            raw = body_bytes.decode("utf-8") if body_bytes else "{}"
            payload: Any = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return await _finalize_idem(
                idem_record,
                _err(
                    request_id,
                    status=400,
                    code="invalid_json",
                    message=f"Request body is not valid JSON: {exc}",
                ),
            )
        if not isinstance(payload, dict):
            return await _finalize_idem(
                idem_record,
                _err(
                    request_id,
                    status=400,
                    code="invalid_json",
                    message="Request body must be a JSON object.",
                ),
            )

        try:
            inp = spec.input_model.model_validate(payload)
        except ValidationError as exc:
            return await _finalize_idem(
                idem_record,
                _err(
                    request_id,
                    status=422,
                    code="input_validation_error",
                    message="Input validation failed.",
                    extra={"errors": exc.errors(include_url=False)},
                ),
            )

        ctx = build_ctx(
            request_id=request_id,
            source="rest",
            user=principal.user if principal else getattr(request, "user", None),
            credential=principal.credential if principal else None,
        )
        # let `ctx.trace.exception(...)` resolve the slug
        # without the endpoint having to pass it in explicitly.
        ctx.meta["endpoint_slug"] = spec.slug
        # propagate the per-endpoint Q2 timeout
        # override so `ctx.aenqueue(...)` can forward it to async_task.
        if spec.async_timeout_seconds > 0:
            ctx.meta["async_timeout_seconds"] = spec.async_timeout_seconds

        # credit charge wraps run(). For credits_cost==0 the
        # registered factory hands back a no-op CM, so the unauthenticated
        # / free-endpoint path is unchanged.
        idempotency_key = make_idempotency_key(
            credential_id=(
                principal.credential.pk
                if principal and principal.credential is not None
                else None
            ),
            request_id=request_id,
            endpoint_slug=spec.slug,
            amount=spec.credits_cost,
        )
        # stash consume metadata on the Ctx so that any
        # `ctx.aenqueue(...)` inside `run()` records (credits, key) onto
        # the TrackedTask. When the Q2 task dead-letters, the subscriber
        # rebuilds the refund key from these and issues a compensating
        # credit. Anonymous / free endpoints leave these absent.
        if spec.credits_cost > 0 and principal is not None:
            ctx.meta["consume_idempotency_key"] = idempotency_key
            ctx.meta["consume_credits"] = spec.credits_cost
            # Stash on the request so RequestLogMiddleware can stamp the
            # APICallLog row with the consume key — the CreditsRefunded
            # subscriber uses that key to find the row to flip.
            request._consume_idempotency_key = idempotency_key  # type: ignore[attr-defined]

        # credit-charge orchestration. The factory is sync
        # (DB-bound). We can't use a `with` block here because `await
        # ep.run(...)` straddles enter/exit. Manage the CM by hand:
        # enter via sync_to_async, refund-on-failure via the duck-typed
        # `Charge.issue_refund()`, exit via sync_to_async.
        charge_cm: Any = None
        charge_handle: Any = None
        if spec.credits_cost > 0 and ctx.user is not None:
            charge_cm = charge_credits(
                ctx.user,
                n=spec.credits_cost,
                idempotency_key=idempotency_key,
                endpoint_slug=spec.slug,
                request_id=request_id,
            )
            try:
                charge_handle = await sync_to_async(
                    charge_cm.__enter__, thread_sensitive=True
                )()
            except Exception as exc:
                if (
                    type(exc).__name__ == "InsufficientCreditsError"
                    and hasattr(exc, "required")
                    and hasattr(exc, "available")
                ):
                    return await _finalize_idem(
                        idem_record,
                        _err(
                            request_id,
                            status=402,
                            code="insufficient_credits",
                            message=(
                                f"Need {exc.required} credit(s); balance is "
                                f"{exc.available}. Top up at /dashboard/billing/."
                            ),
                            extra={
                                "required": int(exc.required),
                                "available": int(exc.available),
                            },
                        ),
                    )
                raise

        try:
            instance = spec.cls()
            result = await instance.run(inp, ctx)
        except ValueError as exc:
            # CLAUDE.md `run()` contract — an endpoint signals "user
            # input passed Pydantic but failed a downstream check" by
            # raising ValueError. Canonical examples: `safe_request`
            # refused the URL as unsafe (SSRF guard), an upstream API
            # returned an unparseable shape the endpoint re-raised as
            # ValueError, the user-supplied URL pointed at a private
            # IP. These are 422s, NOT 500s — the input was bad even
            # though it parsed.
            #
            # Why not write an ErrorLog row: the Pydantic-422 path
            # above (line ~271) doesn't either. Logging every SSRF
            # probe + every bad-upstream response would flood the
            # admin Errors panel with rows operators have to learn
            # to ignore. The ledger of failed calls is still visible
            # via APICallLog (status_code=422); ErrorLog is reserved
            # for actual server-side bugs.
            #
            # Refund: same as the 500 path — the user was charged at
            # CM-enter (line ~336) before `run()` saw the input.
            # Returning 422 without refunding would let buyers scan
            # the SSRF guard at the cost of the operator's plan
            # credits.
            if charge_handle is not None and hasattr(charge_handle, "issue_refund"):
                try:
                    await sync_to_async(
                        charge_handle.issue_refund, thread_sensitive=True
                    )(reason="input_validation_error")
                except Exception:
                    log.exception(
                        "rest: refund failed",
                        extra={"request_id": request_id, "slug": spec.slug},
                    )
            if charge_cm is not None:
                try:
                    await sync_to_async(
                        charge_cm.__exit__, thread_sensitive=True
                    )(None, None, None)
                except Exception:
                    log.exception(
                        "rest: charge __exit__ failed",
                        extra={"request_id": request_id, "slug": spec.slug},
                    )
            # Message cap: surface the endpoint author's message
            # verbatim (the contract is that ValueError messages are
            # user-safe), but cap at 500 chars so a runaway exception
            # can't return a multi-KB response.
            msg = str(exc)[:500] or "Input failed downstream validation."
            return await _finalize_idem(
                idem_record,
                _err(
                    request_id,
                    status=422,
                    code="input_validation_error",
                    message=msg,
                ),
            )
        except Exception as exc:
            # Refund the credit charge before returning 500. `issue_refund`
            # is idempotent and lives on the duck-typed `Charge` handle.
            if charge_handle is not None and hasattr(charge_handle, "issue_refund"):
                try:
                    await sync_to_async(
                        charge_handle.issue_refund, thread_sensitive=True
                    )(reason="endpoint_error")
                except Exception:
                    log.exception(
                        "rest: refund failed",
                        extra={"request_id": request_id, "slug": spec.slug},
                    )
            if charge_cm is not None:
                try:
                    await sync_to_async(
                        charge_cm.__exit__, thread_sensitive=True
                    )(None, None, None)
                except Exception:
                    log.exception(
                        "rest: charge __exit__ failed",
                        extra={"request_id": request_id, "slug": spec.slug},
                    )
            # persist a structured ErrorLog row + mirror to Sentry
            # via the apps.core.error_hook bridge (registered by
            # `ObservabilityConfig.ready()`). No-op when observability isn't
            # installed (rare; only narrow apps.core unit tests).
            try:
                await sync_to_async(
                    error_hook.record_error, thread_sensitive=True
                )(
                    exc=exc,
                    request_id=request_id,
                    source="rest",
                    endpoint_slug=spec.slug,
                    request_path=getattr(request, "_scrubbed_path", request.path) or "",
                    request_method=request.method or "",
                    status_code=500,
                    user_id=(principal.user.pk if principal is not None else None),
                    ip=client_ip(request),
                    user_agent=request.headers.get("User-Agent", ""),
                    handled=False,
                )
            except Exception:
                log.exception(
                    "rest: ErrorLog write failed",
                    extra={"request_id": request_id, "slug": spec.slug},
                )
            log.exception(
                "endpoint %s/%s raised",
                spec.version,
                spec.slug,
                extra={"request_id": request_id},
            )
            return await _finalize_idem(
                idem_record,
                _err(
                    request_id,
                    status=500,
                    code="internal_error",
                    message="The endpoint failed unexpectedly.",
                ),
            )

        # Success path — commit the charge by exiting the CM with no exc.
        if charge_cm is not None:
            try:
                await sync_to_async(
                    charge_cm.__exit__, thread_sensitive=True
                )(None, None, None)
            except Exception:
                log.exception(
                    "rest: charge __exit__ failed",
                    extra={"request_id": request_id, "slug": spec.slug},
                )

        if not isinstance(result, spec.output_model):
            log.error(
                "endpoint %s/%s returned %r, expected %s",
                spec.version,
                spec.slug,
                type(result).__name__,
                spec.output_model.__name__,
                extra={"request_id": request_id},
            )
            return await _finalize_idem(
                idem_record,
                _err(
                    request_id,
                    status=500,
                    code="internal_error",
                    message="The endpoint returned an unexpected response shape.",
                ),
            )

        body = result.model_dump_json()
        response = HttpResponse(body, content_type="application/json", status=200)
        response.headers["X-Request-ID"] = request_id
        if spec.deprecated and spec.deprecated_at is None:
            # Legacy boolean flag (pre-F3) had no date associated; stamp
            # a coarse `Deprecation: true` so existing clients still see
            # the warning. The richer Deprecation/Sunset/Link triad lands
            # via the outer wrapper when `deprecated_at` is set.
            response.headers["Deprecation"] = "true"

        # Note: stamping APIKey.last_used_* lives in apps.api_keys as a
        # subscriber on `EndpointCalled` — keeps the core
        # request path free of feature-app imports.
        return await _finalize_idem(idem_record, response)

    @csrf_exempt
    async def view(request: HttpRequest) -> HttpResponse:
        # FinalPolish F3 — RFC 8594 deprecation + sunset gate. Sits at the
        # very top of dispatch so a sunset endpoint never burns cycles on
        # auth resolution or charge attempts. Deprecation only stamps
        # advisory headers; sunset short-circuits with 410 Gone.
        now = timezone.now()
        request_id = getattr(request, "request_id", None) or uuid.uuid4().hex

        if spec.is_sunset_at(now):
            sunset_iso = spec.sunset_at.isoformat() if spec.sunset_at else ""
            response = _err(
                request_id,
                status=410,
                code="endpoint_sunset",
                message=(
                    spec.deprecation_message
                    or f"This endpoint was sunset on {sunset_iso} and is no longer available."
                ),
                extra={"sunset_at": sunset_iso},
            )
            for k, v in spec.deprecation_response_headers().items():
                response.headers[k] = v
            return response

        response = await _dispatch(request)

        # Stamp the RFC 8594 advisory headers on EVERY response of a
        # deprecated-but-not-yet-sunset endpoint — including 4xx errors
        # so clients hitting validation failures still see the warning.
        if spec.is_deprecation_active_at(now):
            for k, v in spec.deprecation_response_headers().items():
                response.headers[k] = v
        return response

    view.__name__ = f"endpoint_{spec.version}_{spec.slug}"
    return view


async def _finalize_idem(record: Any, response: HttpResponse) -> HttpResponse:
    """Cache the rendered response on the idempotency row, if any.

    Called as the last step on every terminal exit of `make_endpoint_view`'s
    inner view. `cache_response` itself is a no-op when status is 5xx
    (the row is dropped instead so a transient failure doesn't cement
    a bad response). Always returns the response unchanged."""
    if record is None:
        return response
    body = response.content if hasattr(response, "content") else b""
    content_type = response.get("Content-Type", "application/json")
    try:
        await sync_to_async(idempotency.cache_response, thread_sensitive=True)(
            record,
            status=response.status_code,
            body=bytes(body),
            content_type=content_type,
        )
    except Exception:
        log.exception(
            "rest: idempotency cache_response failed (key=%s user=%s)",
            getattr(record, "key", "?"),
            getattr(record, "user_id", "?"),
        )
    return response



def _bearer_token(request: HttpRequest) -> str | None:
    """Extract the bearer token from `Authorization: Bearer <token>`.

    Returns None when the header is absent or doesn't carry a bearer
    scheme. We don't return the token for any other scheme — Phase 4 may
    layer additional schemes on top, but until then unknown ones are
    treated as "no credential" rather than 401, matching anonymous flow.
    """
    raw = request.headers.get("Authorization", "")
    if not raw:
        return None
    parts = raw.strip().split(None, 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def _err(
    request_id: str,
    *,
    status: int,
    code: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> JsonResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if extra:
        body["error"].update(extra)
    response = JsonResponse(body, status=status)
    response.headers["X-Request-ID"] = request_id
    return response
