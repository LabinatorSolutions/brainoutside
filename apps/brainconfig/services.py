"""Typed accessors over the encrypted AppSetting store.

Resolution order for every key: DB setting → env var of the same name →
registry default. Empty strings read as unset at every layer (Coolify
injects compose ``${VAR}`` as "" — never distinguish unset/empty).

The registry below is the single source of truth for what the Settings
page renders and what SdkRunner reads (PLAN.md §3 `apps/brainconfig`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings as dj_settings

from apps.events.models import emit

from .models import AppSetting

# SDK run kinds that carry their own model/budget knobs. `test_connection`
# deliberately reuses the reader settings — it is a ping, not a kind.
SDK_KINDS = ("reader", "feeder")


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    help: str
    default: str = ""
    secret: bool = False  # write-only in the UI; only "set/unset" is shown


REGISTRY: tuple[SettingSpec, ...] = (
    SettingSpec(
        "ANTHROPIC_API_KEY",
        "Anthropic credential",
        "Either a workspace-scoped API key (sk-ant-api…, console spend "
        "limit per PLAN.md §9) or a Claude subscription token from "
        "`claude setup-token` (sk-ant-oat…) — detected automatically.",
        secret=True,
    ),
    SettingSpec(
        "CLAUDE_MODEL_READER",
        "Reader model",
        "Model for assemble-context / chat. Retrieval defaults to a "
        "Sonnet-class model (PLAN.md §7).",
        default="claude-sonnet-5",
    ),
    SettingSpec(
        "CLAUDE_MODEL_FEEDER",
        "Feeder model",
        "Model for feed extraction — may be a bigger model than the reader.",
        default="claude-opus-5",
    ),
    SettingSpec(
        "MAX_BUDGET_USD_READER",
        "Reader budget (USD/run)",
        "Soft per-run cap, checked between turns by the SDK.",
        default="0.50",
    ),
    SettingSpec(
        "MAX_BUDGET_USD_FEEDER",
        "Feeder budget (USD/run)",
        "Soft per-run cap for feed extraction.",
        default="1.00",
    ),
    SettingSpec(
        "MAX_TURNS_READER",
        "Reader max turns",
        "Hard cap on agentic turns per reader run.",
        default="15",
    ),
    SettingSpec(
        "MAX_TURNS_FEEDER",
        "Feeder max turns",
        "Hard cap on agentic turns per feeder run.",
        default="25",
    ),
    SettingSpec(
        "SDK_TIMEOUT_SECONDS",
        "Run timeout (seconds)",
        "Wall-clock ceiling per SDK run; the subprocess is killed past it.",
        default="300",
    ),
    SettingSpec(
        "DAILY_COST_CAP",
        "Daily cost cap (USD)",
        "Circuit breaker: SdkRunner refuses to start once today's summed "
        "cost exceeds this (admin-exemptable, grill C16). Set 0 to "
        "disable — reasonable on subscription auth, where cost figures "
        "are synthetic CLI estimates, not billed spend. Clearing restores "
        "the default.",
        default="10.00",
    ),
    SettingSpec(
        "BRAIN_REPO_URL",
        "Brain repo URL",
        "Git remote of the mind. Env/compose value wins when set.",
    ),
    SettingSpec(
        "GITHUB_WEBHOOK_SECRET",
        "GitHub webhook secret",
        "HMAC secret for POST /webhooks/github. Empty = webhook disabled.",
        secret=True,
    ),
)

_SPECS = {s.key: s for s in REGISTRY}


def get(key: str) -> str:
    """Effective value: DB → env → registry default. "" reads as unset."""
    spec = _SPECS.get(key)
    row = AppSetting.objects.filter(key=key).first()
    if row is not None and row.value.strip():
        return row.value.strip()
    env_val = (os.environ.get(key) or "").strip()
    if env_val:
        return env_val
    return spec.default if spec else ""


def is_db_set(key: str) -> bool:
    row = AppSetting.objects.filter(key=key).first()
    return bool(row is not None and row.value.strip())


def set_value(key: str, value: str, *, actor=None) -> None:
    """Persist (encrypted) and emit a `settings_change` event.

    The event never carries the value — only which key changed (secrets!).
    """
    row, _ = AppSetting.objects.get_or_create(key=key)
    row.value = value.strip()
    row.updated_by = actor
    row.save()
    emit("settings_change", key=key, cleared=not value.strip())


# ---- typed accessors (what SdkRunner consumes) ---------------------------


def anthropic_api_key() -> str:
    return get("ANTHROPIC_API_KEY")


def model_for(kind: str) -> str:
    return get(f"CLAUDE_MODEL_{_kind(kind)}")


def max_budget_usd(kind: str) -> float:
    return _as_float(get(f"MAX_BUDGET_USD_{_kind(kind)}"), 0.50)


def max_turns(kind: str) -> int:
    try:
        return max(1, int(get(f"MAX_TURNS_{_kind(kind)}")))
    except ValueError:
        return 15


def sdk_timeout_seconds() -> int:
    try:
        return max(30, int(get("SDK_TIMEOUT_SECONDS")))
    except ValueError:
        return 300


def daily_cost_cap() -> Decimal | None:
    """The breaker threshold, or None when disabled (value ≤ 0).

    Disabling is always explicit: an unparseable or CLEARED value falls
    back to the safe default, never to "off"."""
    try:
        cap = Decimal(get("DAILY_COST_CAP"))
    except InvalidOperation:
        return Decimal("10.00")
    return None if cap <= 0 else cap


def _kind(kind: str) -> str:
    k = kind.upper()
    if k not in {s.upper() for s in SDK_KINDS}:
        raise ValueError(f"unknown SDK kind: {kind}")
    return k


def _as_float(raw: str, fallback: float) -> float:
    try:
        return float(raw)
    except ValueError:
        return fallback
