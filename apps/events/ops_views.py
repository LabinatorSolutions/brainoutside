"""Ops view: the background-task monitor.

Every fire-and-forget click (extract, approve) hands work to the Q2
worker; this page is where that work is visible — running SDK
operations (`ok=None` rows exist BEFORE the run, grill C6), feeds with
an extraction in flight, approvals mid-commit, queue depth, and recent
outcomes. A run older than the Q2 task timeout is flagged stale: the
worker died before finalizing it.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone

from apps.brainconfig.nav import ops_context
from apps.feeds.models import Feed

from .models import SdkOperation


@staff_member_required(login_url="login")
def tasks(request):
    stale_cutoff = timezone.now() - timedelta(seconds=int(settings.Q_CLUSTER["timeout"]) + 60)

    running = list(SdkOperation.objects.filter(ok__isnull=True)[:25])
    for op in running:
        op.is_stale = op.created_at < stale_cutoff

    extracting = [
        f
        for f in Feed.objects.filter(extract_queued_at__isnull=False)
        if f.extraction_in_flight
    ]
    approving = list(Feed.objects.filter(status="approving"))

    queue_depth = None
    try:
        from django_q.brokers import get_broker

        queue_depth = get_broker().queue_size()
    except Exception:  # broker down — the page still renders
        pass

    q_tasks = []
    try:
        from django_q.models import Task

        q_tasks = list(Task.objects.order_by("-started")[:15])
    except Exception:
        pass

    live = [op for op in running if not op.is_stale]
    busy = bool(live or extracting or approving)
    return render(
        request,
        "ops/tasks.html",
        {
            "running": running,
            "recent": SdkOperation.objects.filter(ok__isnull=False)[:25],
            "extracting": extracting,
            "approving": approving,
            "queue_depth": queue_depth,
            "q_tasks": q_tasks,
            "busy": busy,
            **ops_context(request),
        },
    )
