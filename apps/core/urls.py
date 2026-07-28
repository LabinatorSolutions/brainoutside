"""Auto-built URLconf from the endpoint registry (ETag).

Mounted under `/api/` by `config/urls.py`. For every (version, slug) in the
registry we register `<version>/<slug>` → `make_endpoint_view(spec)`. For
each version present we also register the three built-in routes:

- `<version>/_catalog`       — every endpoint in this version
- `<version>/_openapi.json`  — OpenAPI 3.1 doc for this version
- `<version>/_health`        — minimal liveness check

Phase 7.5 promotes `_health` to richer `/healthz` + `/readyz` endpoints.
Phase 2.6 makes RequestLogMiddleware track every call here.

`_openapi.json` and `_catalog` set an ETag header so
`django.middleware.http.ConditionalGetMiddleware` short-circuits
`If-None-Match` re-requests to 304 (Not Modified). Both bodies are pure
functions of the in-process registry which never changes after boot, so
the ETag is the SHA-256 of the body computed once per process.
"""
from __future__ import annotations

import hashlib
import json

from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import URLPattern, path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from apps.core import endpoint_gating
from apps.core.bearer import resolve as resolve_bearer
from apps.core.openapi import build_openapi
from apps.core.registry import registry
from apps.core.rest import make_endpoint_view


def _etag_for(body: str) -> str:
    """Strong ETag (RFC 7232 §2.3) — quoted ASCII hex digest."""
    return '"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:16] + '"'


def _requester_is_staff(request: HttpRequest) -> bool:
    """Best-effort staff check for the public catalog / openapi views.

    These surfaces accept session cookies (browser) AND bearer tokens
    (API key / OAuth), so we check both: session `user.is_staff` first,
    then resolve any `Authorization: Bearer` token to a principal. Any
    failure resolves to non-staff (the safe direction — admin-only
    endpoints stay hidden). Used only to decide whether admin-only
    endpoints appear in the document, never to grant access."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False) and user.is_staff:
        return True
    raw = request.headers.get("Authorization") or ""
    parts = raw.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    token = parts[1].strip()
    if not token:
        return False
    try:
        principal = async_to_sync(resolve_bearer)(token)
    except Exception:
        return False
    return bool(principal is not None and getattr(principal.user, "is_staff", False))


def _hidden_slugs_for(request: HttpRequest) -> frozenset[str]:
    """Admin-only slugs to hide from this requester. Empty for staff (they
    see everything) and when nothing is flagged admin-only."""
    if _requester_is_staff(request):
        return frozenset()
    try:
        return frozenset(endpoint_gating.admin_only_slugs())
    except Exception:
        return frozenset()


def _build_urlpatterns() -> list[URLPattern]:
    patterns: list[URLPattern] = []
    versions = sorted({s.version for s in registry.all()}) or ["v1"]

    for version in versions:
        patterns.extend(_builtin_routes(version))

    for spec in registry.all():
        view = make_endpoint_view(spec)
        patterns.append(
            path(
                f"{spec.version}/{spec.slug}",
                view,
                name=f"endpoint_{spec.version}_{spec.slug}",
            )
        )
    return patterns


def _builtin_routes(version: str) -> list[URLPattern]:
    return [
        path(f"{version}/_catalog", _make_catalog_view(version), name=f"catalog_{version}"),
        path(
            f"{version}/_openapi.json",
            _make_openapi_view(version),
            name=f"openapi_{version}",
        ),
        path(f"{version}/_health", _health_view, name=f"health_{version}"),
        # `_jobs/<id>` is mounted from `config/urls.py`
        # against `apps.jobs.urls` rather than wired here, so apps.core
        # stays free of an `apps.jobs` import (Contract 1).
    ]


def _make_catalog_view(version: str):
    """ETag-tagged catalog. Bodies cached per process keyed by the set of
    admin-only slugs hidden from the requester, so admin-only endpoints
    are omitted for non-staff while staff (empty hidden-set) get the full
    listing. `ConditionalGetMiddleware` flips a matching `If-None-Match`
    to 304. The cache map is bounded — one entry per distinct hidden-set,
    which only changes when an operator toggles admin-mode."""
    cache_map: dict[frozenset[str], tuple[str, str]] = {}

    @csrf_exempt
    @require_GET
    def view(request: HttpRequest) -> HttpResponse:
        hidden = _hidden_slugs_for(request)
        if hidden not in cache_map:
            entries = [
                s.to_catalog_entry()
                for s in registry.all()
                if s.version == version and s.slug not in hidden
            ]
            body = json.dumps(
                {"version": version, "count": len(entries), "endpoints": entries}
            )
            cache_map[hidden] = (body, _etag_for(body))
        body, etag = cache_map[hidden]
        resp = HttpResponse(body, content_type="application/json", status=200)
        resp["ETag"] = etag
        # Body varies by caller identity — keep shared caches honest.
        resp["Vary"] = "Authorization, Cookie"
        return resp

    view.__name__ = f"catalog_{version}"
    return view


def _make_openapi_view(version: str):
    """cache the rendered OpenAPI body in-process forever
    (until the process restarts). The doc is a pure function of the
    registry, which only changes at startup, so the document only needs
    to be (re)built once per process.

    ETag set so `ConditionalGetMiddleware` short-circuits
    `If-None-Match` re-requests to 304. SDK generators / docs sites
    that re-fetch the OpenAPI doc on each load now pay one TCP round-
    trip instead of one body re-render."""
    cache_map: dict[frozenset[str], tuple[str, str]] = {}

    @csrf_exempt
    @require_GET
    def view(request: HttpRequest) -> HttpResponse:
        hidden = _hidden_slugs_for(request)
        if hidden not in cache_map:
            doc = build_openapi(
                version=version,
                title=getattr(settings, "APP_NAME", "API"),
                exclude_slugs=hidden,
            )
            body = json.dumps(doc)
            cache_map[hidden] = (body, _etag_for(body))
        body, etag = cache_map[hidden]
        # Use HttpResponse + manual dumps so we control the content-type exactly
        # (some validators are picky about `application/openapi+json` vs json).
        resp = HttpResponse(body, content_type="application/json", status=200)
        resp["ETag"] = etag
        # Body varies by caller identity — keep shared caches honest.
        resp["Vary"] = "Authorization, Cookie"
        return resp

    view.__name__ = f"openapi_{version}"
    return view


@csrf_exempt
@require_GET
def _health_view(_request: HttpRequest) -> JsonResponse:
    # Phase 7.5 expands this with /healthz (liveness) + /readyz (DB/Redis/MCP probes).
    return JsonResponse({"status": "ok"})


urlpatterns = _build_urlpatterns()
