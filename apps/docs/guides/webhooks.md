# Webhooks

Subscribe a URL to events on your account; we POST you a signed
JSON payload when each event fires. Useful for keeping your billing
system in sync with credit grants, surfacing API-key revocations to
your audit log, etc.

## Subscribable events

| Event | Fires when |
|---|---|
| `subscription.activated` | A subscription starts (Stripe Checkout completes) |
| `subscription.updated` | Plan change or quantity change |
| `subscription.canceled` | Subscription cancellation takes effect |
| `credits.granted` | Credits added (purchase, period rollover, admin adjustment) |
| `credits.refunded` | Credits refunded |
| `api_keys.created` | A new API key is minted |
| `api_keys.revoked` | An API key is revoked |
| `api_keys.rotated` | An API key is rotated |
| `invoice.paid` | Stripe `invoice.paid` for a subscription renewal |
| `invoice.failed` | Stripe `invoice.payment_failed` |
| `webhooks.test.ping` | Synthetic test event (use the **Send test event** button on the dashboard to fire one) |

You can also subscribe with the wildcard `*` to receive everything,
or `<prefix>.*` (e.g. `credits.*` or `subscription.*`) to receive every
event in a family.

## Registering an endpoint

[/dashboard/webhooks/](/dashboard/webhooks/) → **Register a new
endpoint** → paste a URL + tick the events you want. We mint a signing
secret prefixed `whsec_` (Stripe/GitHub-style) and show it once — store
it in your receiver's env config.

## Signature

Every POST carries these headers:

```
X-Mcp-Signature: t=1735689600,v1=<hex hmac>
X-Mcp-Event:     credits.granted
X-Mcp-Event-Id:  0a3c...
X-Mcp-Attempt:   1
```

`X-Mcp-Event-Id` mirrors the `event_id` in the body — it's stable across
retries, so you can dedupe straight from the header without parsing the
body.

The signature is `HMAC-SHA256(secret, "<timestamp>.<raw body>")`.
Receivers should:

  1. Read `t` and verify it's within ±5 minutes of now (replay
     protection).
  2. Recompute the HMAC with the body they got.
  3. `hmac.compare_digest` against `v1`.

Python verifier:

```python
import hashlib, hmac, time

def verify(body: bytes, header: str, secret: str, max_age_s: int = 300) -> bool:
    if not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > max_age_s:
        return False
    msg = f"{ts}.".encode("utf-8") + body
    expected = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))
```

## Payload shape

```json
{
  "event_id": "0a3c...",
  "event_name": "credits.granted",
  "payload": {
    "user_id": 42,
    "amount": 1000,
    "new_balance": 5400,
    "reason": "package_purchase"
  },
  "attempt": 1
}
```

The `payload` shape varies per event — `credits.*` carries `new_balance`
so you can mirror the running total without a follow-up API call;
`subscription.*` carries `plan_slug` + `stripe_subscription_id`;
`api_keys.*` carries `key_id` and (on `created`) `prefix`.

`event_id` is stable across retries — the same id gets re-delivered if
the first attempt fails. Use it for idempotency on your side.

## Retry schedule

If your receiver returns non-2xx (or times out at 10s), we retry on:

```
60s → 5min → 30min → 2hr → 12hr → dead-letter
```

After 6 attempts the delivery flips to `dead`. You can replay any
failed or dead delivery from your dashboard (per-row Replay button on
[/dashboard/webhooks/](/dashboard/webhooks/) — failed/dead rows only).

## Testing

Use the **Send test event** button per endpoint on the dashboard. It
fires a synthetic `webhooks.test.ping` event that bypasses the
event-type matcher so you can verify your receiver wiring without
subscribing to a real event class. Useful before you flip on
`credits.granted` in production.

## Full receiver examples

Production-ready receivers in your stack — each covers HMAC verify,
the ±5-minute timestamp window, and the 2xx-fast / queue-then-process
pattern:

| Language / framework | Recipe |
|---|---|
| Python (Flask + FastAPI) | [{{ PUBLIC_REPO_URL }}/blob/main/docs/webhooks/verify-python.md]({{ PUBLIC_REPO_URL }}/blob/main/docs/webhooks/verify-python.md) |
| Node.js (Express) | [{{ PUBLIC_REPO_URL }}/blob/main/docs/webhooks/verify-nodejs.md]({{ PUBLIC_REPO_URL }}/blob/main/docs/webhooks/verify-nodejs.md) |
| Go (`net/http`) | [{{ PUBLIC_REPO_URL }}/blob/main/docs/webhooks/verify-go.md]({{ PUBLIC_REPO_URL }}/blob/main/docs/webhooks/verify-go.md) |
| PHP (vanilla) | [{{ PUBLIC_REPO_URL }}/blob/main/docs/webhooks/verify-php.md]({{ PUBLIC_REPO_URL }}/blob/main/docs/webhooks/verify-php.md) |

If the recipe links above show blank URLs, ask support for the
language-specific receiver code — we'll send it directly.

> Common gotcha: the HMAC is computed against the EXACT bytes we
> sent. If your framework re-serializes the JSON before your handler
> sees it (`express.json()`, `request.json`, `json.NewDecoder`), the
> signatures won't match. Grab the raw body BEFORE any JSON parse.
