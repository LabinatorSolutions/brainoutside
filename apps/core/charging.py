"""Credit-charge hook registry.

`apps.core.rest.EndpointView` needs to charge credits before a paid
endpoint runs and refund on failure — but apps.core cannot import
apps.billing (import-linter Contract 1: core has no reverse deps on
feature apps). This module is the bridge: apps.billing registers a
charge factory at boot, apps.core.rest calls it.

Same shape as `apps.core.bearer`: feature app calls `register(...)` from
its `AppConfig.ready()`, core dispatches without knowing the
implementation. If billing is disabled (or its app isn't installed),
`charge()` returns a no-op context manager so the rest of the request
path works unchanged — useful for tests that exercise `EndpointView`
without booting the billing app.

The factory contract is:

    factory(user, *, n, idempotency_key, endpoint_slug, request_id) -> AbstractContextManager

The yielded value is opaque to apps.core — it's the billing app's
`Charge` handle. `EndpointView` doesn't introspect it; it just keeps
the `with` block alive across `await ep.run(...)`.

Errors raised inside the with-block bubble up so the view can map
`InsufficientCreditsError` to a 402 response. The factory itself may
raise `InsufficientCreditsError` (or any subclass of `CreditError`)
when the consume can't be booked at all.
"""
from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, Callable, Iterator, Protocol

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


class ChargeFactory(Protocol):
    """Signature of the registered charge factory."""

    def __call__(
        self,
        user: "AbstractBaseUser",
        *,
        n: int,
        idempotency_key: str,
        endpoint_slug: str,
        request_id: str,
    ) -> AbstractContextManager[Any]: ...


_factory: ChargeFactory | None = None


def register(factory: ChargeFactory) -> None:
    """Install the credit-charge factory. Called from `BillingConfig.ready()`."""
    global _factory
    _factory = factory


def is_enabled() -> bool:
    """True when a charge factory is registered."""
    return _factory is not None


def charge(
    user: "AbstractBaseUser",
    *,
    n: int,
    idempotency_key: str,
    endpoint_slug: str,
    request_id: str,
) -> AbstractContextManager[Any]:
    """Open a credit-charge context. No-op when billing is disabled.

    Caller responsibility:
      - Ensure `n > 0` before calling (the auth gate has already checked
        `spec.credits_cost > 0`).
      - Map `InsufficientCreditsError` raised here to a 402 response.
    """
    if _factory is None or n <= 0:
        return _noop_charge()
    return _factory(
        user,
        n=n,
        idempotency_key=idempotency_key,
        endpoint_slug=endpoint_slug,
        request_id=request_id,
    )


@contextmanager
def _noop_charge() -> Iterator[None]:
    yield None


# Re-export the canonical idempotency-key helper. apps.core.rest builds
# the key (it has the request_id + slug + credential) and passes it in,
# so that helper has to be reachable without an apps.billing import.
# We don't actually duplicate the implementation here — we expose a
# stub that `BillingConfig.ready()` overwrites by passing the real
# function via `set_idempotency_key_fn`. Keeps apps.core import-clean.


_make_idempotency_key: Callable[..., str] | None = None


def set_idempotency_key_fn(fn: Callable[..., str]) -> None:
    """Install the canonical idempotency-key formula. Called from billing's ready()."""
    global _make_idempotency_key
    _make_idempotency_key = fn


def make_idempotency_key(
    *,
    credential_id: int | str | None,
    request_id: str,
    endpoint_slug: str,
    amount: int,
) -> str:
    """Delegate to the registered formula. Falls back to a deterministic
    string if billing isn't installed (rare — only matters in unit tests
    of EndpointView that mock the charge factory)."""
    if _make_idempotency_key is None:
        parts = [
            str(credential_id) if credential_id is not None else "none",
            request_id,
            endpoint_slug,
            str(int(amount)),
        ]
        return ":".join(parts)
    return _make_idempotency_key(
        credential_id=credential_id,
        request_id=request_id,
        endpoint_slug=endpoint_slug,
        amount=amount,
    )
