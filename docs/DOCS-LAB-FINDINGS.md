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

| # | What | Status |
|---|---|---|
| 1 | MCP does not apply the consumer's tier | **fixed** `879d3f0` — and it was worse than reported |
| 2 | "one **signed** commit" is not true | **decided**: change the copy `c1be652` / `b96b752` — site not deployed |
| 3 | `/ops/` vs `/admin/` | **decided**: `/ops/`; the code default was the outlier `ca9f346` |
| 4 | write credential never verified | **fixed** `f44a551` |
| 5 | `OAUTH_ISSUER` warning every boot | **fixed** `88da601` |
| 6 | stale template deep-link paragraph | **fixed** `15fe25e` |
| 7 | contradictory periodic-pull claim | **fixed** `15fe25e` — a third instance found in code |

Three things the list got wrong or understated, each written up under
its own item: `propose-feed` was dead over MCP for **every** tier, not
just degraded (#1); the `or "ops/"` fallback called stale is neither
stale nor unreachable (#3); and the periodic-pull claim also lives in
`sync_brain.py`'s docstring (#7).

Everything below was re-verified against a lab rebuilt from `main`, not
from the diff.

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

> **DECIDED: change the copy, don't implement signing.** Commits
> `c1be652` (site repo) and `b96b752` (this repo). Not deployed — the
> site push is the operator's call.

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

### Why not implement it

Signing with the credential the server already holds does not work.
GitHub only shows **Verified** for a signature made with a key
registered to the account *as a signing key*; a deploy key is a
repo-scoped auth credential and cannot be one. So the cheap version —
"sign with the SSH key we already have" — produces commits GitHub
badges **Unverified**, which is visibly worse than the current
no-badge state.

Doing it properly means the operator generates a signing key, mounts it
into the container as a third secret, and registers the public half with
GitHub. That is real setup cost on the wizard's critical path, and
`SETUP-DESIGN.md` is trying to hold the required-env list at two.

Against that: signing is not the property doing the work here. One
commit per approval, authored by a distinct server identity, full diff
in your own history, nothing entering without you pressing approve —
all already true, all verifiable, and that is what the sentence was
reaching for.

"Signed" is also a *security* claim on a launch page, and anyone who
clicked through to a commit would have watched it fail. Signing stays
available as a later feature; nothing now depends on it.

### Changed

| Where | Now reads |
|---|---|
| `landing.html:93` | …approval is one commit back to your repo, **authored by the server so its writes never look like yours**. |
| `landing.html:150` | …approval is one commit, **under the server's own name**. |
| `LAUNCH.md` §1 | …approval is one commit **authored by the server — so its writes are always distinguishable from yours**. |

Verified by rendering `/` from a container built on the working tree:
both sentences appear as intended and the word "signed" occurs **zero**
times on the page.

**The site is not deployed.** The commit sits on `main` in
`brainoutside-site` unpushed; publishing it is a separate, outward-facing
step and is yours to take.

## 3. Every doc says the ops UI is at `/ops/`; by default it is at `/admin/`

**Severity: medium.** Docs-only, but it is in every doc.

> **DECIDED: `/ops/` is canonical, and the code was the thing that was
> wrong.** Commit `ca9f346`. Re-verified live; see *Decision* below.

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

### Decision: `/ops/`, and the docs were never the thing to fix

This turned out not to be a judgement call. Counting the votes:

| Says | What |
|---|---|
| `ops/` | `.env.example:77`, the dev `.env`, every doc, six `or "ops/"` fallbacks in code, `templates/ops/`, `*_ops_views.py`, existing tests asserting `/ops/health/` |
| `admin/` | `config/settings/env.py:99` |

`.env.example` — the file an operator is told to copy — already said
`ops/`. The *code* default is what you get when you don't copy it, which
is precisely the fresh-`docker compose up` path the getting-started page
walks. So the lab hit the one configuration where the two disagreed.

The setting's own block is headed **"Admin URL hardening"**. Defaulting
an admin panel to `/admin/` — the most scanned path on the web —
cancels the feature for everyone who never overrides it.

Changed `env.py:99` to `"ops/"`. No documentation changed: it was right.

### Verified

Same lab, rebuilt, still with no `ADMIN_PANEL_URL_PATH` set:

```
before   /admin/ -> 302    /ops/ -> 404
after    /admin/ -> 404    /ops/ -> 302   (302 = exists, redirects to login)
```

Guardrail: `apps/core/tests/test_env_defaults.py` fails if the pydantic
default and `.env.example` ever disagree again on a key where that
changes *where the app is*, plus a named check that this one never goes
back to `admin/`.

**Upgrade note.** A deployment that never set `ADMIN_PANEL_URL_PATH`
moves from `/admin/` to `/ops/`. Set `ADMIN_PANEL_URL_PATH=admin/` to
stay put. No redirect is offered from the old path deliberately — a
redirect would advertise that the panel is there.

### Correction to this finding

`base.py:120`'s `or "ops/"` is described above as a fallback that "can
never fire". It fires whenever the variable is set to an empty string.
It was never stale — it is the intended default, written out six times
across the codebase, that `env.py:99` was contradicting.

## 4. The write credential is the only one the wizard never verifies

**Severity: medium.** Costs a confusing failure well after setup.

> **FIXED** — commit `f44a551`. Verified in a browser against the lab;
> see *Fix* below.

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

### Fix

`setup_services.verify_write_access` asks the remote the same question
`git push` asks first — the `git-receive-pack` service advertisement —
and reads the answer. Nothing is cloned, nothing is sent, no ref is
touched, so it is safe to press repeatedly while fixing a token's scopes
in another tab.

Measured against GitHub *before* writing it, because a read-shaped check
would have looked like it worked:

| token / repo | `upload-pack` (read) | `receive-pack` (write) |
|---|---|---|
| valid, push allowed | 200 | **200** |
| invalid or expired | — | **401** |
| valid, no write on this repo | 200 | **403** |

The last row is the failure this exists to catch — and note the 200 next
to it. Any check built on read access passes exactly when the operator is
about to be bitten.

A `--dry-run` push would be more literal, but the write step runs
*before* Build, so no local clone exists yet; it would mean cloning the
whole brain to answer a yes/no.

Two entry points:

- **Verify**, mirroring the read step. Uses the pasted token if there is
  one, else the stored one. Deliberately does **not** save — a token
  that fails the check should not get stored because someone pressed the
  button to find out.
- **Save** now runs the check too, so a credential cannot pass through
  the step unexamined. A warning, not a block: a network blip must not
  strand setup.

### Verified

In a real browser against the lab, on the actual wizard page:

```
Verify button present: True
[invalid token]  "That token was not accepted. It may be expired,
                  revoked, or pasted incompletely."   401 Unauthorized
[valid token]    "The server can push to your brain."
console errors: 0
```

And all four outcomes exercised against the live service, including the
403 that a browser cannot easily reach:

```
valid token, repo it CAN write        ok=True   The server can push to your brain.
valid token, repo it CANNOT write     ok=False  403 Forbidden — …/github/gitignore
invalid token                         ok=False  401 Unauthorized
remote with no https form             ok=False  file:///… has no https form
```

Confirmed afterwards that Verify stored nothing: the lab's working PAT
hashes identical before and after.

## 5. `OAUTH_ISSUER is 'http://localhost:8000' in production` on every boot

**Severity: low.** First impressions.

Logged once per gunicorn worker per boot (3× on the default config). It is
harmless and the message says so, but on a local trial it is the first thing a
newcomer reads in the log, and it reads like a misconfiguration.

Evidence: `evidence/e2-fresh-boot.txt`.

> **FIXED** — commit `88da601`. Zero occurrences across all services on
> the rebuilt lab; it was 3× before.

### It could not fire for a good reason

Worse than noisy — it was unfalsifiable. `_derive_public_origin` runs
first and overwrites a localhost issuer whenever `ALLOWED_HOSTS` names a
real host. So reaching the check *with* a localhost issuer means
`ALLOWED_HOSTS` named none — the condition the message tells you to go
check is the one guaranteed to hold whenever you are reading it.

And on `ALLOWED_HOSTS=localhost,127.0.0.1` a localhost issuer is not a
misconfiguration at all. It is an accurate description of a deliberate
local run — the exact run the getting-started page walks someone
through.

So the condition was fixed, not the volume. Demoting it to `INFO` would
have hidden the one case that is real. It is now silent when every
`ALLOWED_HOSTS` entry is loopback, and still loud for a deployment that
looks public but named no host to derive from (`ALLOWED_HOSTS=*`), where
the advice does apply.

`apps/core/tests/test_oauth_issuer_warning.py` covers both directions,
including that the real-domain case actually derives
`https://brain.example.com` rather than merely going quiet.

## 6. `DEPLOY.md` §4 is stale about the template deep link

**Severity: low.** Docs only.

It says the "generate from template" deep link on wizard step 2 "404s until
`brain-template` is published as its own public repo (M5.4)". It was published
on 2026-08-02. Either the link works now and that paragraph should go, or it
still fails for a different reason and the stated cause is wrong.

> **FIXED** — commit `15fe25e`. The link works; the paragraph is gone.

Measured, not assumed:

```
https://github.com/hassancs91/brainoutside-template/generate  ->  200
gh api repos/hassancs91/brainoutside-template
  {"is_template": true, "private": false, "visibility": "public"}
```

The paragraph is replaced by the thing that *is* true and does bite —
the async-creation race already recorded further down this page. GitHub
returns from "create from template" as soon as the repo *record* exists;
for a moment it is real, private and empty, and a server pointed at it
in that window clones nothing.

## 7. `DEPLOY.md` contradicts itself about the periodic pull

**Severity: low.** Docs only, but the two statements are one page apart.

§7 says, in bold: *"There is no periodic pull."* §8's post-deploy checklist then
says the server reindexes "within seconds (webhook) or minutes (periodic
pull)". §7 reads like it was written after someone actually checked. The docs
site follows §7.

> **FIXED** — commit `15fe25e`. §7 was right; §8 now agrees.

Confirmed on the running lab rather than by reading:

```
Q2 Schedule rows on a fresh prod stack:  0
config/scheduled.py present in image:    False
```

`config/scheduled.py` is the file `manage.py sync_scheduled` reads to
create Schedule rows, and it is in neither the repo nor the image — so a
real deploy registers no scheduled task of any kind. §7's parenthetical
about this was already accurate.

### What the list missed: the same false claim is in the code

`apps/brain/management/commands/sync_brain.py` opened with *"The
15-minute fallback beat (PLAN.md §4) schedules this via django-q2 at
deploy."* Same untrue statement, sitting on the sync path, where someone
tracing why their clone is stale would read it and stop looking. Fixed
in the same commit.

`docs/PLAN.md` §4 still describes the beat as designed. Left alone: it
is a plan document, and the beat is a reasonable thing to still want —
but `sync_brain.py` now says plainly that it does not exist.

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
