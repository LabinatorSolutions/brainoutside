"""Shared nav context for the ops UI pages. Each ops view merges
`ops_context(request)` into its render context."""
from __future__ import annotations

from django.urls import reverse


def _mark_active(sections: list[dict], path: str) -> None:
    """Flag the one nav item the current request belongs to.

    Longest match wins, and that is the whole subtlety: the dashboard is
    mounted at the ops root (`/ops/`), which is a prefix of every other
    ops URL, so a naive `startswith` lights up "Dashboard" on every single
    page. An exact match outranks any prefix, and among prefixes the
    longest one wins — so `/ops/brain/take-2026-07-x/` highlights
    "Brain browser", not "Dashboard".

    Prefix matching rather than exact-only is what makes detail pages
    work: entity, feed detail and chat session pages have no nav entry of
    their own and should highlight their parent section.
    """
    best: dict | None = None
    best_score = -1
    for section in sections:
        for item in section["items"]:
            url = item["url"]
            if path == url:
                score = len(url) + 1  # exact beats the same URL as a prefix
            elif path.startswith(url):
                score = len(url)
            else:
                continue
            if score > best_score:
                best, best_score = item, score
    if best is not None:
        best["active"] = True


def ops_context(request) -> dict:
    settings_url = reverse("brainconfig:settings")
    base = settings_url.rsplit("settings/", 1)[0]
    sections = [
        {
            "label": "Ops",
            "items": [
                {"label": "Dashboard", "url": reverse("brainconfig:dashboard")},
                {"label": "Brain browser", "url": reverse("brainconfig:browser")},
                {"label": "Graph", "url": reverse("brainconfig:graph")},
                {"label": "Timeline", "url": reverse("brainconfig:timeline")},
                {"label": "Feed queue", "url": reverse("brainconfig:feeds")},
                {"label": "Chat", "url": reverse("brainconfig:chat")},
                {"label": "Tasks", "url": reverse("brainconfig:tasks")},
                {"label": "Logs", "url": reverse("brainconfig:logs")},
                {"label": "Settings", "url": settings_url},
                {"label": "Setup & health", "url": reverse("brainconfig:health")},
            ],
        },
        {
            "label": "Public",
            "items": [
                {"label": "API docs", "url": "/docs/"},
            ],
        },
    ]
    # `request` is None in a few unit tests that build the nav in isolation;
    # no request just means nothing is highlighted.
    if request is not None and getattr(request, "path", ""):
        _mark_active(sections, request.path)

    return {
        "ops_nav_sections": sections,
        "ops_top_links": [
            {"label": "Docs", "url": "/docs/"},
            {"label": "Health", "url": "/readyz"},
        ],
        "ops_base": base,
    }
