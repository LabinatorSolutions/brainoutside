# Authentication

Every paid `/api/v1/*` call is authenticated with an **API key** sent
in the `Authorization` header. Keys are minted in the dashboard at
[/dashboard/keys/](/dashboard/keys/) and look like `mcpsk_<~43 url-safe
characters>` (256 bits of entropy).

Endpoints with `credits_cost=0` accept anonymous callers — no key
required. Every endpoint with `credits_cost>0` requires a valid key.

## Sending the header

```bash
curl -X POST {{ PUBLIC_BASE_URL }}/api/v1/hello \
  -H "Authorization: Bearer mcpsk_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{"name": "world"}'
```

Python:

```python
import requests

r = requests.post(
    "{{ PUBLIC_BASE_URL }}/api/v1/hello",
    headers={"Authorization": "Bearer mcpsk_your_key_here"},
    json={"name": "world"},
)
r.raise_for_status()
print(r.json())
```

JavaScript:

```javascript
const r = await fetch("{{ PUBLIC_BASE_URL }}/api/v1/hello", {
  method: "POST",
  headers: {
    "Authorization": "Bearer mcpsk_your_key_here",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ name: "world" }),
});
if (!r.ok) throw new Error(`HTTP ${r.status}`);
console.log(await r.json());
```

## Key lifecycle

Keys are SHA-256 hashed at rest — we never store the plaintext. **The
secret is shown exactly once at creation.** If you lose it, revoke the
key and mint a new one.

  - Mint a new key: dashboard → **API keys** → **+ New key**
  - Revoke a key: dashboard → **API keys** → **Revoke** (instant; in-flight
    calls finish, new calls return `401 invalid_credential`)
  - See per-key usage: click any key in the list → **Recent activity**

## Failure modes

| Status | Code | Cause |
|---|---|---|
| `401` | `invalid_credential` | Bad / unknown / revoked key |
| `401` | `auth_required` | No `Authorization` header on a paid endpoint |
| `402` | `insufficient_credits` | Credit balance is below the per-call cost; top up on the [billing page](/dashboard/billing/) |

See the [error codes guide](/docs/guide/errors/) for the full response
body shape and the complete error catalog.

## Best practices

  - **One key per environment.** Use a `prod-server` key in production
    and a `local-dev` key on your machine. If one leaks you only have
    to revoke one, and your dashboard's _Recent activity_ tells you
    exactly which.
  - **Rotate periodically.** Revoke + re-mint every 90 days. The
    process is two clicks and zero downtime if you swap before
    revoking.
  - **Never put keys in client-side JS.** Keys belong on a server
    you trust. Browser-side calls leak your secret to anyone who
    opens devtools.
  - **Don't commit keys to git.** Use `.env` files in `.gitignore`,
    or your platform's secrets manager.
