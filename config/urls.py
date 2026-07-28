"""Root URL routing — brain server.

Public surface: /api/ (registry REST), /mcp (proxy → FastMCP subprocess),
/docs/ (endpoint docs), /healthz, /readyz. Ops UI mounts later under
DJANGO_ADMIN_URL_PATH + app pages; everything session-authed sits behind a
network boundary in prod (PLAN.md §9).
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.db import connections
from django.http import HttpResponse, JsonResponse
from django.urls import include, path

from apps.brain.views import github_webhook
from apps.core.views import csp_report, robots_txt, security_txt
from apps.mcp_proxy.views import mcp_proxy_view


def healthz(request):
    """Liveness: process is up, no external deps touched."""
    return HttpResponse("ok", content_type="text/plain")


def readyz(request):
    """Readiness: DB reachable + brain clone valid with the contract present."""
    from apps.brain.services import gitrepo

    checks: dict[str, object] = {}
    status = 200
    try:
        connections["default"].cursor().execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as exc:  # pragma: no cover - failure path
        checks["db"] = f"fail: {exc.__class__.__name__}"
        status = 503
    brain = gitrepo.status_probe()
    if brain.get("valid") and brain.get("contract_ok"):
        checks["brain"] = {"head": str(brain.get("head", ""))[:12]}
    else:
        checks["brain"] = brain
        status = 503
    return JsonResponse({"status": "ok" if status == 200 else "fail", **checks}, status=status)


urlpatterns: list = [
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path(".well-known/security.txt", security_txt, name="security-txt"),
    path("robots.txt", robots_txt, name="robots-txt"),
    path("_csp-report/", csp_report, name="csp-report"),
    # Registry-driven REST: <version>/<slug> + _catalog/_openapi.json/_health.
    path("api/", include("apps.core.urls")),
    # Django proxy view → FastMCP loopback subprocess (bearer API-key auth).
    # Both slash forms: MCP clients POST without the trailing slash.
    path("mcp/", mcp_proxy_view, name="mcp"),
    path("mcp", mcp_proxy_view),
    # Public docs site (registry-driven catalog + per-endpoint detail).
    path("docs/", include("apps.docs.urls", namespace="docs")),
    # GitHub push → sync (HMAC-verified, deduped, echo-guarded).
    path("webhooks/github", github_webhook, name="github-webhook"),
]

if settings.DJANGO_ADMIN_URL_PATH:
    urlpatterns.append(path(settings.DJANGO_ADMIN_URL_PATH, admin.site.urls))

# Ops UI (Settings now; dashboard/browser in M1.10). Mounted under the
# admin-panel prefix so AdminIPAllowlistMiddleware guards it; in prod the
# whole prefix also sits behind the network boundary (PLAN.md §9).
_ops_prefix = (settings.ADMIN_PANEL_URL_PATH or "ops/").strip("/") + "/"
urlpatterns.append(path(_ops_prefix, include("apps.brainconfig.urls")))

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")

handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"
