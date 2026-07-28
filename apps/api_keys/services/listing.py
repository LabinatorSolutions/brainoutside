"""Read-only listing services for `apps.api_keys`.

Kept separate from `generate.py` so write-path concerns (atomicity,
secret-handling) don't bloat with every read query the dashboard needs.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from apps.api_keys.models import APIKey

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


def list_for_user(user: "AbstractBaseUser") -> list[APIKey]:
    """All non-deleted keys for a user, newest first. Includes revoked rows
    so the dashboard can show their history.

    uses `objects.for_user(user)` (the owner-only manager)
    rather than `.filter(user=user)` so a future view author can't
    accidentally drop the user filter.
    """
    return list(
        APIKey.objects.for_user(user).filter(is_deleted=False).order_by("-created_at")
    )


def get_user_key(user: "AbstractBaseUser", *, key_id: int) -> APIKey | None:
    """Resolve a key by id but only if it belongs to `user`. Returns None
    rather than raising — used by views that just no-op on a stale id.

    anonymous user → empty queryset → first() == None,
    matching the docstring.
    """
    return (
        APIKey.objects.for_user(user).filter(pk=key_id, is_deleted=False).first()
    )


def get_user_key_or_404(user: "AbstractBaseUser", *, key_id: int) -> APIKey:
    """fetch a key by id with strict ownership
    enforcement; raises `Http404` if the row doesn't exist OR doesn't
    belong to `user` OR is soft-deleted. Use in detail views where a
    cross-user crafted URL should be indistinguishable from "id
    doesn't exist".
    """
    return APIKey.objects.get_for_user_or_404(user, pk=key_id, is_deleted=False)
