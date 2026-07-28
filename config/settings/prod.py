"""Production settings: fail-fast on missing or default-value secrets.

Phase 9.4.4 admin-URL hardening + SECRET_KEY + ALLOWED_HOSTS validation
lives in `Settings.assert_prod_safe()` — called below at module load so
boot fails immediately rather than at first request.

Phase 11.4 added `DATABASE_URL` parsing: when set to a `postgres://...`
URL (the canonical 12-factor shape), prod swaps the SQLite default from
base.py for psycopg + connection pooling. The parser is intentionally
inlined here (no `dj-database-url` dep) — the URL format is stable
enough that 30 lines of regex-free urllib.parse handles it.
"""
import os
from urllib.parse import urlparse

from .base import *  # noqa: F401,F403
from .env import settings as _env

_env.assert_prod_safe()

DEBUG = False

# ----- DATABASE_URL parsing ---------------------------
# Operators set DATABASE_URL=postgres://user:pass@host:5432/db. Without it,
# prod falls through to base.py's SQLite default which is FINE for a smoke
# test but never what a real deployment wants. The Q2 default broker is also
# the ORM (Postgres-backed), so Q2 inherits this DB automatically.
if _env.DATABASE_URL and _env.DATABASE_URL.startswith(("postgres://", "postgresql://")):
    _db = urlparse(_env.DATABASE_URL)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": (_db.path or "").lstrip("/"),
            "USER": _db.username or "",
            "PASSWORD": _db.password or "",
            "HOST": _db.hostname or "",
            "PORT": str(_db.port) if _db.port else "",
            # CONN_MAX_AGE keeps connections warm across requests. Default
            # 60s assumes either no pgbouncer or session-pool mode; set
            # DB_CONN_MAX_AGE=0 in `.env` when fronting Postgres with
            # transaction-mode PgBouncer (see the opt-in `pgbouncer:` service
            # in docker-compose.yml + docs/SCALING.md). Env-driven so
            # operators don't need a code edit to flip the value.
            "CONN_MAX_AGE": _env.DB_CONN_MAX_AGE,
            "OPTIONS": {
                "connect_timeout": 5,
                # `application_name` shows up in pg_stat_activity. Useful
                # for distinguishing web vs worker vs migration connections
                # in production troubleshooting.
                "application_name": _env.APP_NAME or "mcp-api-starter",
            },
        }
    }

# ----- TLS + cookie + HSTS hardening --------------------
# Force every request to HTTPS at the edge. SECURE_SSL_REDIRECT honors the
# `X-Forwarded-Proto` header set by SECURE_PROXY_SSL_HEADER (configure on
# your reverse proxy / load balancer). Without that header, every request
# would 301-redirect into a loop behind a TLS-terminating proxy.
#
# `SECURE_SSL_REDIRECT_ENABLED` env override — defaults to True so
# the prod posture stays safe-by-default. Operators running the full compose
# stack locally (no TLS terminator in front) set this to "0" so the dev
# browser hand-walk works without an HTTPS terminator. Don't disable in real
# prod; HSTS + secure cookies assume HTTPS-only.
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT_ENABLED", "1") not in ("0", "false", "False")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Secure cookies follow the same toggle — secure cookies + plain HTTP causes
# Django to drop session cookies on the floor, breaking magic-link sign-in.
SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT

# HSTS — `max-age=1y; includeSubDomains; preload`. Operators submitting to
# the HSTS preload list need all three. Test on a non-preloaded subdomain
# first; once preloaded, every browser refuses HTTP for the registered
# eTLD+1 indefinitely (the un-preload process takes weeks).
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
