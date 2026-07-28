"""Bearer-token resolver registry.

`apps.core.rest.EndpointView` only knows about *bearer authentication in
the abstract*. Concrete backends (API key, OAuth 2.1 access token) live
in their own apps and register themselves here at startup. This keeps
the `apps.core` layer pure — it has no reverse dependency on a feature
app, which import-linter Contract 1 enforces.

Each registered resolver is an async callable `(token: str) -> Principal | None`.
We try them in registration order and return the first match, so the
order at boot determines priority. For Phase 3 only the API-key resolver
exists; Phase 4.3 adds OAuth and registers it after API keys (cheaper
prefix match wins).

Resolvers are sync DB-bound today; the wrapper here adapts each one
through `sync_to_async` so the request loop stays non-blocking. Phase 8.2
will introduce a Redis cache layer in front of the resolvers themselves.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from asgiref.sync import sync_to_async

if TYPE_CHECKING:
    from apps.core.principal import Principal

# (name, sync_callable) tuples. The name is logged on resolution + makes
# debugging "which backend authenticated this caller" tractable.
_resolvers: list[tuple[str, Callable[[str], "Principal | None"]]] = []


def register(name: str, resolver: Callable[[str], "Principal | None"]) -> None:
    """Add a resolver. Call from `AppConfig.ready()` of the owning app."""
    _resolvers.append((name, resolver))


async def resolve(token: str) -> "Principal | None":
    """Try every registered resolver. First match wins. None = unauthenticated."""
    for _name, fn in _resolvers:
        principal = await sync_to_async(fn, thread_sensitive=True)(token)
        if principal is not None:
            return principal
    return None


def registered_names() -> list[str]:
    """Introspection helper — used by `manage.py check` extensions if any."""
    return [name for name, _ in _resolvers]
