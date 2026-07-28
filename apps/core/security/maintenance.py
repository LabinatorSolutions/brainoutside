"""Maintenance-mode middleware.

When `apps.core.maintenance.is_enabled()` returns True, every request
is short-circuited to a branded 503 page UNLESS:

- the request path is on the bypass list (`/healthz`, `/readyz`,
  `/static/`, `/media/`),
- OR the request targets the admin URL prefix (so an operator who
  toggled it on can also toggle it back off — staff 2FA setup,
  Settings page, etc.),
- OR the resolved user is `is_staff` (so staff can keep using the
  user-facing site to verify their changes during the window).

Returns 503 with `Retry-After: 60` and a generic body so external
health checks see the right semantics.

Position in MIDDLEWARE: AFTER `AuthenticationMiddleware` so we can
read `request.user.is_staff`, BEFORE the request reaches user-facing
views. Sits below `OTPMiddleware` for parity with the rest of the
auth-aware chain — staff identity is the bypass condition, the 2FA
flag is irrelevant here.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string

from apps.core import maintenance

log = logging.getLogger(__name__)

_BYPASS_PREFIXES = (
    "/healthz",
    "/readyz",
    "/static/",
    "/media/",
    "/.well-known/",
)


def _admin_prefix() -> str:
    raw = getattr(settings, "ADMIN_PANEL_URL_PATH", "") or ""
    return ("/" + raw.strip("/") + "/") if raw else ""


def _django_admin_prefix() -> str:
    raw = getattr(settings, "DJANGO_ADMIN_URL_PATH", "") or ""
    return ("/" + raw.strip("/") + "/") if raw else ""


def _is_bypass_path(path: str) -> bool:
    if any(path.startswith(p) for p in _BYPASS_PREFIXES):
        return True
    admin = _admin_prefix()
    if admin and path.startswith(admin):
        return True
    django_admin = _django_admin_prefix()
    if django_admin and path.startswith(django_admin):
        return True
    # Auth flow stays open so staff can sign in to flip the flag back.
    if path.startswith("/auth/"):
        return True
    return False


def _user_is_staff(request: HttpRequest) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and getattr(user, "is_staff", False))


def _render_503(request: HttpRequest) -> HttpResponse:
    body = render_to_string(
        "errors/maintenance.html",
        {
            "APP_NAME": settings.APP_NAME,
            "maintenance_message": maintenance.get_message(),
        },
        request=request,
    )
    response = HttpResponse(body, status=503, content_type="text/html; charset=utf-8")
    response["Retry-After"] = "60"
    response["Cache-Control"] = "no-store"
    return response


class MaintenanceModeMiddleware:
    """Async-only. Returns 503 + branded page when the flag is on."""

    sync_capable = False
    async_capable = True

    def __init__(
        self,
        get_response: Callable[[HttpRequest], Awaitable[HttpResponse]],
    ) -> None:
        self.get_response = get_response
        markcoroutinefunction(self)
        if not iscoroutinefunction(get_response):
            raise RuntimeError(
                "MaintenanceModeMiddleware requires the async ASGI stack."
            )

    async def __call__(self, request: HttpRequest) -> HttpResponse:
        if not await sync_to_async(maintenance.is_enabled, thread_sensitive=True)():
            return await self.get_response(request)
        if _is_bypass_path(request.path or ""):
            return await self.get_response(request)
        # `request.user` is a SimpleLazyObject that hits the session DB
        # on first access — must run in a sync thread.
        is_staff = await sync_to_async(_user_is_staff, thread_sensitive=True)(request)
        if is_staff:
            return await self.get_response(request)
        return await sync_to_async(_render_503, thread_sensitive=True)(request)


__all__ = ["MaintenanceModeMiddleware"]
