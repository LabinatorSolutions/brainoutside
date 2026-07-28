"""Ops Settings page (PLAN.md §8).

Session-authed, staff-only, mounted under the admin-panel prefix so the
IP-allowlist middleware and (in prod) the network boundary cover it.
Secrets are write-only: the form never echoes a stored secret — it shows
only set/unset and accepts a new value or an explicit clear.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.reader.services import sdk_runner

from . import services
from .models import AppSetting
from .nav import ops_context


@staff_member_required
@require_http_methods(["GET", "POST"])
def settings_page(request):
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "test_connection":
            return _test_connection(request)
        _save(request)
        return redirect(request.path)

    rows = []
    db_rows = {r.key: r for r in AppSetting.objects.all()}
    for spec in services.REGISTRY:
        row = db_rows.get(spec.key)
        db_set = bool(row is not None and row.value.strip())
        rows.append(
            {
                "spec": spec,
                "db_set": db_set,
                # Secrets are never echoed; non-secrets show the DB value only
                # (the effective value may come from env — shown separately).
                "db_value": "" if spec.secret else (row.value if row else ""),
                "effective": "••••••••" if spec.secret and services.get(spec.key) else services.get(spec.key),
                "updated_at": row.updated_at if row else None,
            }
        )
    return render(
        request,
        "ops/settings.html",
        {
            "rows": rows,
            "today_cost": sdk_runner.today_cost_usd(),
            "test_result": request.session.pop("test_result", None),
            **ops_context(request),
        },
    )


def _save(request) -> None:
    changed = []
    for spec in services.REGISTRY:
        clear = request.POST.get(f"clear__{spec.key}") == "on"
        raw = (request.POST.get(f"value__{spec.key}") or "").strip()
        if clear:
            if services.is_db_set(spec.key):
                services.set_value(spec.key, "", actor=request.user)
                changed.append(spec.key)
            continue
        if not raw:
            continue  # blank input = keep current (write-only secrets rely on this)
        if raw != services.get(spec.key) or not services.is_db_set(spec.key):
            services.set_value(spec.key, raw, actor=request.user)
            changed.append(spec.key)
    if changed:
        messages.success(request, f"Saved: {', '.join(changed)}")
    else:
        messages.info(request, "Nothing changed.")


def _test_connection(request):
    try:
        run = sdk_runner.test_connection()
    except sdk_runner.SdkRunnerError as exc:
        request.session["test_result"] = {"ok": False, "error": str(exc)}
        return redirect(request.path)
    request.session["test_result"] = {
        "ok": run.ok,
        "model": run.model,
        "duration_ms": run.duration_ms,
        "input_tokens": run.usage.get("input_tokens"),
        "output_tokens": run.usage.get("output_tokens"),
        "cost_usd": run.cost_usd,
        "text": run.text[:200],
        "error": run.error_class,
        "operation_id": run.operation_id,
    }
    return redirect(request.path)
