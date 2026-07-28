# my-brain-web-app — Phase 2 Plan

The online head for Hasan's mind (`my-brain` repo). A Django app that owns a
server-side clone of the brain repo and exposes it as REST + MCP + UI, with a
gated write path, structured logging, and a chat test bench.

**Core principle (from phase 1):** the git repo stays the single source of
truth. Everything in this app is either a *view* of the repo or a *gated
pipeline into* the repo. The database is a rebuildable index — if DB and repo
ever disagree, the repo wins, and a `drift` event is logged.

---

## 1. Base: fork of `mcp-api-starter-template`

We fork Hasan's own Django + FastMCP boilerplate — the source repo at
`D:\repos\mcp-api-starter-template` (github.com/hassancs91/
mcp-api-starter-template), NOT `solo-mcp-api-starter`, which is just its
squashed distribution export — per its `docs/FORKING.md` rebrand checklist,
rather than starting a fresh Django project. Rationale:

- The `@endpoint` registry gives the entire two-layer read side for free:
  one decorated class → REST `POST /api/v1/<slug>` + MCP tool + docs page.
- API keys, per-key auth, call logs, Fernet-encrypted secrets, healthz,
  Docker stack (web + mcp + worker + postgres + redis) already exist and are
  battle-tested by ToolerBox.
- On-brand: "ToolerBox proves the boilerplate" — the brain proves it again.

Unused SaaS machinery (plans, credits, billing) is left dormant or stripped
per the forking checklist. The starter's per-key **credits/rate-limit**
hooks may later be useful if the brain ever goes public.

## 2. App layout (new apps on top of the starter)

| App | Responsibility |
|---|---|
| `apps/brain` | Git clone manager, sync + webhook, frontmatter index models, validator, drift check |
| `apps/app_endpoints/mind` | The public surface: Endpoint classes (REST+MCP auto-mounted) |
| `apps/feeds` | Feed proposals, approval queue, commit+push on approve |
| `apps/reader` | Claude Agent SDK runner (shared service), `assemble_context`, chat |
| `apps/events` | Structured event log + dashboard queries |
| `apps/brainconfig` | Settings page: Claude SDK config, test connection, app settings |

## 3. Data model

All content-bearing truth stays in the repo. These models are index/ops only.

### `apps/brain`
- **Entity** — one row per INDEX.md entry (note, project card, identity file,
  lens, catalog). Fields: `entity_id` (e.g. `take-2026-07-...`), `kind`
  (identity/project/take/story/lesson/fact/catalog/lens), `path`, `title`,
  `description`, `status` (current/superseded), `superseded_by`,
  `visibility` (public/agents-only/private), `topics` (JSON), `projects`
  (JSON), `source`, `source_url`, `date`, `last_verified`, `content_hash`,
  `indexed_at`. Unique on `entity_id`.
- **SyncRun** — one row per index rebuild: trigger (webhook/manual/post-feed),
  repo commit hash, entities added/changed/removed, drift detected (bool),
  duration.

### `apps/feeds`
- **Feed** — `source_id`, `channel` (ui/api/mcp), `raw_payload` (URL, pasted
  text, transcript ref), `proposal` (JSON: list of file writes with full
  content, INDEX.md line changes, supersede markings, card `last-verified`
  bumps), `status` (pending/approved/edited/rejected/failed),
  `decided_at`, `commit_hash`, `error`. FK → SdkOperation(s).

### `apps/reader`
- **ChatSession** — `tier` (private/agents-only/public — the simulator
  switch), `title`, `created_at`, totals (tokens, cost).
- **ChatMessage** — session FK, role, content, `sources` (JSON: entity_ids
  served, each with visibility + staleness at serve time), FK → SdkOperation.

### `apps/events`
- **Event** — `type` (read/feed/drift/auth_denied/sync/settings_change),
  `consumer` FK (starter's API-key owner; null for UI/admin), indexed
  columns: `created_at`, `type`, `consumer`, plus `entity_ids` (JSON) and
  `details` (JSON) for the variable parts. Every dashboard is a query over
  this table; UI offers filtering by type/consumer/date/entity.
- **SdkOperation** — one row per Claude Agent SDK invocation, no exceptions:
  `kind` (feed_extract/assemble_context/chat_turn/test_connection),
  `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_write_tokens`, `cost_usd`, `duration_ms`, `ok`, `error`,
  `related` (generic FK → Feed/ChatMessage/read event). This is the token
  ledger the dashboards aggregate (spend per day, per kind, per consumer).

### `apps/brainconfig`
- **AppSetting** — key/value, values Fernet-encrypted at rest (starter
  already ships the Fernet key in env). Keys: `ANTHROPIC_API_KEY`,
  `CLAUDE_MODEL` (default + per-kind overrides), `MAX_TOKENS_PER_OP`,
  `BRAIN_REPO_URL`, `GITHUB_WEBHOOK_SECRET`.

## 4. The git layer (`apps/brain`)

- Clone lives on a Docker volume at `/data/brain-repo`; auth via a
  read-write **deploy key** scoped to the brain repo only.
- **Single-writer lock** (file lock) around every mutating git operation.
- Write sequence on feed approval: lock → `pull --rebase` → write files →
  run validator → `commit -m "feed: <source-id>"` → push → reindex → unlock.
  Any failure rolls back the working tree (`checkout .` + `clean -fd`) and
  marks the Feed `failed` with the error.
- **Webhook** `POST /webhooks/github` (HMAC-verified): pull + reindex, so
  local feeds via Claude Code appear online within seconds. Fallback: a
  worker-beat pull every 15 min.
- **Drift check**: every reindex ends by comparing repo-parsed state hash vs
  DB state hash; mismatch → `drift` event with the exact entity ids →
  auto-repair from repo. Dashboard health tile shows sync status.

### Validator (mechanical contract enforcement, runs pre-commit on feeds)
1. Frontmatter schema valid; `id` matches filename and `type` matches folder.
2. `source` + `source_url` present (provenance rule).
3. `topics` ⊆ taxonomy in CLAUDE.md (parsed from the repo, not duplicated).
4. Takes/stories contain `> VERBATIM:` (voice rule).
5. No file deletions in feed commits (supersede-never-delete).
6. No Arabic-script content in bodies (language rule).
7. Every new note has a matching INDEX.md line.

## 5. The read surface (`apps/app_endpoints/mind`)

Each is one `@endpoint` class → REST + MCP + docs automatically.

**Dumb layer** (serves repo bytes, visibility-filtered):
- `get_index` — INDEX.md filtered to caller's tier (lines above tier removed).
- `list_notes` — filter by kind/topic/project/status; returns index rows.
- `get_note` — full markdown by entity_id.
- `get_lens` — lens file content.
- `get_identity` — the identity core files (every consumer needs these).
- `get_raw` — raw/ file by path (depth on demand).

**Smart layer**:
- `assemble_context(task, lens?)` — spins the mind-reader Agent SDK agent
  over the clone; returns `{context_pack, entity_ids_used, tokens}`.

**Write door**:
- `propose_feed(source, payload)` — creates a pending Feed (never writes the
  repo). This is how a note-taking app / iOS shortcut / PyRunner script
  automates capture; approval stays human, in the UI.

**Visibility enforcement** lives in one place — a file-access service below
both layers. Every API key carries `max_visibility`; the service refuses to
read entities above tier, so the reader agent can't leak what it never saw.
Denials log as `auth_denied` events. Admin (session-authed UI) = `private`.

## 6. Claude Agent SDK integration (`apps/reader`)

- One shared `SdkRunner` service wraps every SDK call: loads the API key from
  AppSetting, sets `cwd` to the clone, injects the system prompt **from the
  repo itself** (CLAUDE.md + the relevant skill under `skills/`), enforces
  `MAX_TOKENS_PER_OP`, and records an SdkOperation row (tokens, cost,
  duration) for **every** invocation — feed extraction, context assembly,
  chat turns, and test connections alike.
- Reader agent: read-only tool set (Read/Grep/Glob), sandboxed to the clone.
- Feeder agent: also read-only against the repo — it produces a *proposal
  object*, never file writes; only the approval handler touches disk.
- Runs execute in the starter's worker (async), so web requests don't block;
  `assemble_context` and chat support streaming via the worker → SSE.

## 7. UI (Django templates, learnwithhasan theme)

Design derived from the live site's theme contract
(`static/css/theme/tokens.css` on learnwithhasan.com):

- Palette: paper `#fffef7` / cream `#faf8f5` surfaces, ink `#1a1a2e`, muted
  `#6b6b7b`, accent indigo `#6366f1` (hover `#4f46e5`), violet/coral
  secondary accents, teal `#4db8a8` success, yellow warn, red `#ff6b6b`
  danger; dark terminal ink scale (`#0d1117` … `#c9d1d9`) for log/code
  panes.
- Type: Space Grotesk (display), Inter (body), JetBrains Mono (code/ids).
- Radius 14px / 10px, soft double shadows — same tokens file, copied in as
  `--tokens` so the brain app reads as a sibling of the main site. Clean,
  light, professional — public-ready later.

Pages (ops-first V1):
1. **Dashboard** — sync health tile, pending feeds, reads today, token spend
   (day/week), most-served notes, staleness list (cards > 45 days).
2. **Brain browser** — entity table (filter by kind/topic/status/visibility),
   note view with rendered markdown + frontmatter chips + staleness flag.
3. **Feed queue** — pending proposals with full diff view; approve / edit /
   reject; history of past feeds with commit links.
4. **Chat** — sessions, tier switcher (private / agents-only / public
   simulator), sources panel per answer (entities used, visibility,
   staleness, verbatim quotes), token count per message + session total.
5. **Logs** — Event stream with filters (type/consumer/date/entity),
   SdkOperation ledger with cost aggregates.
6. **Settings** — Claude SDK section: API key (write-only field, encrypted),
   model pickers per operation kind, max-tokens budget, **Test connection**
   button (minimal SDK ping → shows model + latency + tokens, logged as an
   SdkOperation). Also: repo URL/deploy key status, webhook secret,
   manual "pull + reindex now" and "rebuild index" actions.
7. **Consumers** — starter's API-key management + `max_visibility` per key.

## 8. Deployment (Coolify)

- Starter's Docker stack: web + mcp + worker + postgres + redis; one extra
  volume for `/data/brain-repo`. Deploy as a docker-compose resource on the
  existing Coolify VPS.
- Env: starter's bootstrap (SECRET_KEY, Fernet key, admin path) + deploy key.
  Mind Coolify's empty-string env injection (known lesson: apps that check
  unset-vs-empty crash) — all env reads treat empty as unset.
- GitHub webhook → the Coolify-exposed domain `/webhooks/github`.

## 9. Build sequence — step by step

Each step is small, independently verifiable, and committed on its own.
A step is DONE only when its check passes.

### M0 — Prepare the brain repo (prerequisite, in `my-brain`)
- **0.1** Curate untracked files: commit `CLAUDE.md`, `.claude/skills/`,
  `eval/`, templates, `.gitignore`; keep `.vscode/` and any local-only
  settings out of git.
  *Check: `git status` clean; no secrets or editor config tracked.*
- **0.2** Create private GitHub repo `my-brain`, push `main`.
  *Check: fresh `git clone` elsewhere reproduces the full contract
  (CLAUDE.md + skills + eval present).*
- **0.3** Generate a dedicated read-write deploy key for the server; add to
  GitHub repo settings. *Check: clone + push works with that key only.*

### M1 — Read-only online brain
- **1.1** Fork `mcp-api-starter-template` → this repo; run its
  `docs/FORKING.md` rebrand checklist (APP_NAME=BrainServer, brand color =
  indigo `#6366f1`, strip demo endpoint, leave billing dormant).
  *Check: `make stack-up` boots; `/healthz` green.*
- **1.2** Import learnwithhasan theme tokens (`tokens.css` palette + fonts)
  into the template's base styles. *Check: base template renders with
  paper/ink/indigo look.*
- **1.3** `apps/brain`: git layer — clone-on-boot (deploy key from env),
  single-writer lock, `pull_rebase()`, `/data/brain-repo` volume.
  *Check: container boots with a fresh clone; restart reuses it.*
- **1.4** `apps/brain`: Entity model + frontmatter parser + `rebuild_index`
  management command. *Check: command indexes every entity; counts match
  the repo; `_TEMPLATE.md` and non-entity files correctly excluded.*
- **1.5** Sync + drift: SyncRun model, post-pull reindex, state-hash drift
  check emitting `drift` events, webhook `POST /webhooks/github`
  (HMAC-verified) + 15-min fallback pull.
  *Check: push a commit from local → server reindexes within seconds;
  manually corrupt a DB row → next sync logs drift and self-repairs.*
- **1.6** Visibility core: file-access service enforcing tier; extend the
  starter's API-key model with `max_visibility`.
  *Check: unit tests — public key cannot read agents-only/private paths.*
- **1.7** Dumb-layer endpoints (`get_index`, `list_notes`, `get_note`,
  `get_lens`, `get_identity`, `get_raw`) via the `@endpoint` registry.
  *Check: REST + MCP + docs pages all serve; tier filtering verified per
  endpoint.*
- **1.8** `apps/brainconfig`: AppSetting (Fernet-encrypted) + Settings page
  (API key write-only field, model pickers, budgets, repo/webhook config,
  "pull + reindex now", "rebuild index") + SdkRunner skeleton +
  **Test connection** button logging its SdkOperation.
  *Check: bad key → clear error; good key → model+latency+tokens shown and
  one SdkOperation row written.*
- **1.9** UI: dashboard v1 (sync health, entity counts, staleness list) +
  brain browser (filterable table, note view with frontmatter chips).
  *Check: every entity reachable and rendered; staleness flags match
  `last-verified` + 45 days.*
- **1.10** Deploy to Coolify (compose resource, volumes, env, webhook URL,
  empty-env guards). *Check: end-to-end from the public domain: auth'd
  REST read + MCP tool call + UI login.*

### M2 — Write path
- **2.1** Feed model + `propose_feed` endpoint + UI feed form (URL / pasted
  text / transcript). *Check: proposals from all three channels land as
  pending Feeds; repo untouched.*
- **2.2** Feeder agent via SdkRunner (read-only tools, proposal object out,
  SdkOperation logged). *Check: feeding a real blog post yields 2–4
  schema-valid proposed notes with provenance.*
- **2.3** Validator (the 7 rules, §4) as a pure function over a proposal.
  *Check: unit tests — each rule rejects a crafted bad proposal.*
- **2.4** Approval queue UI: diff view, edit-before-approve, reject with
  reason. *Check: edited proposal re-validates before approve enabled.*
- **2.5** Approval handler: lock → pull --rebase → write → validate →
  commit `feed: <source-id>` → push → reindex → unlock; rollback on any
  failure. *Check: kill the push mid-flight → Feed marked failed, working
  tree clean, repo consistent.*
- **2.6** Round-trip proof: feed online → approve → `git pull` locally
  shows the commit; feed locally via Claude Code → push → appears in the
  online browser. *Check: both directions, no divergence.*

### M3 — Smart reader + chat
- **3.1** `assemble_context` endpoint: reader agent over the clone, returns
  context pack + entity_ids + tokens; visibility-capped by caller tier.
  *Check: same task at public vs private tier returns different packs;
  private entities never appear at lower tiers.*
- **3.2** Chat: sessions, tier switcher, sources panel (entities +
  visibility + staleness + verbatims), per-message token counts.
  *Check: asking about Qissatuna in public mode returns nothing private.*
- **3.3** Event + token dashboards: filterable event stream, SdkOperation
  ledger with day/week aggregates, most-served notes.
  *Check: numbers reconcile with raw table queries.*
- **3.4** Transport decision for chat streaming (see open question 4) —
  implement the simplest honest option.

### M4 — Consumers migrate + prove accuracy
- **4.1** Run `eval/` falsification against the live endpoints vs the same
  questions answered locally in Claude Code; compare. *Check: no
  regressions attributable to the server path.*
- **4.2** Point My-Agents-Team + content pipelines at the MCP/REST surface
  with `agents-only` keys. *Check: one real task per consumer produced
  from online context only.*
- **4.3** Feed a project card for `my-brain-web-app` into the brain (via
  mind-feeder, gated as always). *Check: card indexed and served.*

## 10. Open questions (settle during M1)

1. Postgres (starter default) vs SQLite — keep Postgres; the stack ships it
   and events/dashboards benefit.
2. Strip billing/plans now or leave dormant — proposal: leave dormant, strip
   only UI traces.
3. ~~Where the skills live~~ — resolved: they are at
   `my-brain/.claude/skills/{mind-feeder,mind-reader}` but `.claude/` is
   currently **untracked** in the brain repo. It must be committed before M1
   so the server clone gets the skill prompts.
4. Streaming transport for chat (SSE via worker vs polling) — decide in M3.
