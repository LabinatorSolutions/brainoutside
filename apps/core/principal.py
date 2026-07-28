"""Unified `Principal`.

A `Principal` is the resolved identity attached to a `Ctx`. Two paths
converge here:

- **API-key bearer**: `Authorization: Bearer mcpsk_...`
  → `APIKeyAuthBackend` returns a Principal with `credential` = APIKey row.
- **OAuth 2.1 bearer**: `Authorization: Bearer <opaque>`
  → `OAuthBearerBackend` returns a Principal with `credential` = AccessToken row.

Endpoints don't care which auth method got them here — they read
`ctx.user` for ownership decisions and `ctx.credential` for
plan/credits/rate-limit attribution. Phase 5's credit charge keys off
`credential.id` so refunds attribute to the same key/token.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


@dataclass(frozen=True, slots=True)
class Principal:
    user: "AbstractBaseUser"
    credential: Any  # APIKey | AccessToken | None — duck-typed across backends
    credential_kind: str  # "api_key" | "oauth_token" | "session" | "anonymous"

    @property
    def is_authenticated(self) -> bool:
        return self.credential_kind != "anonymous"
