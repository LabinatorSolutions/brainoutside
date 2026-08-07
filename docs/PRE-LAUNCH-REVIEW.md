# Pre-launch code review — findings

Adversarial bug hunt across the whole codebase ahead of the open-source
release. **Report only — nothing in this pass was fixed.** Security proper
is deliberately deferred to a separate later pass; only tier-enforcement
*correctness* and incidental critical notes appear here.

Method: ten parallel reviewers, one per area, each required to verify a
finding against the full call path (callers and callees) before reporting.
Claims marked **[verified]** were additionally re-checked by hand against
the source after the reviewers reported.

Baseline: `pytest apps/core/tests apps/core/mcp/tests` — **177 passed**.

---

## Verdict

The engine is well-built and the security scaffolding is real (CSP enforced
in dev, boot-time prod assertions, race-safe boot secrets, atomic approval
claim, honest secret handling in the ops UI). What is *not* ready is the
open-source surface: a stranger cloning this today hits install-blocking
hosting defaults, docs describing a different product, and several features
that are wired up in the UI but enforce nothing.

Three classes of problem dominate:

1. **The visibility model has holes in three independent places.** Tiers are
   the product promise, and each hole leaks in a different direction.
2. **Vendored SaaS-template residue.** Whole subsystems (billing, webhooks,
   audit sinks, job hooks) are documented, exported and dead. Three of five
   public docs guides describe features that do not exist.
3. **Everything outside `apps/core` is untested.** The git sync engine, the
   approval pipeline, the reader, and the setup wizard have zero automated
   coverage — and that is exactly where the worst findings landed.

---

## P0 — blocks the release

### 1. The reader agent can read above its caller's tier [verified]

`apps/reader/services/sdk_runner.py:130-134`

```python
allowed_tools=[f"Read({tier_path.as_posix()}/**)", "Grep", "Glob"],
```

`Read` is carefully path-scoped to the caller's tier snapshot. `Grep` and
`Glob` sit right beside it as bare tool names, which under
`permission_mode="dontAsk"` pre-approve *any* arguments — including an
absolute `path=` outside `cwd`. All three tier snapshots share one mounted
volume in every container that runs this.

A `public`-tier consumer calls `assemble-context` (whose `task` input the
code itself annotates "UNTRUSTED consumer input") and the agent greps
`/data/brain-views/private` with `output_mode="content"`. Private note text
returns in the grep output and lands in `context_pack`. `_verify_entities`
filters the returned *id list*, never the pack bytes.

This defeats the structural claim in `snapshots.py:5-9` — "an agent
physically cannot read above tier". The asymmetry with the scoped `Read`
one line up says the omission was unintentional. Worth confirming with a
live agent run before designing the fix, since it rests on Claude Code's
permission semantics.

### 2. `raw/../` in a note body pulls any repo file into the public snapshot

`apps/brain/services/snapshots.py:45,61-74,139-141` [verified]

`_RAW_REF_RE = r"raw/[A-Za-z0-9_\-./]+?\.md"` permits `.` and `/`, so `..`
segments match; `_linked_raw_paths` only checks `is_file()`. Any *public*
note containing `raw/../knowledge/takes/private-note.md` emits that private
file's full text into the **public** snapshot. Reachable through the feed
approval pipeline.

### 3. `get-raw` escapes `raw/` and bypasses the DB tier gate [verified]

`apps/mind/endpoints.py:263` + `apps/mind/files.py:20-28`

The restriction is `inp.path.startswith("raw/")` — a prefix test, not a
normalization. `files.read` then guards containment in the snapshot but not
in `raw/`. So `get-raw {"path": "raw/../_MANIFEST.json"}` returns the tier
manifest, and `raw/../notes/x.md` returns any note in the snapshot —
without the `tiers.allows` DB check that `get-note` applies. No escalation
beyond the caller's own snapshot on its own, but it is the amplifier for
finding 5 below.

### 4. A file rename permanently bricks sync [verified]

`apps/brain/services/indexer.py:291-306`, `apps/brain/models.py:32,34`

`entity_id` and `path` are both `unique`; `rebuild()` creates new paths
*before* pruning old ones. Rename a note while keeping its frontmatter
`id:` — the normal case, since `id` is the stable identity — and
`Entity.objects.create(path=new, entity_id=same)` raises `IntegrityError`
while the old row still holds the id. No `transaction.atomic`, so earlier
writes are already committed and the DB is left mixed.

Every retry fails identically. The "Replace the clone" repair re-clones and
calls the same `rebuild`. **Sync is dead until manual DB surgery or
renaming the file back** — deleted content keeps being served, new content
never appears.

### 5. BOM or malformed YAML silently downgrades `visibility: private`

`apps/brain/services/indexer.py:41,66-76,138-141`

`_FRONTMATTER_RE` anchors on `\A---` and files are decoded as plain `utf-8`,
not `utf-8-sig`. A UTF-8 BOM (Notepad's default for years) makes frontmatter
matching fail outright; a YAML typo makes `safe_load` raise and `fm` become
`{}`. Either way `_resolve_visibility` falls back to the *path* default, so
a note explicitly marked `private` is indexed `agents-only`, copied into
the agents-only snapshot, and served. One bad file doesn't crash the index —
it silently mis-tiers it, which is worse.

### 6. Hosting defaults that break or expose a stranger's install

| | |
|---|---|
| `docker-compose.yml:57-58` [verified] | `ports: - "8000"` publishes web to a **random host port on 0.0.0.0**. The file's own header and DEPLOY.md say only the proxy exposes web. A port scan reaches the origin over plain HTTP, around TLS and Cloudflare Access. For plain `docker compose up` users the documented `localhost:8000` also never works. |
| `config/settings/prod.py:28-53` [verified] | If `DATABASE_URL` is unset or not `postgres://`, prod **silently keeps base.py's SQLite at `/app/db.sqlite3`** — container-local, on no volume, no warning. Works for weeks, then a redeploy wipes feeds, events, keys, ledger. |
| `docker-compose.yml:98` | Redis is the Q2 broker but runs `--maxmemory-policy allkeys-lru --appendonly no`. Under pressure Redis may evict **queue** keys: enqueued approvals and extractions vanish silently. Needs `noeviction` or a second instance. |
| `config/settings/prod.py:66-67` | Behind a proxy that doesn't send `X-Forwarded-Proto`: infinite 301 loop. The only documented escape (`SECURE_SSL_REDIRECT_ENABLED=0`) isn't in `.env.example`, and after setting it **every POST fails CSRF** because no `CSRF_TRUSTED_ORIGINS` setting exists anywhere. The install is unrescuable without a code edit. |

### 7. The documented key-rotation workflow destroys every stored secret

`apps/core/management/commands/rotate_field_encryption_key.py:90-101,149-152`

The command walks `django_cryptography` `EncryptedMixin` fields. This fork
has none — the only encrypted data is `AppSetting.value_encrypted`, a plain
`TextField` of Fernet tokens from `apps/brainconfig/crypto.py`. So the
command prints *"No encrypted fields discovered. Nothing to do."* and exits
0. The operator, following the command's own docstring, then swaps
`FIELD_ENCRYPTION_KEY` and redeploys. Every `AppSetting` read now raises
`InvalidToken`, which `AppSetting.value` deliberately swallows, returning
`""`. The Anthropic key and webhook secrets silently read as unset, with no
error anywhere. Recovery only by restoring the old key.

### 8. `config/scheduled.py` does not exist [verified]

`apps/core/management/commands/sync_scheduled.py:25` imports
`config.scheduled`, which is absent from the repo. The command dies with
`ModuleNotFoundError`, so **no Q2 schedule row is ever created**:

- idempotency keys are never purged → completed responses replay forever,
  and stuck in-flight rows 409 forever instead of clearing at 24h;
- `cleanup_sdk_transcripts` (plaintext note content under
  `~/.claude/projects/`) is referenced by nothing → grows forever;
- `Event` / `SdkOperation` have no pruning path at all;
- no periodic brain pull — a stale brain is served indefinitely if the
  webhook isn't wired.

DEPLOY.md acknowledges the gap, but a broken management command ships.

### 9. Zero-entity build traps the operator in the wizard forever [verified]

`apps/brainconfig/setup_state.py:126-130`, `setup_views.py:350-356`,
`middleware.py:55-63`

`brain_built()` requires `Entity.objects.exists()`. A build that succeeds
but indexes 0 entities marks the job done, never satisfies `is_complete()`,
and the middleware bounces the operator off every `/ops/` page back to
`/setup/` — where "finish" has just cleared progress, so they see a fresh
"Build my brain" button. Infinite loop, with nothing anywhere saying "you
need at least one indexable note". The official template ships 3 identity
files, which is exactly why this only surfaces on strangers' machines.

### 10. Three of five public docs guides document a different product

`apps/docs/guides/webhooks.md` (entire file), `rate-limits.md`, `errors.md`

`webhooks.md` documents an **outbound** webhook subscription system —
`subscription.activated` / `credits.granted` events, `whsec_` signing
secrets, `X-Mcp-Signature`, retry schedules, a "Send test event" button on
`/dashboard/webhooks/`. None of it exists; the only webhook is the inbound
GitHub push hook, and there are no `/dashboard/*` routes at all.
`rate-limits.md` documents Free/Pro **plans** and contradicts `auth.md` on
whether an anonymous tier exists. `errors.md` describes Stripe and tells the
sole operator of their own server to "open a support ticket".

Also: `mcp-setup.md:15-21` states the URL-token connector surface "is not
enabled here, so Claude.ai's custom connector flow will not complete" —
a static claim about a runtime flag, now directly contradicted by the
`/ops/connectors/` page built for exactly that flow. And
`endpoint_detail.py:416-430` renders a Claude Desktop config in `{"url",
"transport", "auth"}` shape, which Claude Desktop cannot parse; the app's
own guide correctly prescribes the `mcp-remote` shim.

---

## P1 — high

### Enforcement that looks live and does nothing

- **`admin_only` is inert** [verified] — `rest.py:150-163`, `urls.py:43-66`,
  `mcp_proxy/views.py:652` all gate on `principal.user.is_staff`. Setup sets
  `is_staff = True` on the single account every credential resolves to, so
  the flag is never consulted. Ship an endpoint "dark", hand out a
  public-tier connector URL, and the endpoint answers 200 and appears in
  `_catalog`.
- **Maintenance mode is never enforced** [verified] — `MaintenanceModeMiddleware`
  is written, exported, and **absent from `MIDDLEWARE`**. Flipping it writes
  the setting, busts the cache, returns success, and the site keeps serving.
  (Note: one reviewer assumed it was wired; it is not. The dead "Try again"
  button on `errors/maintenance.html:22` — an Alpine `@click` with no
  `x-data` ancestor — is a real bug but currently unreachable.)
- **Dark mode is unreachable** [verified] — `templates/partials/_theme_toggle.html`
  is included by no template, and **nothing anywhere reads
  `localStorage.getItem('theme')`**. The `.dark` token block, every compiled
  `dark:` utility, and the explorer's repaint observer are all dead. Its own
  comment ("respects OS default on first visit") is false — there is no
  `prefers-color-scheme` check either.
- **Every error/audit sink is unregistered** — `error_hook`, `audit_hook`,
  `jobs_hook`, `charging`, `lockout` bucket provider: ~15 call sites, all
  permanent no-ops, because the apps their docstrings name
  (`apps.observability`, `apps.audit`, `apps.billing`) don't exist here.
  Consequences: endpoint 500s write no `ErrorLog`; `/_csp-report/` receives
  browser violations and discards them; endpoint-disable, maintenance and
  allowlist changes leave **no audit trail**; the retention cron prunes a
  table that doesn't exist.
- **Google Analytics half-wired** — setting `GOOGLE_ANALYTICS_ID` widens CSP
  to allow Google hosts, but `_analytics.html` and `_cookie_consent.html`
  are included by nothing. Loosened CSP, no analytics, no consent banner.
- **`base.html:8` blanket `noindex, nofollow`** with no override block,
  while `robots.txt` says `Allow: /` and the sitemap advertises the docs.
  Public docs can never index; the sitemap also lists `/privacy/`, which
  isn't a mounted URL.

### The write door

- **Uncaught exception mid-apply serves unapproved content** —
  `apps/feeds/services/approval.py:247-294` handles only `ApplyFailure` and
  `BrainRepoError`. An `OSError` from `mkdir` on a path like
  `raw/existing-file.md/extra.md`, or a `UnicodeDecodeError` from the strict
  `read_text` at `:133,164,172,199`, escapes with **no rollback**. Files
  already written stay in the tree; `pull_rebase --autostash` preserves
  them; then `indexer.rebuild()` + `build_all()` index and serve
  never-committed content.
- **Wedged `approving` has no recovery** — Q2 on the Redis broker has no
  ack; a lost task leaves the feed in `approving` forever, and every UI
  action requires `pending`. Worst case: crash after `git push` succeeded but
  before `feed.save` → commit in the brain, DB says `approving` with empty
  `commit_hash`, permanently, with nothing scanning history for the
  `Feed-Id:` trailer.
- **Push-race replay silently reverts upstream edits** — `approval.py:251-263`
  does `fetch` + `reset --hard origin/<branch>` then re-applies **full file
  contents** with no overlap detection. An upstream commit touching the same
  file is silently overwritten; the commit that lands differs from the diff
  the reviewer approved.
- **Empty-diff commit marks the feed `failed`** — a push that already landed
  remotely replays to an identical tree → "nothing to commit" ×3 →
  `failed` with empty `commit_hash` while the content is live in the brain.
- **A rebase conflict wedges the clone** — `gitrepo.py:328-338` leaves
  `.git/rebase-merge` present on conflict and nothing in the sync path ever
  runs `rebase --abort`; `replace_clone` refuses because the tree reads
  dirty. Never self-heals.

### Snapshots and serving

- **Partial `build_all` leaves tiers disagreeing, reported green** —
  `indexer.rebuild()` writes `SyncRun.ok=True` *before* snapshots build, and
  `build_all` iterates tiers sequentially with no try/except. Fail on tier 2
  and public sits at the new HEAD while the others sit at the old one,
  indefinitely, with the health tile green. Because `get-raw` does no DB
  check (see P0 #3), a note whose visibility was just tightened keeps being
  served from the stale snapshot.
- **The swap is a visible outage, and a crash leaves no directory** —
  `rmtree(final)` then `rename` (`snapshots.py:153-155`). During the window
  every read at that tier returns 422 "unknown path". A kill between the two
  calls leaves **no snapshot for that tier at all** until the next
  successful sync — realistic, because the webhook runs `sync.sync()`
  synchronously inside a gunicorn request under a 60s timeout.
- **Throttle 500s on Redis flakiness** — `apps/mind/throttle.py:81-88`.
  django-redis with `IGNORE_EXCEPTIONS=True` returns `None` from `incr` on a
  connection blip; `_consume` doesn't handle `None`, so `None > limit` raises
  `TypeError` → 500 on **every request** across the whole read surface until
  Redis is back, with a misleading error log.
- **Streaming SDK runs have no wall-clock timeout** —
  `sdk_runner.py:361-364` checks elapsed time only *after* a message
  arrives; the `async for` itself is unbounded (the non-streaming path
  correctly uses `asyncio.wait_for`). A wedged CLI hangs the SSE response
  forever, the Send button stays disabled, the ledger row stays `ok=None`,
  and the subprocess keeps spending invisibly to the daily-cap breaker.

---

## P2 — medium (selected)

**Tier / credential consistency**

- A `public`-tier connector URL can still **list and call admin-only tools** —
  the tier constrains notes, not tools, and `URLMCPToken` inherits the
  owner's `is_staff` (`mcp_proxy/views.py:652`).
- `_visible_entities` uses `TIER_ORDER[tier]` → **`KeyError` → 500** where
  every sibling check uses `.get(..., default)` and fails closed
  [verified]. The URL-token path sanitizes its tier; the APIKey path does
  not.
- `throttle.check` still has the uncaught-`ValueError` shape that commit
  42a7682 fixed *for URL tokens specifically*: `filter(api_key=<non-APIKey>)`
  raises for any future third credential type — the same 500-on-every-call
  the commit message describes.
- MCP sunset/deprecation/charge gates **skip every `v2+` endpoint** — they
  feed the raw `slug__v2` tool name to `registry.by_slug`, which matches on
  bare slug; the admin gate two lines up strips the suffix correctly.
- A JSON-RPC **batch** body escapes throttle, charge, admin gate, sunset and
  audit, because all of them hang off a `log_slug` that is only set for a
  single-object `tools/call`.

**Attribution and accounting**

- **URL-token reads are entirely unattributed** — `Event.consumer` is an FK
  to `APIKey`, so `_cred()` nulls every connector read. Combined with the
  fact that `EndpointCalled` is never fired on the REST path (its
  `RequestLogMiddleware` is not installed), a heavily-used connector reads
  as never used — the one fact an operator checks before revoking.
- Timed-out / disconnected / errored SDK runs record `cost_usd=NULL`, so the
  daily-cap breaker systematically undercounts real spend.
- No concurrency guard on a chat session: two tabs interleave messages,
  corrupt the replayed history slice, and lose token-total updates.

**Setup and settings**

- "Test connection" **saves the Claude key before testing**, and the step is
  marked done on presence alone — a failed test still completes setup with a
  dead credential. The adjacent write step deliberately does the opposite.
- `/ops/settings/` accepts un-normalized values the wizard carefully
  validates: pasting `myname/brain` into `BRAIN_REPO_URL` stores it verbatim,
  turns the clone check red, and makes the offered repair fail.
- Clearing a required setting instantly ejects the operator from the entire
  ops UI, including the settings page they'd use to fix it.
- `DAILY_COST_CAP=nan` passes validation (`Decimal("nan")` constructs fine)
  and then raises `InvalidOperation` *outside* the guard — every SDK run and
  the usage dashboard 500.
- Before the first admin exists, **all six wizard steps are open**, not just
  the account step; some anonymous paths 500 and one enqueues worker jobs.
- A transient DB error makes `needs_first_admin()` return True, so an
  established install shows the world "Create your account".
- Wizard "Verify" runs a 180s `git clone` **inline in the web request**
  under a 60s gunicorn timeout — the project's own worker rule, applied on
  the health page, skipped on the fresh-install path.

**Idempotency**

- A request killed after the INSERT strands a `Pending` row: `CancelledError`
  is a `BaseException` and escapes `except Exception`, so a `docker compose
  up -d` mid-call 409s that key **for 24 hours** — and, per P0 #8, the purge
  that would clear it never runs.
- The pre-`run()` half of the pipeline has no exception handler at all, so a
  DB blip during bearer resolution returns **HTML** from `handler500`, not
  the documented JSON error contract.

**Cache-aside races** — read-then-populate with no interlock lets a
concurrent reader re-cache a stale value *after* the writer's invalidation:
30s for endpoint disable ("this is broken — keep it off"), 300s for
maintenance mode and the admin IP allowlist.

**Async pipeline collapse** [verified] — `whitenoise.middleware.WhiteNoiseMiddleware`
and `apps.brainconfig.middleware.SetupRequiredMiddleware` are both sync-only
(no `async_capable`), so Django wraps the entire inner chain in
`async_to_sync`. Every request crosses the boundary four times and pins two
threadpool threads; `assemble-context` at 5–30s plus the 4s `activity.json`
poll saturates the pool silently. This is exactly the pathology
`log_scrub.py:88-96` documents and designs around at the outer edge.

---

## Cleanup for open source

**Hardcoded personal / product values** (the repo's own "engine must grep
clean" rule):

- `apps/mind/endpoints.py:251` — `"e.g. 'raw/toolerbox-tools-catalog.md'"` in
  the `get-raw` **input schema**, which ships in `/docs/` and in every MCP
  `tools/list` payload.
- `apps/mind/endpoints.py:81` — `service="my-brain-web-app"` hardcoded in the
  public `ping` response while `settings.APP_NAME` exists.
- `apps/brainconfig/setup_views.py:122` — template repo under a personal
  GitHub account. Probably intentional, but worth a deliberate decision.
- `apps/feeds/validator.py:29-32,188-191` and `approval.py:37-41` hardcode
  one specific brain's layout and an Arabic-script ban.
- `errors/500.html` offers "Email support" at `support@example.com` on every
  default install, and claims "our team has been notified" (nothing notifies).

**Dead code shipping in the release**

- Billing/credits apparatus end to end: charge CM, refunds, 402 mapping,
  `/dashboard/billing/` links. No endpoint declares a non-zero
  `credits_cost`, and no backend is registered — `charge()` is always a
  no-op. Credits chips still render in the docs UI.
- Background-jobs surface (`ctx.enqueue`, `jobs_hook`, `async_timeout_seconds`) —
  nothing registers a backend; real code calls `django_q.tasks.async_task`
  directly. Both `RuntimeError` messages tell the operator to run
  `make worker`; there is no Makefile.
- `apps/core/resilience.py` — zero callers. (Its `CircuitBreaker` also admits
  unlimited concurrent calls in half-open state, and `retry(attempts=0)`
  raises `AssertionError` instead of the wrapped error.)
- `apps/core/permissions.py` — `OwnerOnlyManager` has no users and describes
  a cross-user gate impossible in a single-user product.
- Templates: `data_table.html`, `_toast.html` (included on **every** ops and
  docs page, triggered by an HTMX header nothing sends), `_impersonation_banner.html`
  (references a nonexistent app and an unregistered URL name → 500 if ever
  included), `_skeleton.html`, `_empty_state.html`, `errors/503.html`.
- `static/vendor/htmx.min.js`, `apexcharts.min.js`, `focus-trap.js` — ~700KB
  collected and referenced by nothing. `lucide.min.js` is loaded and
  `createIcons()` runs on every page with zero `data-lucide` elements.
- Upstream phase references and comments describing infrastructure that
  doesn't exist (`docs/SCALING.md`, an opt-in `pgbouncer:` service, Procfile /
  Railway / Fly configs, `apps.observability`). These are the main reason the
  dead hooks read as wired.
- `rest.py:7-9` — a mangled sentence left by an incomplete edit, whose claim
  ("Anonymous calls remain allowed at this phase") is now false.

**`.env.example` drift** — missing every var an operator actually needs on a
non-default deploy: `SECURE_SSL_REDIRECT_ENABLED`, `MCP_URL_AUTH_ENABLED`
(the gate for the whole connectors feature — off by default, so a fresh
install's connectors page mints URLs against a 404ing surface),
`URL_TOKEN_DEFAULT_TTL_DAYS`, the `WEB_*` and `Q_*` tuning vars,
`CSP_REPORT_ONLY`, `SECURITY_TXT_*`, `BRAIN_GIT_WRITE_PAT`,
`BRAIN_DEPLOY_KEY_FILE`. Conversely `DEBUG=false` is dead (nothing reads it),
and `PUBLIC_BASE_URL=http://localhost:8000` permanently defeats the
derive-from-domain logic, so every guide renders localhost snippets forever.

**Compose hygiene** — no `depends_on: web` for mcp/worker in the deployed
file (the local override fixes exactly this race), no `start_period` on the
web healthcheck (first boot exceeds 90s on slow hardware → deploy marked
failed), no redis healthcheck, no `stop_grace_period` or `init: true` (10s
SIGKILL undercuts the 30s drain and orphans Claude CLI children in the
worker), unpinned `fastmcp>=2.0` / `uvicorn[standard]>=0.30` with a
**deprecated** `uvicorn.workers.UvicornWorker` path in the entrypoint.

---

## Test coverage

All tests live under `apps/core`. Nothing anywhere imports `apps.brain`,
`apps.feeds`, `apps.reader`, or `apps.events` in a test. The git sync
engine, the snapshot builder, the proposal→approval→commit pipeline, the
SDK runner, and the setup wizard state machine are **completely untested** —
and that is where the P0 findings clustered. Before inviting contributors,
the approval state machine and the indexer/snapshot pair are the two places
where a regression is both most likely and most expensive.

---

## What held up under review

Worth knowing, so the list above isn't read as a verdict on the whole
codebase:

- The approval **claim** is genuinely atomic — double-approve,
  approve-after-reject, and concurrent double-commit of one proposal are all
  correctly prevented.
- Boot-secret generation is race-safe and fails loudly; `assert_prod_safe()`
  guards are real and fire at boot; `.env` and `secrets/` are dockerignored;
  the entrypoint has `set -euo pipefail`, execs PID 1, and `.gitattributes`
  pins `*.sh` to LF with the exec bit — the classic Windows-repo deploy
  killers are handled.
- Migrations match models (`makemigrations --check` clean).
- Secrets never round-trip through forms; the ops UI is honest about
  unreadable secrets after a key change.
- URL-token rotate/revoke are race-safe; the MCP bridge does all ORM and
  cache work through `sync_to_async`; the loopback identity middleware
  resets its contextvars in `finally`.
- Health checks do no network I/O in-request, so `/readyz` doesn't block on
  GitHub.
- The CSS build is healthy — every utility used in a template is present in
  the committed `tw.css`.
- All POST forms carry `{% csrf_token %}`, and the two documented Alpine
  submit gotchas are correctly deferred everywhere.

---

## Deferred

The security pass (authn/authz depth, injection, SSRF, secret handling,
dependency CVEs, the git-URL argument-injection and symlink-following notes
the reviewers flagged in passing) has not been run yet — that is the next
round.
