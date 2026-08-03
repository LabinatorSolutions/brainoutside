# Findings from the docs lab

Everything below was found by **running the product**, not by reading it —
while writing the brainoutside.com getting-started page. Each one has a
reproducible script and a captured output file behind it in the site repo at
`brainoutside-site/docs-lab/getting-started/`.

The lab: a fresh `config.settings.prod` stack on empty volumes (compose project
`bo-docs-getting-started`), pointed at a private brain generated from the
published `brainoutside-template`, with the `/setup` wizard walked in a real
browser. That is also the boot gate `LAUNCH.md` §2 left open — **it passes**.

Opened 2026-08-02. Nothing here has been fixed; this is a work list.

---

## 1. MCP does not apply the consumer's tier — REST does

**Severity: high.** Blocks the getting-started page's MCP section.

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
