"""Caller-tier resolution — one place, below every serving layer."""
from __future__ import annotations

from apps.mind.models import Consumer

TIER_ORDER = {"public": 0, "agents-only": 1, "private": 2}


def tier_for_credential(credential: object) -> str:
    """APIKey -> its consumer profile's tier; no profile -> public.

    Least privilege: an unprofiled key can only read published content.
    (Anonymous callers never reach here — rest.py 401s them first.)
    """
    if credential is None:
        return "public"
    try:
        # NB: not getattr(credential, "consumer_profile", ...) — a missing
        # OneToOne raises DoesNotExist from the descriptor, defaults don't
        # apply.
        profile = Consumer.objects.filter(api_key=credential).first()
    except (TypeError, ValueError):  # not an APIKey (e.g. OAuth token)
        return "public"
    return profile.max_visibility if profile else "public"


def allows(tier: str, visibility: str) -> bool:
    return TIER_ORDER.get(visibility, 2) <= TIER_ORDER.get(tier, 0)
