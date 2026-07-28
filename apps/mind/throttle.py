"""Per-KEY rate limiter (grill A3: the template throttles per-user, and
every consumer here is the same user — one chatty agent would starve the
rest). Fixed one-minute window in cache (Redis in prod; LocMem dev).

Registered into apps.core.throttling from MindConfig.ready(); rest.py
passes `credential=` through (marked divergence).
"""
from __future__ import annotations

import time

from django.core.cache import cache

from apps.core.throttling import ThrottleResult
from apps.mind.models import DEFAULT_RATE_LIMIT_PER_MIN, Consumer


def check(*, user=None, endpoint_slug: str = "", ip: str | None = None, cost: int = 1, credential=None, **_: object) -> ThrottleResult:
    if credential is None:
        # Session/UI callers (no API key) are not throttled here.
        return ThrottleResult(allowed=True, remaining=10**6, retry_after_s=0, limit_per_min=10**6)

    profile = Consumer.objects.filter(api_key=credential).first()
    limit = profile.rate_limit_per_min if profile else DEFAULT_RATE_LIMIT_PER_MIN

    window = int(time.time() // 60)
    key = f"brainrl:{getattr(credential, 'pk', 'x')}:{window}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.add(key, 1, timeout=120)
        count = 1

    if count > limit:
        return ThrottleResult(
            allowed=False,
            remaining=0,
            retry_after_s=60 - int(time.time() % 60),
            limit_per_min=limit,
            reason="per-key limit exceeded",
        )
    return ThrottleResult(
        allowed=True, remaining=max(0, limit - count), retry_after_s=0, limit_per_min=limit
    )
