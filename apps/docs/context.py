"""Shared template context for `/docs/` pages.

Mirror of `apps.dashboard.context` and `apps.admin_panel.context` —
builds the sidebar nav (endpoints grouped by tag + the static guides
section) + the topbar's `nav_links` slot.

Each docs view does:

    ctx = build_layout_context(request, active="endpoint:hello")
    ctx["something_specific"] = ...
    return render(request, "docs/endpoint_detail.html", ctx)

`active` is a slug-prefixed string (`endpoint:<slug>` for endpoint
detail pages, `guide:<slug>` for static guides, `index` for the
catalog). The matching nav item gets `aria-current="page"` styling.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.urls import reverse


@dataclass(frozen=True)
class GuideEntry:
    slug: str
    label: str


# The 5 static guides shipped in 10.4.7. Adding a new guide = one new
# row here + one new `apps/docs/guides/<slug>.md` file.
GUIDES: list[GuideEntry] = [
    GuideEntry("auth", "Authentication"),
    GuideEntry("mcp-setup", "MCP setup"),
    GuideEntry("rate-limits", "Rate limits"),
    GuideEntry("webhooks", "Webhooks"),
    GuideEntry("errors", "Error codes"),
]


def _safe_url(url_name: str, **kwargs) -> str:
    """Resolve `url_name`, falling back to "#" if it can't be reversed.
    Defensive guard so a missing route degrades the sidebar link rather
    than 500ing the whole page."""
    try:
        return reverse(url_name, kwargs=kwargs) if kwargs else reverse(url_name)
    except Exception:
        return "#"


def _endpoint_nav_section(
    active: str, *, hidden_slugs: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Build the "Endpoints" section by walking the registry. One
    flat list ordered by slug for v1; tag grouping happens on the
    catalog page itself (sidebar stays flat to keep navigation
    fast).

    `hidden_slugs` are admin-only endpoints to omit for non-staff
    visitors — keeps the sidebar consistent with the catalog cards."""
    items: list[dict[str, Any]] = []
    try:
        from apps.core.registry import registry

        for spec in registry.all():
            if spec.slug in hidden_slugs:
                continue
            slug_marker = f"endpoint:{spec.slug}"
            items.append(
                {
                    "label": spec.slug,
                    "url": _safe_url("docs:endpoint-detail", slug=spec.slug),
                    "icon": "list",
                    "active": slug_marker == active,
                    "badge": 0,
                }
            )
    except Exception:
        # Registry import failure → leave the section empty rather
        # than blowing up the page.
        pass
    return {"label": "Endpoints", "items": items}


def _guides_nav_section(active: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for g in GUIDES:
        slug_marker = f"guide:{g.slug}"
        items.append(
            {
                "label": g.label,
                "url": _safe_url("docs:guide", slug=g.slug),
                "icon": "book",
                "active": slug_marker == active,
                "badge": 0,
            }
        )
    return {"label": "Guides", "items": items}


def build_nav_sections(
    active: str, *, hidden_slugs: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    return [
        {
            "label": "",
            "items": [
                {
                    "label": "All endpoints",
                    "url": _safe_url("docs:index"),
                    "icon": "home",
                    "active": active == "index",
                    "badge": 0,
                },
            ],
        },
        _endpoint_nav_section(active, hidden_slugs=hidden_slugs),
        _guides_nav_section(active),
    ]


def build_top_links() -> list[dict[str, Any]]:
    """Topbar nav links — `Dashboard` jump-back + `Sign in` shows for
    anonymous users on the topbar already (via the partial). The
    docs-side topbar surfaces the cross-app pivot to /dashboard/ for
    authed users; the partial hides the link when the user is anon
    (no harm — it'd 302 to /auth/login/ anyway)."""
    return [
        {"label": "Dashboard", "url": _safe_url("dashboard"), "active": False},
    ]


def build_layout_context(request, *, active: str) -> dict[str, Any]:
    # Hide admin-only endpoints from the sidebar for non-staff visitors so
    # the nav matches the catalog cards (and the 404 they'd get on click).
    hidden: frozenset[str] = frozenset()
    user = getattr(request, "user", None)
    is_staff = bool(user is not None and user.is_authenticated and user.is_staff)
    if not is_staff:
        try:
            from apps.core import endpoint_gating

            hidden = frozenset(endpoint_gating.admin_only_slugs())
        except Exception:
            hidden = frozenset()
    return {
        "docs_nav_sections": build_nav_sections(active, hidden_slugs=hidden),
        "docs_top_links": build_top_links(),
        "active_section": active,
    }


__all__ = [
    "GUIDES",
    "GuideEntry",
    "build_layout_context",
    "build_nav_sections",
    "build_top_links",
]
