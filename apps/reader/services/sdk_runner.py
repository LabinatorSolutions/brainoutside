"""SdkRunner — the single gate every Claude Agent SDK call goes through.

Responsibilities (PLAN.md §7):
- **Lockdown**: deny-by-default tool policy scoped to one tier snapshot.
  `Read` is allowed only inside the caller-tier's snapshot dir, `Grep`/
  `Glob` likewise via cwd; everything else (Bash, Write, Edit, WebFetch,
  WebSearch, Task…) is disallowed BY NAME so the tools leave context
  entirely. `setting_sources=[]` + `strict_mcp_config=True` mean nothing
  auto-loads from ~/.claude or any repo .claude/ (grill C4, C12a).
- **Ledger**: an SdkOperation row is written BEFORE the run (`ok=None`)
  and finalized after, so killed runs are never invisible (grill C6).
- **Budgets**: per-kind `max_budget_usd` (soft, SDK-enforced between
  turns) + `max_turns` + wall-clock timeout with subprocess kill as the
  hard stop, plus the daily circuit breaker (grill C5/C16).
- **Degraded mode**: SDK/API failures map to `ok=False` + `error_class`;
  callers fail fast, never auto-retry chat/assemble (grill C21).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Awaitable

from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.brainconfig import services as config
from apps.events.models import SdkOperation

log = logging.getLogger(__name__)

# Denied by name so their schemas never enter the agent's context (§7).
DENIED_TOOLS = [
    "Bash",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Task",
    "Agent",
    "TodoWrite",
    "KillShell",
    "BashOutput",
]


class SdkRunnerError(Exception):
    """Base for run-refusals and failures the caller should surface."""


class NotConfigured(SdkRunnerError):
    """No ANTHROPIC_API_KEY set (DB setting or env)."""


class DailyCapExceeded(SdkRunnerError):
    """Circuit breaker: today's summed cost is over DAILY_COST_CAP."""


@dataclass
class RunResult:
    ok: bool
    text: str
    model: str = ""
    duration_ms: int | None = None
    num_turns: int | None = None
    cost_usd: float | None = None
    usage: dict = field(default_factory=dict)
    error_class: str = ""
    operation_id: int | None = None
    # Schema-validated object when the run used `output_format` (C9).
    structured_output: object = None


def today_cost_usd() -> float:
    """Sum of CLI cost estimates for today's operations (display-only
    numbers, but good enough for a circuit breaker — grill C23)."""
    from django.db.models import Sum

    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    total = SdkOperation.objects.filter(created_at__gte=start).aggregate(
        s=Sum("cost_usd")
    )["s"]
    return float(total or 0)


def _auth_env(api_key: str) -> dict[str, str]:
    """Map the stored credential to the env var the CLI expects.

    Console API keys (sk-ant-api…) belong in ANTHROPIC_API_KEY. Claude
    subscription tokens from `claude setup-token` (sk-ant-oat…) must go
    in CLAUDE_CODE_OAUTH_TOKEN instead — in the API-key slot the API
    401s them ("Invalid API key"), while the CLI accepts them natively
    through the OAuth path.
    """
    if api_key.startswith("sk-ant-oat"):
        return {"CLAUDE_CODE_OAUTH_TOKEN": api_key}
    return {"ANTHROPIC_API_KEY": api_key}


def _check_gates(*, exempt_daily_cap: bool) -> str:
    api_key = config.anthropic_api_key()
    if not api_key:
        raise NotConfigured("ANTHROPIC_API_KEY is not configured")
    cap = config.daily_cost_cap()  # None = breaker disabled (cap set to 0)
    if not exempt_daily_cap and cap is not None and today_cost_usd() >= float(cap):
        raise DailyCapExceeded(
            f"daily cost cap {cap} USD reached — raise DAILY_COST_CAP, "
            "set it to 0 to disable the breaker, or wait for tomorrow"
        )
    return api_key


def _snapshot_options(tier: str, api_key: str, kind: str, append_prompt: str, output_format=None):
    """ClaudeAgentOptions locked to one tier snapshot (§7 'the real config')."""
    from claude_agent_sdk import ClaudeAgentOptions

    from apps.brain.services import snapshots

    tier_path = snapshots.tier_dir(tier)
    return ClaudeAgentOptions(
        cwd=str(tier_path),
        permission_mode="dontAsk",  # deny anything not pre-approved
        allowed_tools=[f"Read({tier_path.as_posix()}/**)", "Grep", "Glob"],
        disallowed_tools=DENIED_TOOLS,
        setting_sources=[],
        strict_mcp_config=True,
        system_prompt={"type": "preset", "preset": "claude_code", "append": append_prompt},
        model=config.model_for(kind),
        max_turns=config.max_turns(kind),
        max_budget_usd=config.max_budget_usd(kind),
        env=_auth_env(api_key),
        include_partial_messages=False,
        output_format=output_format,
    )


async def _drain(prompt: str, options) -> RunResult:
    """Run one query() to completion and fold it into a RunResult."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

    chunks: list[str] = []
    model = ""
    result: ResultMessage | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            model = message.model or model
            for block in message.content:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
        elif isinstance(message, ResultMessage):
            result = message

    if result is None:
        return RunResult(ok=False, text="", error_class="NoResultMessage")
    usage = result.usage or {}
    # ResultMessage has no `model` field; model_usage keys carry the
    # resolved model id(s) — prefer that over the assistant echo.
    if result.model_usage:
        model = ", ".join(sorted(result.model_usage))
    return RunResult(
        ok=not result.is_error,
        text="".join(chunks).strip() or str(result.result or ""),
        model=model,
        duration_ms=result.duration_ms,
        num_turns=result.num_turns,
        cost_usd=result.total_cost_usd,
        usage=usage,
        error_class="" if not result.is_error else (result.subtype or "sdk_error"),
        structured_output=result.structured_output,
    )


async def _run_ledgered(
    *,
    kind: str,
    prompt: str,
    options,
    prompt_hash_input: str,
    timeout_s: int,
    subject=None,
) -> RunResult:
    """Row-before-run wrapper: SdkOperation is created before the SDK is
    touched and finalized in `finally`, whatever happens. `subject` (a
    model instance, e.g. the Feed being extracted) fills the generic FK."""

    def _create_op():
        extra = {}
        if subject is not None:
            from django.contrib.contenttypes.models import ContentType

            extra = {
                "subject_type": ContentType.objects.get_for_model(subject),
                "subject_id": str(subject.pk),
            }
        return SdkOperation.objects.create(
            kind=kind,
            prompt_hash=hashlib.sha256(prompt_hash_input.encode()).hexdigest(),
            **extra,
        )

    op = await sync_to_async(_create_op)()
    run = RunResult(ok=False, text="", error_class="Unknown")
    try:
        run = await asyncio.wait_for(_drain(prompt, options), timeout=timeout_s)
    except asyncio.TimeoutError:
        run = RunResult(ok=False, text="", error_class="Timeout")
    except Exception as exc:  # SDK/transport errors → degraded, never raise raw
        log.exception("sdk runner: %s run failed", kind)
        label = exc.__class__.__name__
        if label == "Exception":  # SDK raises bare Exception for CLI errors
            label = f"CliError: {str(exc)[:100]}"
        run = RunResult(ok=False, text="", error_class=label)
    finally:
        op.finished_at = timezone.now()
        op.ok = run.ok
        op.error_class = run.error_class
        op.model = run.model
        op.duration_ms = run.duration_ms
        op.num_turns = run.num_turns
        op.cost_usd = run.cost_usd
        op.input_tokens = run.usage.get("input_tokens")
        op.output_tokens = run.usage.get("output_tokens")
        op.cache_read_tokens = run.usage.get("cache_read_input_tokens")
        op.cache_write_tokens = run.usage.get("cache_creation_input_tokens")
        await sync_to_async(op.save)()
    run.operation_id = op.id
    return run


async def test_connection_async(*, exempt_daily_cap: bool = True) -> RunResult:
    """Minimal SDK ping for the Settings page 'Test connection' button.

    No tools, one turn, public-tier cwd — proves the whole chain works:
    key → CLI subprocess → API → usage accounting. Admin-triggered, so it
    is daily-cap-exempt by default (an operator debugging an outage must
    not be locked out by the breaker they're investigating).
    """
    from claude_agent_sdk import ClaudeAgentOptions

    from apps.brain.services import snapshots

    api_key = await sync_to_async(_check_gates)(exempt_daily_cap=exempt_daily_cap)
    model = await sync_to_async(config.model_for)("reader")
    tier_path = snapshots.tier_dir("public")
    cwd = str(tier_path if tier_path.is_dir() else tier_path.parent)
    options = ClaudeAgentOptions(
        cwd=cwd,
        permission_mode="dontAsk",
        allowed_tools=[],
        disallowed_tools=DENIED_TOOLS + ["Read", "Grep", "Glob"],
        setting_sources=[],
        strict_mcp_config=True,
        system_prompt="You are a connectivity probe. Reply with exactly: OK",
        model=model,
        max_turns=1,
        env={
            **_auth_env(api_key),
            # A ping must fail fast: one retry, short per-request timeout —
            # a bad key surfaces as an auth error, not a 2-minute hang.
            "API_TIMEOUT_MS": "30000",
            "CLAUDE_CODE_MAX_RETRIES": "1",
        },
    )
    prompt = "Reply with exactly: OK"
    return await _run_ledgered(
        kind="test_connection",
        prompt=prompt,
        options=options,
        prompt_hash_input=f"test_connection|{model}|{prompt}",
        timeout_s=90,
    )


def run_sync(coro: Awaitable[RunResult]) -> RunResult:
    """Run an SDK coroutine to completion from sync code — safely.

    A bare `asyncio.run()` breaks inside Django's middleware bridge: the
    vendored stack has async-only middlewares, so even under WSGI every
    sync view runs inside `async_to_sync`, which binds a
    CurrentThreadExecutor to the request thread. The coroutine's
    `sync_to_async(thread_sensitive=True)` hops then try to submit back
    onto that same thread while it's blocked driving the new loop →
    "You cannot submit onto CurrentThreadExecutor from its own thread".
    A fresh thread carries no executor binding, so the hops fall through
    to asgiref's global sync executor as intended.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def test_connection(**kwargs) -> RunResult:
    """Sync facade for classic Django views."""
    return run_sync(test_connection_async(**kwargs))


async def run_agent_async(
    *,
    kind: str,
    tier: str,
    prompt: str,
    append_system: str,
    output_format: dict | None = None,
    subject=None,
) -> RunResult:
    """Generic tier-locked agent run — the M2/M3 entry point.

    `kind` is an SDK kind from brainconfig ("reader"/"feeder"); the ledger
    row records the operational kind derived from it. `output_format`
    (Messages-API json_schema shape) makes the run return
    `RunResult.structured_output` (grill C9); `subject` links the ledger
    row to what triggered the run.
    """
    api_key = await sync_to_async(_check_gates)(exempt_daily_cap=False)
    options = await sync_to_async(_snapshot_options)(
        tier, api_key, kind, append_system, output_format
    )
    ledger_kind = "assemble_context" if kind == "reader" else "feed_extraction"
    timeout_s = await sync_to_async(config.sdk_timeout_seconds)()
    return await _run_ledgered(
        kind=ledger_kind,
        prompt=prompt,
        options=options,
        prompt_hash_input=f"{kind}|{tier}|{append_system}|{prompt}",
        timeout_s=timeout_s,
        subject=subject,
    )
