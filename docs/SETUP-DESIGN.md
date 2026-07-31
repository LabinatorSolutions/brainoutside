# Setup & deployment design (M5.3 + M5.4)

The design for turning this from "a thing Hasan runs" into a self-hosted
project a stranger can install. Written before the public release so the
decisions are deliberate; implementation follows this doc.

**Decided 2026-07-30:**
0. **The project is `BrainOutside`** — brainoutside.com. Your brain, kept
   outside your head, where your agents can read it.
1. **GitHub from day one.** No local-only brain mode. One code path, the
   repo is the source of truth and backed up from the first minute.
2. **Credentials live in encrypted settings**, with env/file overrides
   that win when present.
3. **Prebuilt versioned images on GHCR.** Users pull, they don't build.

### Fixed identifiers

Everything below assumes these; change them here, not inline.

| Thing | Value |
|---|---|
| Brand / product | **BrainOutside** |
| Domain | `brainoutside.com` |
| App repo | `github.com/<owner>/brainoutside` |
| Starter repo | `github.com/<owner>/brain-template` |
| Container image | `ghcr.io/<owner>/brainoutside:vX.Y.Z` |
| `APP_NAME` default | `BrainOutside` (already set in `settings/base.py`) |

`brain-template` keeps its generic name on purpose: it is a *brain*
template, usable without this server, and the URL a newcomer sees is
`…/brain-template/generate`, which reads as what it does. Rename to
`brainoutside-template` before publishing if brand consistency matters
more — it is a one-line change here and in the wizard link.

---

## The target

**Under 10 minutes, zero terminal commands, two browser tabs.**

The honest floor: this needs somewhere to run Docker, a GitHub account,
and a Claude credential. None of those can be removed. So "easy" means
*never opening a terminal and never reading a docs page to get started* —
not "no prerequisites".

Measured against today's 12 steps and 3 terminal sessions.

---

## Why not click-to-create-the-repo

Tempting, and technically possible: `POST /repos/{owner}/{repo}/generate`
creates a repo from a template. **Rejected** — that endpoint needs a
*classic* PAT with full `repo` scope (fine-grained tokens are not
documented as supported), which grants read/write on every repository the
user owns. Asking a newcomer to mint GitHub's most dangerous token and
paste it into an app they installed 90 seconds ago is a worse experience
than one click on a button they already trust.

GitHub's own template button does the same job with **no token at all**:

    https://github.com/<owner>/brain-template/generate

That link lands on a prefilled "create repository from template" form.
One click, repo exists, private by default if they choose it.

What IS worth automating is the fiddly part — installing the deploy key
and the webhook — which needs only a **fine-grained token scoped to that
one repo** (Administration: write, Webhooks: write). That is a defensible
ask, and it stays optional: both have a copy-paste fallback with a deep
link.

---

## The flow

### Step 0 — Deploy (~2 min)

Coolify → New Resource → Docker Compose → point at the repo (or paste a
compose file referencing the GHCR image) → set the domain → Deploy.

**Required env shrinks to two:** `POSTGRES_PASSWORD` and the domain
(`ALLOWED_HOSTS`). Everything else is generated on first boot and
persisted to the data volume:

| Was required | Becomes |
|---|---|
| `SECRET_KEY` | auto-generated, persisted |
| `FIELD_ENCRYPTION_KEY` | auto-generated, persisted |
| `GITHUB_WEBHOOK_SECRET` | auto-generated, shown in the UI to paste into GitHub |
| `MCP_LOOPBACK_SECRET` | auto-generated, persisted |
| `DJANGO_ADMIN_URL_PATH` | random slug, generated |
| `BRAIN_REPO_URL` + creds | set in the wizard |
| `ANTHROPIC_API_KEY` | set in the wizard (already encrypted-settings today) |

> **Hard requirement:** generated secrets are written ONCE and reused.
> A `FIELD_ENCRYPTION_KEY` that regenerates per boot silently destroys
> every stored credential. Generate → persist → verify on next boot.

### Where each setting lives, and why

Two stores already exist and should be reused rather than reinvented:

- `apps/core/runtime_settings.py` — plain values in Postgres, Redis
  read-through cache, **env fallback when no override is set**. Already
  used by `ADMIN_IP_ALLOWLIST`.
- `apps/brainconfig` `AppSetting` — Fernet-encrypted. Already used by
  `ANTHROPIC_API_KEY`, model choices, budgets, the daily cap.

Three tests decide where a setting belongs. **(1)** Needed before the DB
is reachable? Must be env. **(2)** Read at import time? Moving it means a
restart, so it is not really a UI setting. **(3)** Would a wrong value
lock the operator out of the UI that sets it? Keep it in env even if it
is technically movable.

| Setting | Verdict | Why |
|---|---|---|
| `DATABASE_URL`, `POSTGRES_PASSWORD` | **env** | Needed to reach the DB |
| `REDIS_URL` | **env** | Needed at boot by cache + queue |
| `DJANGO_SETTINGS_MODULE`, `DEBUG` | **env** | Process bootstrap |
| `SECRET_KEY` | **auto-gen + persist** | Signs sessions — needed before any login exists |
| `FIELD_ENCRYPTION_KEY` | **auto-gen + persist** | Decrypts the settings table itself; storing it there is circular |
| `MCP_LOOPBACK_SECRET` | **auto-gen + persist** | Shared between web and mcp at startup |
| `DJANGO_ADMIN_URL_PATH` | **auto-gen (env)** | Baked into `config/urls.py` at import — a change needs a restart |
| `ADMIN_PANEL_URL_PATH` | **env** | Same: URL routing, resolved at import |
| `ALLOWED_HOSTS` | **env** | Lockout footgun: a wrong value 400s every request, including the page that would fix it |
| `BRAIN_REPO_DIR`, `BRAIN_VIEWS_DIR` | **env** | Container paths tied to volume mounts; compose owns them |
| `ADMIN_IP_ALLOWLIST` | **UI (already works)** | Flows through `runtime_settings` today — just needs exposing on the Settings page, with a guard that the editor's own IP stays allowed |
| `BRAIN_REPO_URL` | **UI (wizard)** | Changing it must also clear the clone — see the origin-check gap below |
| SSH deploy key | **UI, encrypted** | App generates it; user pastes the public half into GitHub |
| Write PAT | **UI, encrypted** | Env/file override retained (see the tradeoff above) |
| `GITHUB_WEBHOOK_SECRET` | **UI, auto-gen** | Read per request in `brain/views.py`; generate it and show it to paste into GitHub |
| `BRAIN_COMMIT_NAME` / `_EMAIL` | **UI** | Pure display config, read per commit |
| `FEED_PAYLOAD_MAX_KB` | **UI** | Read per request in `intake.py` |
| `APP_NAME` | **UI — done** | On `/ops/settings/`; env remains the first-boot fallback. Boot-time consumers (OpenAPI title, MCP server name, `Q_CLUSTER`, Postgres `application_name`) only re-read on restart |
| `PUBLIC_BASE_URL` | **UI** | Low value, but harmless and read at render |
| Claude key, models, budgets, cap | **UI (already)** | Encrypted `AppSetting` today |

**Result: the only env a human writes is `POSTGRES_PASSWORD` and the
domain.** Everything else is generated, defaulted, or set in the browser.

Two things this exposes that must be handled with it:

- **Changing `BRAIN_REPO_URL` in the UI does nothing today.**
  `gitrepo.bootstrap()` reuses *any* valid clone at the repo dir and
  never checks that its origin matches the configured URL, so a repo
  switch would silently keep serving the old brain. Before this becomes a
  UI field: compare origin on boot, and fail loudly (or offer a "replace
  the clone" action) when they differ.
- **Anything UI-settable that needs a restart must say so** in the UI, not
  fail silently or pretend it took effect.

### Step 1 — First visit is the wizard, not a login box

Zero users in the DB → every route redirects to `/setup`. No
`docker exec createsuperuser`.

**1. Create your account** — email + password → superuser.

**2. Create your brain**

> Your brain is a normal GitHub repository full of markdown files. You own
> it. You can read it, edit it, and take it with you.

- Primary: **[ Create my brain repo ]** → opens
  `github.com/<owner>/brain-template/generate` in a new tab. They name it,
  choose private, click Create.
- Then: paste `owner/name` (accept a full URL too and normalise it).
- Secondary: "I already have one" → same field.

**3. Let the server read it**

The app generated an SSH keypair on first boot. It shows the **public**
half with a Copy button and a deep link to
`github.com/<owner>/<repo>/settings/keys/new`.

- [ Copy key ] [ Open GitHub → Add deploy key ] [ I've added it → Verify ]
- Verify does a real clone and reports the actual git error on failure —
  not "something went wrong".

**4. Let the server write back to it**

Approvals commit and push, which a read-only deploy key cannot do. Paste a
fine-grained PAT (Contents: read+write, this repo only) with a deep link
to the token page and the exact permissions listed.

Skippable — without it, approvals commit locally and the dashboard warns
that the brain is ahead of GitHub.

**5. Connect Claude**

API key **or** subscription token (`sk-ant-oat` — the no-API-billing path
is a headline feature). "Test connection" runs a real call and shows
model + latency + tokens before they move on.

**6. Build** — clone → index → snapshots, with live progress. Land on the
dashboard with the rings drawn.

### Step 2 — The dashboard finishes the job

Setup does not end at the wizard; the remaining items become visible
state, because nobody reads the docs page:

- 🔴 **"Your ops UI is reachable from the public internet."** This page
  approves feeds and reads every private note. → how to restrict it.
- 🟡 **"No webhook — pushes sync within 15 minutes."** → copy the
  generated secret + deep link, or auto-install with the PAT.
- 🟡 **"No backup of the database."** Feeds, events, chat and the token
  ledger are not in the repo and are not rebuildable.
- 🟡 **"Brain is ahead of GitHub"** when a write credential is missing.

---

## Credentials

Both git credentials move to **encrypted settings** (Fernet, same as the
Claude key today): set in the browser, rotated without a redeploy, no file
mounting. `BRAIN_GIT_*` env/file values win when present, so
infrastructure-as-code setups keep working.

**Security tradeoff, recorded honestly.** Today the write PAT is mounted
only into the worker container — the internet-facing web container
physically cannot read it (grill C13). Moving it to the database means any
container with DB access can. The realistic attack becomes "Django RCE →
read PAT → rewrite the brain's history", where today that same RCE gets
nothing.

Accepted for a single-user self-hosted product, on these conditions:
- `SECURITY.md` states it plainly rather than burying it;
- the env/file override remains, so the split is available to anyone who
  wants it;
- only the worker ever *uses* the write credential (code-level boundary
  kept even though the container-level one is gone).

Note this is unrelated to prompt injection: the reader agent is sandboxed
to tier snapshots with no Bash, no network and no DB.

---

## Distribution & updates

- **`ghcr.io/<owner>/brainoutside`**, multi-arch (amd64 + arm64), semver
  tags — `:v1.2.0` and `:latest`, built by GitHub Actions on tag.
- `docker-compose.yml` references the published image; a `build:` override
  stays for contributors.
- **Update = change the tag and redeploy.** Migrations run in the
  entrypoint (already do), snapshots rebuild, the brain repo is untouched.
- `CHANGELOG.md`, and release notes that call out anything requiring
  action.

### The contract-version problem

The app ships a `brain-template`, but users' brains are copies that never
update. When v1.3 expects frontmatter that a brain created at v1.0 lacks,
we cannot migrate a repo we do not own.

Plan: version the contract (`contract-version:` in `CLAUDE.md`), have the
startup check **warn, never fail**, and ship a `manage.py upgrade_brain`
that proposes the diff through the normal approval queue — the same
human-gated path everything else uses. Needs designing before v1.0, not
after.

---

## Build order

| # | Item | Notes |
|---|---|---|
| 1 | Auto-generate + persist boot secrets | Unblocks the 2-env deploy |
| 2 | `/setup` wizard, steps 1–6 | The core of M5.3 |
| 3 | Git credentials → encrypted settings | + SSH keypair generation |
| 4 | Verify/repair actions | Real git errors surfaced, not swallowed |
| 5 | Dashboard setup-health panel | Where security actually gets enforced |
| 6 | GHCR release workflow + CHANGELOG | Before any public link exists |
| 7 | INSTALL / SECURITY docs | README done; these two remain |
| 8 | bash + Makefile twin of `dev.ps1` | Linux/macOS parity |
| 9 | Optional PAT automation for key + webhook | Convenience, never required |

Items 1–5 are M5.3; 6–9 are M5.4.
