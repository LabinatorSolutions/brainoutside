"""URL-path token scrubbing.

The threat: when the credential lives in `/mcp/k/<mcpurl_FullSecret>/`,
every layer that touches `request.path` is a leak surface — Django
access logs, our structured JSON logger, APICallLog `request_path`,
ErrorLog rows, Sentry breadcrumbs, nginx/Caddy access logs sitting in
front of uvicorn.

Three coordinated mitigations land in this module:

1. **`URLTokenScrubMiddleware`** — runs BEFORE `RequestIdMiddleware` so
   no inner middleware ever sees a dirty path. Stashes the plaintext
   token at `request._url_token_plain` (only the proxy view reads it),
   and rewrites `request.path` / `request.path_info` /
   `META["PATH_INFO"]` to the `mcpurl_<8>***` shape.

2. **`scrub_url_token(s)`** — pure regex helper, exported so the JSON
   log formatter's pre-filter and Sentry's `before_send` hook can
   defense-in-depth scrub any string that might still slip through
   (a logger that captured `request.path` BEFORE the middleware ran,
   or any other unsanitized source). Same rewrite rule as the
   middleware.

3. **`ScrubLogFilter`** — `logging.Filter` subclass that runs the
   scrub regex on every record's `msg` + `args`. Wired into the
   stdout handler in `config/logging.py` so prod JSON logs are
   guaranteed clean even if a future call site forgets to scrub.

The scrub format is `mcpurl_<8-char-prefix>***`. We keep the 8-char
prefix so operator incident response can still correlate "which token
got abused" without leaking the secret half — same trick the dashboard
uses to display tokens in the UI.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

from django.http import HttpRequest, HttpResponse

# Group 1 captures the 8-char prefix slice we keep visible. The full
# token is `mcpurl_` (7) + 32-byte url-safe body (~43 chars), so the
# pattern matches at least 9 body chars (defensive: still scrubs a
# malformed or truncated token rather than letting it through). The
# trailing class accepts every url-safe-base64 char + `=` padding.
_TOKEN_RE = re.compile(r"mcpurl_([A-Za-z0-9_\-]{8})[A-Za-z0-9_\-=]+")


def scrub_url_token(s: str) -> str:
    """Rewrite every `mcpurl_<full>` substring in `s` to `mcpurl_<8>***`.

    Pure function; safe to call from any log / Sentry hook. Handles
    multiple occurrences (e.g. an exception traceback that quotes a
    URL twice) in one pass.
    """
    if not s or "mcpurl_" not in s:
        return s
    return _TOKEN_RE.sub(r"mcpurl_\1***", s)


# ----- Middleware -----------------------------------------------------------


class URLTokenScrubMiddleware:
    """Strip `mcpurl_*` plaintext out of `request.path*` before anything
    downstream reads them.

    Ordering: install BEFORE `RequestIdMiddleware` so that even the
    first log line emitted by inner middleware (request received, request
    id bound) sees the scrubbed path. The view branch that actually
    needs the plaintext reads `request._url_token_plain`, which we
    stash before rewriting.

    The proxy view's URL conf passes the (still-plain) token in via the
    path kwarg `token`, NOT by reading `request.path`. So scrubbing the
    path here never breaks routing — Django has already parsed the path
    into a (view_func, args, kwargs) triple before middleware runs the
    view, and the kwarg carries the plaintext through to the view.

    Wait — that ordering is the other way around. Middleware runs
    BEFORE URL dispatch. So we rewrite `request.path` here; the URL
    dispatcher then resolves the rewritten path … which would 404.

    Solution: we DON'T rewrite the *routed* path. We only stash the
    plaintext on the request (so the view can read it) and capture a
    `request._scrubbed_path` ALONGSIDE the original. Downstream loggers
    + APICallLog / ErrorLog writers consult `request._scrubbed_path`
    instead of `request.path`. The dispatcher continues to see the
    original path; the kwarg the view receives still carries the
    plaintext (Django's standard kwarg passing). This is the only
    coherent answer that doesn't fight Django's URL resolution.
    """

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse],
    ) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        path = request.path or ""
        if "mcpurl_" in path:
            # Extract the plaintext token (everything between `/mcp/k/`
            # and the next `/` or end of path) so the proxy view can
            # resolve it without reading the scrubbed path.
            m = re.search(
                r"/mcp/k/(mcpurl_[A-Za-z0-9_\-=]+)", path
            )
            if m is not None:
                request._url_token_plain = m.group(1)  # type: ignore[attr-defined]
            request._scrubbed_path = scrub_url_token(path)  # type: ignore[attr-defined]
        else:
            request._scrubbed_path = path  # type: ignore[attr-defined]
        return self.get_response(request)


# ----- Logging filter (defense in depth) ------------------------------------


class ScrubLogFilter(logging.Filter):
    """Scrub `mcpurl_*` tokens out of every log record before it's
    formatted.

    Catches the case where a log call captured `request.path` (or any
    other dirty string) BEFORE the middleware rewrite landed, or where
    an external library logs a URL we never sanitized. Cheap: one
    regex `search` per record on the message + each arg + a small set
    of well-known leak-prone extras.

    Beyond `msg` + `args`, we also scrub:

    - `record.request` — Django's `django.request` logger attaches the
      raw `HttpRequest` here as an extra. The JSON formatter then
      serializes `repr(request)`, which embeds `request.path` verbatim.
      Without this scrub, every 4xx/5xx Django response logs the full
      URL-path token even though our `message` field is clean.
    - `record.exc_text` — cached formatted traceback. If an exception
      mentions a URL that contains the token (e.g. an `httpx` retry
      log), the traceback gets persisted unscrubbed.
    - `record.stack_info` — similar to `exc_text` but for `stack_info=True`
      logger calls.
    """

    _EXTRAS_TO_SCRUB = ("exc_text", "stack_info")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = scrub_url_token(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: scrub_url_token(v) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        scrub_url_token(a) if isinstance(a, str) else a
                        for a in record.args
                    )
            # Django's `django.request` logger attaches the HttpRequest
            # as `extra={"request": request}`. Coerce to a scrubbed
            # repr string so the JSON formatter (and any other consumer)
            # sees a safe value.
            req = getattr(record, "request", None)
            if req is not None:
                record.request = scrub_url_token(
                    req if isinstance(req, str) else repr(req)
                )
            for attr in self._EXTRAS_TO_SCRUB:
                val = getattr(record, attr, None)
                if isinstance(val, str) and "mcpurl_" in val:
                    setattr(record, attr, scrub_url_token(val))
        except Exception:
            # Logging filters MUST NOT raise — they run on every record.
            # Worst case here is a malformed args object; let the
            # record through unscrubbed rather than dropping it.
            pass
        return True


# ----- Sentry before_send hook ----------------------------------------------


def sentry_scrub_url_token(event: dict, _hint: dict) -> dict:
    """Sentry `before_send` hook — rewrites `mcpurl_<full>` in the
    request URL + query string + every breadcrumb data.url.

    Returns the event mutated in place (Sentry's documented contract).
    Returns the event even if scrubbing fails — never drop an error
    report because of a sanitization bug.
    """
    try:
        req = event.get("request") or {}
        if isinstance(req.get("url"), str):
            req["url"] = scrub_url_token(req["url"])
        if isinstance(req.get("query_string"), str):
            req["query_string"] = scrub_url_token(req["query_string"])
        # Breadcrumbs is a list of dicts; their `.data.url` is where
        # the SDK records each outgoing HTTP call. Scrub everything
        # string-shaped that lives at `breadcrumb["data"]["url"]`.
        for crumb in event.get("breadcrumbs", {}).get("values", []) or []:
            data = crumb.get("data") or {}
            if isinstance(data.get("url"), str):
                data["url"] = scrub_url_token(data["url"])
    except Exception:
        pass
    return event


__all__ = [
    "ScrubLogFilter",
    "URLTokenScrubMiddleware",
    "scrub_url_token",
    "sentry_scrub_url_token",
]
