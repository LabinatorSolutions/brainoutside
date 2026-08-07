"""Send an unconfigured server to its setup wizard.

Two distinct situations, deliberately handled differently:

- **No operator account exists.** The app is unowned: nobody can log in,
  nothing can be configured, and the only useful page is the wizard. Every
  human-facing route redirects there.
- **An account exists but setup is unfinished.** Only the ops UI
  redirects, and only for the signed-in operator. The rest of the app
  keeps its normal behaviour — a half-configured server should return its
  usual errors to API clients rather than 302 them into an HTML wizard.

Machine surfaces are never redirected. An API or MCP caller that gets a
302 to `/setup/` sees a confusing HTML body instead of the JSON error
that tells it what is actually wrong, and health checks that follow
redirects would report a broken server as healthy.
"""
from __future__ import annotations

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.conf import settings
from django.shortcuts import redirect

#: Prefixes that must answer normally even with nothing configured.
_EXEMPT_PREFIXES = (
    "/setup/",
    "/healthz",
    "/readyz",
    "/static/",
    "/media/",
    "/api/",
    "/mcp",
    "/webhooks/",
    "/_csp-report/",
    "/.well-known/",
    "/robots.txt",
    "/login/",
    "/logout/",
)


class SetupRequiredMiddleware:
    """Redirect to `/setup/` while the server is unusable or unfinished.

    Async-only, like every neighbour in MIDDLEWARE. This and WhiteNoise
    were the last two sync-only middlewares in the chain, and Django's
    per-middleware adaptation therefore wrapped the whole inner stack in
    `async_to_sync` — four boundary crossings and two pinned threadpool
    threads on every request. The exempt prefixes (which include `/api/`
    and `/mcp`, the long-running async surfaces) short-circuit on a pure
    path test with no DB read at all; only human-facing routes pay the
    one `sync_to_async` hop for the setup predicates.
    """

    sync_capable = False
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        markcoroutinefunction(self)
        if not iscoroutinefunction(get_response):
            raise RuntimeError(
                "SetupRequiredMiddleware requires the async ASGI stack."
            )
        self._ops_prefix = "/" + (settings.ADMIN_PANEL_URL_PATH or "ops/").strip("/") + "/"
        # The settings page is exempt from the unfinished-setup redirect.
        # Completion is DERIVED (see setup_state), so clearing a required
        # value — the ANTHROPIC_API_KEY "clear" checkbox on that very
        # page — used to flip `is_complete()` and eject the operator from
        # the whole ops UI, including the one page that edits stored
        # settings. Same rule as the maintenance-mode bypass list: the
        # switch you would use to undo a state must survive that state.
        # Everything else still routes to the wizard, which is built for
        # the incomplete case.
        self._settings_prefix = self._ops_prefix + "settings/"

    async def __call__(self, request):
        if any(request.path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await self.get_response(request)
        # The predicates and `request.user` (a SimpleLazyObject over the
        # session table) are both DB-bound — sync thread.
        target = await sync_to_async(self._redirect_target, thread_sensitive=True)(
            request
        )
        if target is not None:
            return target
        return await self.get_response(request)

    def _redirect_target(self, request):
        """The decision body, unchanged from the sync version: a redirect
        response, or None to pass through."""
        from apps.brainconfig import setup_state

        if setup_state.needs_first_admin():
            return redirect("setup:home")
        path = request.path
        if path.startswith(self._ops_prefix) and not path.startswith(
            self._settings_prefix
        ):
            user = getattr(request, "user", None)
            if (
                user is not None
                and user.is_authenticated
                and user.is_staff
                and not setup_state.is_complete()
            ):
                return redirect("setup:home")
        return None
