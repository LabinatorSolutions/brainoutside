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

from apps.core import maintenance as maintenance_mode
from apps.core.security.client_ip import client_ip
from apps.reader.services import sdk_runner

from . import services
from .models import AppSetting
from .nav import ops_context

# Display grouping for the Settings page — purely presentational, so it
# lives here rather than on SettingSpec (the registry is what SdkRunner
# and the wizard read; they have no use for page layout). `_save` iterates
# the registry, not this map, so a key missing here still saves — and
# `_sectioned` renders it in a trailing "Other settings" card, so it
# cannot silently drop out of the display path either.
_SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Branding", "", ("APP_NAME",)),
    (
        "Claude SDK",
        "",
        (
            "ANTHROPIC_API_KEY",
            "CLAUDE_MODEL_READER",
            "CLAUDE_MODEL_FEEDER",
            "MAX_BUDGET_USD_READER",
            "MAX_BUDGET_USD_FEEDER",
            "MAX_TURNS_READER",
            "MAX_TURNS_FEEDER",
            "SDK_TIMEOUT_SECONDS",
            "DAILY_COST_CAP",
        ),
    ),
    (
        "Brain repo",
        "env/compose wins for these keys",
        ("BRAIN_REPO_URL", "GITHUB_WEBHOOK_SECRET", "BRAIN_GIT_WRITE_PAT"),
    ),
)


def _sectioned(rows: list[dict]) -> list[dict]:
    """Group registry rows into the page's cards, registry order kept."""
    by_key = {row["spec"].key: row for row in rows}
    placed: set[str] = set()
    sections = []
    for title, note, keys in _SECTIONS:
        section_rows = [by_key[k] for k in keys if k in by_key]
        placed.update(k for k in keys if k in by_key)
        if section_rows:
            sections.append({"title": title, "note": note, "rows": section_rows})
    leftovers = [row for row in rows if row["spec"].key not in placed]
    if leftovers:
        sections.append({"title": "Other settings", "note": "", "rows": leftovers})
    return sections


@staff_member_required(login_url="login")
@require_http_methods(["GET", "POST"])
def settings_page(request):
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "test_connection":
            return _test_connection(request)
        if action == "maintenance":
            return _set_maintenance(request)
        _save(request)
        return redirect(request.path)

    rows = []
    db_rows = {r.key: r for r in AppSetting.objects.all()}
    for spec in services.REGISTRY:
        row = db_rows.get(spec.key)
        db_set = bool(row is not None and row.value.strip())
        source = services.source_of(spec.key)
        rows.append(
            {
                "spec": spec,
                "db_set": db_set,
                # Secrets are never echoed; non-secrets show the DB value only
                # (the effective value may come from env — shown separately).
                "db_value": "" if spec.secret else (row.value if row else ""),
                "effective": "••••••••" if spec.secret and services.get(spec.key) else services.get(spec.key),
                "source": source,
                # An `env_wins` key with a stored value that env is beating:
                # the row exists but is inert, and saying so is the whole
                # point of showing provenance at all.
                "shadowed": db_set and source == "env",
                "updated_at": row.updated_at if row else None,
            }
        )
    return render(
        request,
        "ops/settings.html",
        {
            "sections": _sectioned(rows),
            "today_cost": sdk_runner.today_cost_usd(),
            "test_result": request.session.pop("test_result", None),
            "maintenance_on": maintenance_mode.is_enabled(),
            "maintenance_message": maintenance_mode.get_message(),
            **ops_context(request),
        },
    )


def _set_maintenance(request):
    """Flip maintenance mode. The only way to reach the flag.

    It had a store, a cache, a middleware, a branded 503 page and an audit
    call, and no surface anywhere that could set it — so it was off
    forever. Lives on Settings rather than Health because it is a stored
    value, not an action on the world; Health is for re-clone and rotate.

    Turning it ON does not lock the operator out: `/ops/`, the Django
    admin, `LOGIN_URL`, logout and `/setup/` are all on the middleware's
    bypass list, so this page stays reachable to turn it back off.
    """
    enabled = request.POST.get("enabled") == "on"
    raw_message = (request.POST.get("maintenance_message") or "").strip()
    maintenance_mode.set_enabled(
        enabled,
        # Empty means "leave the current banner alone", matching the
        # store's own contract — not "blank the message".
        message=raw_message[:500] or None,
        actor=request.user,
        request_id=getattr(request, "request_id", "") or "",
        ip=client_ip(request),
    )
    if enabled:
        messages.success(
            request,
            "Maintenance mode is ON. Consumers get 503; you keep full access.",
        )
    else:
        messages.success(request, "Maintenance mode is OFF. The server is serving again.")
    return redirect(request.path)


def _save(request) -> None:
    changed: list[str] = []
    rejected: list[str] = []
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
        if spec.max_len and len(raw) > spec.max_len:
            # Reject rather than truncate: a silently shortened app name
            # looks like the save worked.
            rejected.append(f"{spec.key} (max {spec.max_len} characters)")
            continue
        if raw != services.get(spec.key) or not services.is_db_set(spec.key):
            services.set_value(spec.key, raw, actor=request.user)
            changed.append(spec.key)
    if rejected:
        messages.error(request, f"Too long, not saved: {', '.join(rejected)}")
    if changed:
        messages.success(request, f"Saved: {', '.join(changed)}")
    elif not rejected:
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
