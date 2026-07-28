"""Docs site views.

G1 ships the layout shell + 3 placeholder views — index, endpoint
detail, guide — so the URL surface is complete and every nav link
resolves. G2-G5 swap real content in.

No `@login_required` on any docs view: docs should be browsable
without an account. The Try-it panel (G4) is the only authed surface
— it shows a "Sign in to use Try it" CTA for anonymous users
(per locked design choice 1(a)).
"""
from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.docs import context
from apps.docs.services import catalog as catalog_service
from apps.docs.services import endpoint_detail as detail_service
from apps.docs.services import guides as guides_service


def _is_staff(request: HttpRequest) -> bool:
    """True iff a logged-in staff user is browsing (session auth). Docs
    are public, so anonymous visitors resolve to AnonymousUser → False.
    Drives admin-only endpoint hiding: non-staff never see admin-only
    cards or detail pages."""
    user = getattr(request, "user", None)
    return bool(user is not None and user.is_authenticated and user.is_staff)


@require_GET
def index_view(request: HttpRequest) -> HttpResponse:
    """Endpoint catalog.

    Lists every registered endpoint grouped by tag with a client-side
    Alpine search filter. Tag groups are alphabetical with
    `Uncategorized` last; cards inside each group sort by slug.
    """
    bundle = catalog_service.get_catalog(is_staff=_is_staff(request))
    ctx = context.build_layout_context(request, active="index")
    ctx.update({"bundle": bundle})
    return render(request, "docs/index.html", ctx)


@require_GET
def endpoint_detail_view(request: HttpRequest, slug: str) -> HttpResponse:
    """Per-endpoint detail page (10.4.3 / 10.4.4 / 10.4.5).

    Header (verb / path / credits / version / deprecated pill) +
    description + 4 tabs (REST / MCP / Python SDK / JS SDK) +
    Pydantic-rendered Input + Output schema tables with collapsible
    nested sub-schemas + try-it placeholder for G4.

    Resolves the spec via `apps.core.registry.registry.by_slug(slug)` —
    404 on unknown slug. v1 is the default; multi-version slugs pick
    the v1 row (per-version detail pages can land in Phase 11 if
    needed; today no endpoint ships a non-v1 alternate).
    """
    from apps.core.registry import registry

    matches = registry.by_slug(slug)
    if not matches:
        raise Http404(f"Endpoint {slug!r} not registered.")
    spec = next((s for s in matches if s.version == "v1"), matches[0])

    # admin-only gate — a non-staff visitor must not be able to reach the
    # detail page of a hidden endpoint by guessing its URL. 404 (not 403)
    # keeps it indistinguishable from an unregistered slug. Staff fall
    # through; `admin_only` rides into the context so the page can badge it.
    from apps.core import endpoint_gating

    admin_only = endpoint_gating.is_admin_only(slug)
    if admin_only and not _is_staff(request):
        raise Http404(f"Endpoint {slug!r} not registered.")

    bundle = detail_service.get_endpoint_detail(spec, request)
    ctx = context.build_layout_context(request, active=f"endpoint:{slug}")
    ctx.update({"bundle": bundle, "spec": spec, "admin_only": admin_only})
    return render(request, "docs/endpoint_detail.html", ctx)


@require_GET
def guide_view(request: HttpRequest, slug: str) -> HttpResponse:
    """Static guide.

    Slug is whitelisted against `context.GUIDES` so a crafted URL with
    a path-traversal-like slug (`../foo`) can't read arbitrary files
    from disk. 404 on (a) unknown slug, (b) markdown file missing
    from `apps/docs/guides/`.
    """
    entry = next((g for g in context.GUIDES if g.slug == slug), None)
    if entry is None:
        raise Http404(f"Guide {slug!r} not registered.")
    bundle = guides_service.render_guide(slug, title=entry.label)
    if bundle is None:
        raise Http404(f"Guide {slug!r} markdown file missing.")
    ctx = context.build_layout_context(request, active=f"guide:{slug}")
    ctx.update({"bundle": bundle})
    return render(request, "docs/guide.html", ctx)
