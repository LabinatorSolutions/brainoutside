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
