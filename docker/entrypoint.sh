#!/usr/bin/env bash
# Entrypoint for all three roles. Starts as root only long enough to fix
# /data ownership (named volumes are created root-owned; the app runs as
# uid 1000 — PLAN.md grill A5c), then drops privileges via gosu.
set -euo pipefail

ROLE="${1:-web}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data/brain-repo /data/brain-views
    chown -R app:app /data
    exec gosu app "$0" "$ROLE"
fi

cd /app

case "$ROLE" in
  web)
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    exec gunicorn config.asgi:application \
        -k uvicorn.workers.UvicornWorker \
        --bind 0.0.0.0:8000 \
        --workers "${WEB_CONCURRENCY:-3}" \
        --graceful-timeout "${WEB_GRACEFUL_TIMEOUT:-30}" \
        --timeout "${WEB_TIMEOUT:-60}"
    ;;
  mcp)
    # FastMCP loopback subprocess; the web container's /mcp proxy targets
    # this over the compose network with MCP_LOOPBACK_SECRET auth.
    exec python -m apps.core.mcp.server
    ;;
  worker)
    # django-q2 cluster — feed extraction only (PLAN.md §7); ack timeout
    # > task timeout is validated at boot (double-billing guard, A11).
    exec python manage.py qcluster
    ;;
  *)
    exec "$@"
    ;;
esac
