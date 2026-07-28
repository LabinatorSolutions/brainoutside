"""Development settings: DEBUG on, SQLite, plain static storage."""
from config.logging import build_logging

from .base import *  # noqa: F401,F403
from .env import settings as _env

DEBUG = True

LOGGING = build_logging(debug=True)

# Manifest storage needs collectstatic; dev serves straight from static/.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

ALLOWED_HOSTS = list({*ALLOWED_HOSTS, "localhost", "127.0.0.1", "0.0.0.0"})  # noqa: F405

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSP_REPORT_ONLY = True if _env.CSP_REPORT_ONLY is None else _env.CSP_REPORT_ONLY
