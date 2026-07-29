"""Ops UI: feed queue + propose form (PLAN.md §8, M2.1 slice).

Staff-only, mounted under the admin-panel prefix via brainconfig.urls.
This page is the UI channel of the write door: the form calls the same
intake service as REST/MCP. The approval actions (diff view, edit,
approve/reject) land in M2.4 — for now the queue lists and inspects.
"""
from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.brainconfig.nav import ops_context

from .models import Feed
from .services import feeder, intake

SOURCE_KINDS = ("yt", "blog", "x", "newsletter", "repo", "doc", "thought")


@staff_member_required(login_url="login")
@require_http_methods(["GET", "POST"])
def queue(request):
    if request.method == "POST":
        try:
            feed = intake.propose(
                channel="ui",
                source_kind=request.POST.get("source_kind", ""),
                title=request.POST.get("title", ""),
                source_url=request.POST.get("url", ""),
                content=request.POST.get("content", ""),
                notes=request.POST.get("notes", ""),
                source_id=request.POST.get("source_id", ""),
            )
        except intake.FeedRejected as exc:
            messages.error(request, str(exc))
        else:
            fetch = feed.raw_payload.get("fetch")
            if fetch and not fetch.get("ok"):
                messages.warning(
                    request,
                    f"Captured {feed.source_id}, but the URL fetch failed "
                    f"({fetch.get('error', 'unknown error')}). Paste the content, or let extraction retry.",
                )
            else:
                messages.success(request, f"Feed {feed.source_id} is pending.")
        return redirect(request.path)

    f_status = request.GET.get("status", "")
    feeds = Feed.objects.all()
    if f_status:
        feeds = feeds.filter(status=f_status)
    return render(
        request,
        "ops/feeds.html",
        {
            "feeds": feeds[:200],
            "total": Feed.objects.count(),
            "pending_count": Feed.objects.filter(status="pending").count(),
            "statuses": [s for s, _ in Feed.STATUSES],
            "f_status": f_status,
            "source_kinds": SOURCE_KINDS,
            "payload_max_kb": intake.payload_max_bytes() // 1024,
            **ops_context(request),
        },
    )


@staff_member_required(login_url="login")
@require_http_methods(["GET", "POST"])
def feed_detail(request, pk: int):
    feed = Feed.objects.filter(pk=pk).first()
    if feed is None:
        raise Http404
    if request.method == "POST" and request.POST.get("action") == "extract":
        if feed.status != "pending":
            messages.error(request, f"Feed is {feed.status} — extraction runs on pending feeds only.")
        elif feeder.enqueue_extraction(feed):
            messages.success(request, "Extraction queued — the worker will fill the proposal.")
        else:
            messages.error(request, "Could not reach the worker queue — see the error on the feed.")
        return redirect(request.path)
    payload = feed.raw_payload or {}
    return render(
        request,
        "ops/feed_detail.html",
        {
            "feed": feed,
            "payload": payload,
            "fetch": payload.get("fetch"),
            "content": payload.get("content", ""),
            "proposal_json": (
                json.dumps(feed.proposal, indent=2, ensure_ascii=False) if feed.proposal else ""
            ),
            **ops_context(request),
        },
    )
