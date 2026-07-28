"""Resilience primitives.

CircuitBreaker:    closed → open → half-open state machine
@retry:            exponential backoff for transient errors
with_timeout:      best-effort wall-clock timeout (logs over-run; HTTP libs
                   should pass an explicit `timeout=` argument for hard limits)

Phase 8.6 extension — every `CircuitBreaker(name=...)` registers itself in
the module-level `_BREAKERS` dict so `/readyz` can list every
named breaker's state in the response body, and `record_breaker_open` can
write an ErrorLog row when state flips to OPEN. The registry is exposed
via `iter_breakers()` so `apps.observability.health` can read it without
importing the call sites.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from functools import wraps
from typing import Callable, Iterator, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


class CircuitBreakerOpen(Exception):
    """Raised when calls are rejected because the breaker is open."""


# Module-level registry of every CircuitBreaker(name=...) — populated on
# `__init__`. apps.observability.health.run_all() reads via `iter_breakers()`
# so /readyz can surface breaker state alongside DB/Redis/MCP probes.
_BREAKERS: dict[str, "CircuitBreaker"] = {}
_BREAKERS_LOCK = threading.Lock()


def iter_breakers() -> list["CircuitBreaker"]:
    """Snapshot of every registered CircuitBreaker. Order is registration
    order, which is insertion order on Python 3.7+ dicts."""
    with _BREAKERS_LOCK:
        return list(_BREAKERS.values())


# Optional callback invoked when a breaker transitions to OPEN. Wired by
# `apps.observability.apps.ObservabilityConfig.ready()` to write an
# ErrorLog row (handled=True, exc_class="CircuitBreakerOpen", source="system").
# Kept as a hook to avoid apps.core importing apps.observability.
_OPEN_CALLBACK: Callable[["CircuitBreaker", BaseException], None] | None = None


def set_open_callback(
    cb: Callable[["CircuitBreaker", BaseException], None] | None,
) -> None:
    """Register a callback fired once when a breaker transitions to OPEN."""
    global _OPEN_CALLBACK
    _OPEN_CALLBACK = cb


class CircuitBreaker:
    """Thread-safe state machine guarding an outbound dependency.

    States:
      - closed:    normal — calls flow through; failures count up.
      - open:      rejecting — calls raise `CircuitBreakerOpen` immediately.
      - half-open: probing — one call allowed; success → closed, fail → open.

    Usage:
        stripe_breaker = CircuitBreaker("stripe", fail_max=5, reset_timeout=60)
        try:
            charge = stripe_breaker.call(stripe.Charge.create, amount=...)
        except CircuitBreakerOpen:
            return JsonResponse({"error": "payments_unavailable"}, status=503)
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, name: str, fail_max: int = 5, reset_timeout: float = 60.0) -> None:
        self.name = name
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._state = self.CLOSED
        self._fail_count = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()
        # register so /readyz can list us.
        with _BREAKERS_LOCK:
            _BREAKERS[name] = self

    @property
    def state(self) -> str:
        return self._state

    def cooldown_remaining_s(self) -> float:
        """Seconds until an open breaker enters half-open. 0.0 when closed
        or already past the reset window."""
        if self._state != self.OPEN or self._opened_at is None:
            return 0.0
        remaining = self.reset_timeout - (time.monotonic() - self._opened_at)
        return max(0.0, remaining)

    def reset(self) -> None:
        """Force-close the breaker. Used by tests; ops can call from shell
        when an upstream is known-good but the breaker is still cooling."""
        with self._lock:
            self._state = self.CLOSED
            self._fail_count = 0
            self._opened_at = None

    def call(self, fn: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:
            if self._state == self.OPEN:
                assert self._opened_at is not None
                if time.monotonic() - self._opened_at >= self.reset_timeout:
                    self._state = self.HALF_OPEN
                    logger.info("breaker.%s -> half_open", self.name)
                else:
                    raise CircuitBreakerOpen(
                        f"circuit breaker {self.name!r} is open"
                    )

        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            with self._lock:
                self._fail_count += 1
                state_flipped_open = False
                if self._state == self.HALF_OPEN or self._fail_count >= self.fail_max:
                    if self._state != self.OPEN:
                        state_flipped_open = True
                    self._state = self.OPEN
                    self._opened_at = time.monotonic()
                    logger.warning(
                        "breaker.%s -> open after %d failures",
                        self.name,
                        self._fail_count,
                    )
            # write ErrorLog (via the registered callback)
            # exactly once when state transitions to OPEN. We do this
            # outside the lock so a slow callback can't stall the breaker.
            if state_flipped_open and _OPEN_CALLBACK is not None:
                try:
                    _OPEN_CALLBACK(self, exc)
                except Exception:
                    logger.exception("breaker.%s open-callback raised", self.name)
            raise
        else:
            with self._lock:
                self._fail_count = 0
                if self._state != self.CLOSED:
                    self._state = self.CLOSED
                    self._opened_at = None
                    logger.info("breaker.%s -> closed", self.name)
            return result


def retry(
    attempts: int = 3,
    backoff: float = 0.5,
    max_backoff: float = 30.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Exponential-backoff retry decorator.

    Pass a narrower `exceptions` tuple to skip retries on user errors:
        @retry(attempts=4, exceptions=(IOError, ConnectionError))
        def fetch_url(url: str) -> bytes: ...
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            wait = backoff
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == attempts:
                        break
                    logger.warning(
                        "retry %s attempt %d/%d failed: %s; sleeping %.2fs",
                        fn.__name__,
                        attempt,
                        attempts,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                    wait = min(wait * 2, max_backoff)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


@contextmanager
def with_timeout(seconds: float, name: str = "operation") -> Iterator[None]:
    """Wall-clock guardrail. Logs a warning if the block exceeded `seconds`.

    NOT a hard kill — Python sync code can't be safely interrupted across
    platforms. For real timeouts on network I/O, pass `timeout=` to the
    underlying library (e.g. `requests.get(url, timeout=10)` — wrapped in
    `apps.core.http.safe_request` once Phase 9.7 lands).
    """
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        if elapsed > seconds:
            logger.warning(
                "with_timeout(%s): exceeded %.2fs (took %.2fs)",
                name,
                seconds,
                elapsed,
            )
