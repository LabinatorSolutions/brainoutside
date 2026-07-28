"""Shared nav context for the ops UI pages (settings now; dashboard,
browser, feeds, logs as they land in M1.10+). Each ops view merges
`ops_context(request)` into its render context."""
from __future__ import annotations

from django.urls import reverse


def ops_context(request) -> dict:
    settings_url = reverse("brainconfig:settings")
    base = settings_url.rsplit("settings/", 1)[0]
    return {
        "ops_nav_sections": [
            {
                "label": "Ops",
                "items": [
                    {"label": "Dashboard", "url": reverse("brainconfig:dashboard")},
                    {"label": "Brain browser", "url": reverse("brainconfig:browser")},
                    {"label": "Settings", "url": settings_url},
                ],
            },
            {
                "label": "Public",
                "items": [
                    {"label": "API docs", "url": "/docs/"},
                ],
            },
        ],
        "ops_top_links": [
            {"label": "Docs", "url": "/docs/"},
            {"label": "Health", "url": "/readyz"},
        ],
        "ops_base": base,
    }
