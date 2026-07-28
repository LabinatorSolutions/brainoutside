"""Endpoint catalog data.

Groups every registered endpoint by tag for the docs index page.
Endpoints with no tags land in a single "Uncategorized" group;
endpoints with multiple tags appear in each.

The page mounts a client-side Alpine search filter — the service
returns the full catalog and the template handles search via
`x-show`. Server-side search would also work but the catalog is
small (typically <50 specs) and the snappier UX of instant
client-side filtering is worth the duplicate render.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


UNCATEGORIZED = "Uncategorized"


@dataclass
class EndpointCard:
    """One endpoint card on the index page. The template iterates
    these per group; `search_blob` is what Alpine matches against."""

    slug: str
    version: str
    method: str
    path: str
    description: str
    credits_cost: int
    deprecated: bool
    admin_only: bool = False
    tags: list[str] = field(default_factory=list)

    @property
    def search_blob(self) -> str:
        """Lowercased blob used by the client-side Alpine filter.
        Joins every searchable field so a single substring match
        against the input picks up slug / description / tag hits."""
        parts = [self.slug, self.description, " ".join(self.tags)]
        return " ".join(parts).lower()


@dataclass
class CatalogGroup:
    label: str
    cards: list[EndpointCard] = field(default_factory=list)


@dataclass
class CatalogBundle:
    groups: list[CatalogGroup] = field(default_factory=list)
    total_endpoints: int = 0


def get_catalog(*, is_staff: bool = False) -> CatalogBundle:
    """Build the catalog from `apps.core.registry`. Each spec lands in
    each of its tags' groups (and once in `Uncategorized` when
    tag-less). Groups are alphabetical with `Uncategorized` last so
    the eye lands on real categories first.

    `is_staff` gates admin-only endpoints: non-staff visitors never see
    a card for an endpoint whose `EndpointFlag.admin_only` is set (the
    runtime hide toggle). Staff see every endpoint, with an admin-only
    badge rendered by the template."""
    bundle = CatalogBundle()

    try:
        from apps.core.registry import registry
    except Exception:
        return bundle

    specs = registry.all()

    # Admin-only set (runtime hide toggle). One bulk DB query — this is a
    # page render, not a hot path. Non-staff: drop the cards entirely.
    # Staff: keep them but stamp `admin_only` so the template can badge
    # them as still-hidden-from-the-public.
    try:
        from apps.core import endpoint_gating

        hidden = endpoint_gating.admin_only_slugs()
    except Exception:
        hidden = set()
    if hidden and not is_staff:
        specs = [s for s in specs if s.slug not in hidden]

    bundle.total_endpoints = len(specs)

    # Tag → cards
    grouped: dict[str, list[EndpointCard]] = {}
    for spec in specs:
        card = EndpointCard(
            slug=spec.slug,
            version=spec.version,
            method=spec.method,
            path=f"/api/{spec.version}/{spec.path}",
            description=spec.description or "",
            credits_cost=spec.credits_cost,
            # FinalPolish F3 — `is_deprecated` fires for BOTH the legacy
            # bool flag AND any spec that carries a `deprecated_at` date.
            # Keeps the catalog chip honest for both deprecation styles
            # without making the template do the OR-logic itself.
            deprecated=spec.is_deprecated,
            admin_only=spec.slug in hidden,
            tags=list(spec.tags),
        )
        if not spec.tags:
            grouped.setdefault(UNCATEGORIZED, []).append(card)
            continue
        for tag in spec.tags:
            grouped.setdefault(tag, []).append(card)

    # Order: tags alphabetical (case-insensitive), Uncategorized last.
    sorted_tags = sorted(
        (t for t in grouped if t != UNCATEGORIZED),
        key=lambda t: t.lower(),
    )
    for t in sorted_tags:
        bundle.groups.append(
            CatalogGroup(label=t, cards=sorted(grouped[t], key=lambda c: c.slug))
        )
    if UNCATEGORIZED in grouped:
        bundle.groups.append(
            CatalogGroup(
                label=UNCATEGORIZED,
                cards=sorted(grouped[UNCATEGORIZED], key=lambda c: c.slug),
            )
        )

    return bundle


__all__ = ["CatalogBundle", "CatalogGroup", "EndpointCard", "UNCATEGORIZED", "get_catalog"]
