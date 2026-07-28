# Rate limits

Every authenticated caller is rate-limited with a **token bucket** to
keep one runaway script from starving everyone else. Limits are
enforced **per (user, endpoint)** — all of your API keys and OAuth
tokens share one bucket per endpoint, so minting more keys does not
multiply your limit. Anonymous traffic (against free endpoints)
shares a per-IP bucket.

## Defaults

| Tier | Requests/minute |
|---|---|
| Free | 30 |
| Pro | 120 |

Limits live on the user's **plan**. Operators can adjust via the admin
plans page; per-endpoint overrides happen at the `EndpointCost` level
(an endpoint that's cheaper or more expensive than the plan default
can carry its own ceiling).

## Headers on 429

When a request is throttled, we attach:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 12
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 0
Content-Type: application/json

{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Per-minute limit exceeded; retry in 12s.",
    "limit_per_min": 120,
    "retry_after_s": 12
  }
}
```

  - `Retry-After` (seconds) — sleep at least this long before retrying.
  - `X-RateLimit-Limit` — the bucket size for this caller + endpoint.
  - `X-RateLimit-Remaining` — tokens left (always `0` on a 429).
  - `error.retry_after_s` mirrors `Retry-After` in the JSON body for
    clients that find headers awkward to read.

Successful (non-429) responses do **not** carry `X-RateLimit-*`
headers — read your current allowance off the
[/dashboard/usage/](/dashboard/usage/) page instead.

## Backing off

Read `Retry-After` and sleep for at least that many seconds. A simple
backoff that handles 429s cleanly:

```python
import time, requests

def call_with_backoff(url, headers, body, max_retries=5):
    for attempt in range(max_retries):
        r = requests.post(url, headers=headers, json=body)
        if r.status_code != 429:
            return r
        delay = int(r.headers.get("Retry-After", "1"))
        time.sleep(delay + 0.1)  # +100ms jitter
    raise RuntimeError("rate-limit retries exhausted")
```

## Behavior during incidents

If the limiter's Redis backend is unreachable, calls fall back to an
in-memory bucket per worker process. The limit still applies — it
just applies independently per worker, so an N-worker deployment
effectively allows N× the configured rate during the incident. You
won't be charged extra credits for any over-quota traffic that slips
through.

## Asking for more

For higher limits than the Pro tier offers — talk to support. Limits
are configurable per-customer.
