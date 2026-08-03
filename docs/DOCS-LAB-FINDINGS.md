# Findings from the docs lab

Everything below was found by **running the product**, not by reading it —
while writing the brainoutside.com getting-started page. Each one has a
reproducible script and a captured output file behind it in the site repo at
`brainoutside-site/docs-lab/getting-started/`.

The lab: a fresh `config.settings.prod` stack on empty volumes (compose project
`bo-docs-getting-started`), pointed at a private brain generated from the
published `brainoutside-template`, with the `/setup` wizard walked in a real
browser. That is also the boot gate `LAUNCH.md` §2 left open — **it passes**.

Opened 2026-08-02. Worked through 2026-08-03 — each item now carries its
own status line.

**Note on the lab.** It was rebuilt from `origin/main` on 2026-08-03 to
verify these fixes, so its port moved (`43331` → `45939`; it is assigned
fresh on each `up`). Read the current one from
`docker compose -p bo-docs-getting-started ps`. Volumes untouched: the
brain, the wizard's answers and the one approved feed are all still there.

---

## 1. MCP does not apply the consumer's tier — REST does

**Severity: high.** Blocks the getting-started page's MCP section.

> **FIXED** — commit `879d3f0`. Re-verified live; see *Fix* below.

The same consumer key resolves to a different tier depending on the door:

| Path | Result |
|---|---|
| `POST /api/v1/ping` + key | `{"pong": true, "tier": "agents-only"}` |
| `POST /api/v1/list-notes` + key | **200, 7 notes** — both takes visible |
| `POST /api/v1/list-notes` anonymous | **401** |
| **MCP, same key** | agent reports **`public`**, sees **2 entities**, 0 takes |

The `Consumer` row says `max_visibility: agents-only`, and `APIKey.last_used_at`
is set *by the MCP call* — so the key is recognised on the MCP path and the
tier is not applied. `apps/mcp_proxy/views.py:31` says the resolved identity
rides out on `X-MCP-*` headers; something in that hand-off lands on `public`.

**It fails closed** — an agents-only key reads *less* than it should, never
more — so this is not a disclosure bug. But MCP is the headline integration,
and an agent pointed at a real brain answers *"there are no takes here"*.

Evidence: `evidence/e5-mcp.txt`, `scripts/e5_mcp_query.py`.

### What the list got wrong: `propose-feed` was dead over MCP entirely

Worse than reported, and in a way that matters more. `propose-feed`
raises below `agents-only` (`apps/feeds/endpoints.py:75`), and every MCP
caller resolved to `public` — so **no key of any tier could propose a
feed over MCP**, only over REST:

```
propose-feed over MCP -> 200 isError: True
  "propose-feed requires an agents-only key or above."
```

The read side degraded quietly; the write side was simply unavailable.
Same root cause, one fix.

### Cause

Not the hand-off — the headers were fine. `apps/core/mcp/bridge.py`
built **every** `Ctx` with `user=None, credential=None`, under a
`# Phase 3 resolves accounts.User from mcp_user_id_var` comment. The
contextvars were set by the middleware and never read. The module
docstring already described the code that was missing, which is why
reading it does not find this.

`tiers.tier_for_credential(None)` returns `public` — identical to an
unprofiled key — so the failure had no distinguishing symptom.

### Fix

The proxy strips the bearer token deliberately, so the subprocess has to
re-look-up the rows from `(user_id, credential_id)`. Two pieces:

- **`X-MCP-Credential-Kind` now crosses the hop and is read.** The proxy
  was already sending it (`views.py:170`); nothing consumed it. A bare pk
  is ambiguous — `APIKey` 3 and an OAuth `AccessToken` 3 are different
  credentials with different tiers.
- **A rehydrator registry on `apps.core.bearer`.** `apps.core` cannot
  import `apps.api_keys` (Contract 1), so this follows the pattern that
  module already uses for resolvers: core owns the registry, the owning
  app registers into it from `AppConfig.ready()`. The rehydrator
  re-applies every liveness guard `authenticate_token` applies —
  it hands out a tier, so a revoked key must not resolve just because
  someone holds its id.

Unknown kind or dead row → no credential → `public`, with a logged
warning. Same direction as the bug, now only when it is true.

### Verified

Tier parity across every tier, on the live lab, after rebuild — and the
counter-check that this grants tier rather than bypassing it:

| key tier | REST tier / notes | MCP tier / notes | propose-feed over MCP |
|---|---|---|---|
| `public` | public / 2 | public / 2 | denied |
| `agents-only` | agents-only / 7 | agents-only / 7 | accepted |
| `private` | private / 7 | private / 7 | accepted |
| *unprofiled* | public / 2 | public / 2 | denied |

A `public` key still sees 2 of 7. Least privilege for an unprofiled key
still holds. 123 host tests pass, including
`apps/core/mcp/tests/test_bridge_identity.py` — new, and DB-free by
registering a fake credential kind against the rehydrator registry.

Probe keys and the two probe feed proposals were deleted from the lab
afterwards; its consumer list and feed queue are as the docs work left
them.

## 2. "Approval is one **signed** commit" is not literally true

**Severity: medium — it is live copy.**

The approval commit `2414eda` on the lab brain reports, from the GitHub API:

```
"verified": false, "reason": "unsigned"
```

There is no GPG or SSH signing. The commit is authored
`brain-app <brain-app@localhost>` (configurable via `BRAIN_COMMIT_NAME` /
`BRAIN_COMMIT_EMAIL`).

What **is** true and worth saying: *one commit per approval, authored by the
server identity so its writes are distinguishable from yours.* That is a good
property and it does not need the word "signed".

The phrasing appears on the **live landing page** (§6, "Nothing enters without
you") and in `LAUNCH.md` §1. Either implement signing or change three sentences.

Evidence: `evidence/e4-feed.txt`.

## 3. Every doc says the ops UI is at `/ops/`; by default it is at `/admin/`

**Severity: medium.** Docs-only, but it is in every doc.

`config/settings/env.py:99` defaults `ADMIN_PANEL_URL_PATH` to `"admin/"`.
Measured on the lab instance: `/admin/` → **200**, `/ops/` → **404**. The
wizard's own `progress.json` returns `"ops_url": "/admin/"` and Finish lands
there.

`CLAUDE.md`, `docs/DEPLOY.md`, `docs/PLAN.md`, `docs/OPEN-SOURCE.md` and
`docs/UI-REWRITE.md` all describe `/ops/…`.

**Checked, and it is NOT a security hole:** the IP allowlist keys off
`ADMIN_PANEL_URL_PATH` itself (`apps/core/security/ip_allowlist.py:101`), so
`ADMIN_IP_ALLOWLIST` protects the real prefix whatever it is set to. The risk is
confusion, not exposure. Note also that `config/settings/base.py:120` carries a
stale `or "ops/"` fallback that can never fire.

Decide which one is canonical, then fix the other.

Evidence: `evidence/e3-wizard-walk.txt`.

## 4. The write credential is the only one the wizard never verifies

**Severity: medium.** Costs a confusing failure well after setup.

- Read step: **Verify** button, proves the deploy key against the real repo.
- Claude step: **Test** button — returned `Connection OK` in 2.1s on an
  `sk-ant-oat` token.
- Write step: accepts a PAT and moves on. Nothing is checked.

A token without repo access therefore surfaces much later, as a **failed feed**,
after the operator has already trusted the setup. Observed exactly that: a
fine-grained PAT that did not list the repo under *Repository access* failed at
approval with

```
remote: Write access to repository not granted.
fatal: ... The requested URL returned error: 403
```

A `verify` action mirroring the read step would catch it in the wizard, where
the operator is already in a fixing frame of mind.

Evidence: `evidence/e4-feed-FAILED-write-permission.txt`.

## 5. `OAUTH_ISSUER is 'http://localhost:8000' in production` on every boot

**Severity: low.** First impressions.

Logged once per gunicorn worker per boot (3× on the default config). It is
harmless and the message says so, but on a local trial it is the first thing a
newcomer reads in the log, and it reads like a misconfiguration.

Evidence: `evidence/e2-fresh-boot.txt`.

## 6. `DEPLOY.md` §4 is stale about the template deep link

**Severity: low.** Docs only.

It says the "generate from template" deep link on wizard step 2 "404s until
`brain-template` is published as its own public repo (M5.4)". It was published
on 2026-08-02. Either the link works now and that paragraph should go, or it
still fails for a different reason and the stated cause is wrong.

## 7. `DEPLOY.md` contradicts itself about the periodic pull

**Severity: low.** Docs only, but the two statements are one page apart.

§7 says, in bold: *"There is no periodic pull."* §8's post-deploy checklist then
says the server reindexes "within seconds (webhook) or minutes (periodic
pull)". §7 reads like it was written after someone actually checked. The docs
site follows §7.

---

## Not bugs — behaviours the docs must handle

- **GitHub template generation is asynchronous.** `gh repo create --template`
  (and the web button) returns as soon as the repo *record* exists; for a short
  window the repo is real, private and **empty** (`size: 0`, `HTTP 409 Git
  Repository is empty`). Point a server at it in that window and it clones
  nothing. Measured wait: 0.7s — small, but it is a race the getting-started
  page has to sequence around.
- **`SECURE_SSL_REDIRECT_ENABLED` defaults on**, which is correct behind a TLS
  proxy and fatal on plain `http://localhost` — every request 301s *including
  the wizard that would let you fix it*. `DEPLOY.md` documents this; the docs
  site needs to as well for anyone trying it locally first.
- **Extraction starts by itself.** `intake.propose()` enqueues it
  (`apps/feeds/services/intake.py:259`), so there is no "extract" button to
  press. Correct, and worth stating so nobody waits for one.

## What passed, and is now proven rather than assumed

- A private brain generated from the published template carries every contract
  path; `contract-version: "1.0"`; zero knowledge notes.
- Cold `--no-cache` build **121–126s** (n=3), `/setup` reachable **21.5–23.4s**
  after `up`, on a Windows laptop with Docker Desktop.
- The six wizard steps complete in **20.0s of interaction, zero console
  errors**, with no terminal work after `up`.
- The write gate holds: after a feed proposal, `knowledge/` still contained 0
  notes and the working tree was clean.
- Feed → queue → approve → **commit on GitHub**, end to end.
