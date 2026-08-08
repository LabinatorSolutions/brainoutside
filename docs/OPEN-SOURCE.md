# Open-source checklist (M5 — running doc)

Decided 2026-07-30: the app goes open source as a self-hosted
"build your own brain" tool. **MIT.** Beta release after M3/M3.5; the
M4 eval then runs in public. Scope: single-user, single-brain — that is
the product's identity, not a limitation to fix.

**Name (decided 2026-07-30): `BrainOutside` — brainoutside.com.**
Repos `brainoutside` + `brainoutside-template` (renamed from `brain-template` 2026-08-02; in-tree dev folder keeps its name); image
`ghcr.io/<owner>/brainoutside`. Identifier table lives in
[SETUP-DESIGN.md](SETUP-DESIGN.md); the onboarding flow is designed
there too.

This is a *running* checklist: items get ticked (and added) continuously
as we build, so M5 is a release step, not an excavation. Rule for every
new feature from now on: **"is this engine, or is this Hasan-config?"**
Hasan-config goes in settings/DB/brain-repo, never in code.

## De-Hasanify (5.1) — continuous

- [x] Commit identity → env settings `BRAIN_COMMIT_NAME`/`BRAIN_COMMIT_EMAIL`
      (generic default `brain-app <brain-app@localhost>`; Hasan's value
      lives in his .env).
- [x] Theme: `lwh`/learnwithhasan naming → neutral token names (the
      palette itself can stay — it's just a default theme). *Verified
      2026-08-02: `lwh` greps clean in assets/, static/css/, templates/
      (remaining hits are minified-vendor-JS substrings).*
- [x] Audit for hardcoded personal values: `learnwithhasan`,
      `hassancs91`, `brain.` domains, Windows paths. Engine code must
      grep clean; docs/history references are fine. *Verified
      2026-08-02: HEAD engine tree greps clean on all four (one
      `D:/repos/x` string in a gitrepo.py comment is a generic URL-parse
      example, not a personal path).*
- [ ] GitHub-only assumptions (webhook, PAT push URL) — fine for v1,
      documented as such; GitLab/Gitea is post-release territory.

## Starter template (5.2)

- [x] `brain-template/` — folder skeleton, generalized CLAUDE.md contract,
      `_TEMPLATE.md` per note kind, mind-feeder + mind-reader skills
      (both with server-mode), one example lens, placeholder identity
      files with writing guidance inside, seed INDEX.md.
      Lives in this repo for now; split out to its own public repo at
      release. *Published 2026-08-02 as `hassancs91/brainoutside-template`;
      the in-tree copy is now the dev fixture (sync deliberately —
      LAUNCH.md §2).*
- [x] Generalize the contract text: "the owner" throughout, scope rule
      replaces the Arabic-corpus rule, taxonomy is an editable example.
- [x] App's startup contract check = template validator (already loud) —
      verified by booting the app against a fresh clone of the template.
- [x] Decide whether the example project card ships or the template
      starts fully empty (it currently ships one, marked DELETE THIS).
      *Decided 2026-08-02: ships — one card + one lens, zero knowledge
      notes; both already carry delete-me guidance.*

## Setup experience (5.3) — designed in SETUP-DESIGN.md

- [x] Auto-generate + PERSIST boot secrets, so required env drops to 2
      (`POSTGRES_PASSWORD` + domain). Persistence is load-bearing: a
      regenerating `FIELD_ENCRYPTION_KEY` silently destroys every stored
      credential. Written once to `$BRAIN_STATE_DIR/boot-secrets.json` on
      a new `brain-state` volume; generation is locked so web/mcp/worker
      racing on first boot cannot diverge. `OAUTH_ISSUER` /
      `PUBLIC_BASE_URL` now derive from `ALLOWED_HOSTS` (the template's
      inherited boot-refusal on `OAUTH_ISSUER` guarded OAuth flows this
      app doesn't vendor, and would have been a third required var).
- [x] First-run wizard at `/setup`: admin → create brain repo (deep link
      to `brain-template/generate`, no token needed) → deploy key with
      copy button + Verify → write PAT → Claude key OR `sk-ant-oat` →
      bootstrap. No `docker exec` anywhere. Walked end-to-end in a browser
      on a blank prod-settings instance.
- [x] Git credentials → encrypted settings (+ app-generated SSH keypair),
      env/file overrides win when present. `ssh-keygen -y` independently
      derives the same public half, so the generated key is a real one.
- [x] Dashboard setup-health panel — the ops-UI-is-public warning is the
      one that actually protects novices. Plus `/ops/health/` with the
      repair actions (verify, pull, rebuild, replace the clone, rotate the
      deploy key, generate a webhook secret).
- [x] Subscription-token support is a headline feature (no API billing) —
      offered as a first-class choice on the wizard's Claude step.
- [x] `docker compose up` happy path documented in ≤10 lines —
      *done 2026-08-08: the six-line block at the top of
      `docs/INSTALL.md` (clone, .env, loopback-bind override, up,
      proxy, wizard).*
- [ ] Decide the CSP inline-style question (see the release blocker
      below) — until then a deployed install is functional but unstyled.
- [x] The wizard's template deep link 404s until `brainoutside-template` is
      published as its own repo (5.4). *Published + template-flagged
      2026-08-02; final `/generate` click-through pends GitHub web-tier
      recovery (503s on all HTML pages at check time).*
- [ ] Changing `BRAIN_REPO_URL` after setup is safe (the server refuses to
      serve a mismatched clone, and "Replace the clone" repairs it), but
      the wizard does not yet offer that repair inline — it only appears
      on the health page.
- [x] First boot with a valid clone + empty DB auto-indexes + builds
      snapshots (`brain_bootstrap`) — no "empty brain" trap on day one.
- [x] `sync_brain --no-pull` for credential-less clones (local dev,
      host-owned pulls).

## UI rewrite (5.6) — designed in UI-REWRITE.md, runs BEFORE 5.4

Decided 2026-07-31: launch WITH the better UI. Full detail and build
order in [UI-REWRITE.md](UI-REWRITE.md).

- [x] Restore a real Tailwind build — v4.3.3, standalone CLI binary, no
      Node or `node_modules`, `tw.css` stays committed so self-hosters
      need no toolchain. `.\dev.ps1 css [-Watch]` + the POSIX twin, both
      reading one pinned version. `tailwind.config.js` deleted (v4 is
      CSS-first). `@tailwindcss/forms` + `typography` both load in the
      standalone binary — the one unknown, checked first.
- [x] `tokens.css` → Tailwind `@theme`, so `bg-surface` / `border-line` /
      `text-muted` are real utilities. Uses **`@theme inline`**, which is
      load-bearing: a plain `@theme` resolves the var() at `:root`, so a
      `.dark` subtree inherits the already-resolved light colour.
- [x] Component layer (btn family, card, table, badge, dot, field,
      stat-tile, notice, meta-grid, empty-state, diff lines) + a living
      style guide at `/ops/styleguide/` (staff-only AND DEBUG-only). It
      found the `@theme inline` bug on its first render.
- [x] Convert all templates — **455 → 0** inline `style="…"` attributes.
- [x] **Dark mode ships.** `tokens.css` has a `.dark` block; the toggle
      that used to toggle nothing now works. Status colours needed a
      second token each (`--signal-ink` / `--warn-ink` / `--danger-ink`):
      the fills fail contrast as text (#4db8a8 is ~2.1:1 on paper).
- [x] Sidebar active state — `nav.py` emits it now, longest-match so the
      root-mounted dashboard doesn't light up on every page.
- [ ] Rest of the redesign: page furniture, table density, responsive
      (5.6.3.2–.4). No horizontal overflow at 390/820/1440 today, but
      nothing has been designed FOR those widths yet.
- [x] Guardrails: **CSP enforced in dev**; tests that fail on an inline
      style attribute, on a nonce-less inline `<script>`, on a multi-line
      `{#…#}` comment, on duplicate `class` attributes, and on `tw.css`
      differing from a fresh build. 39 tests pass.
- [ ] Launch assets last, against the shipped UI — and still not the
      graph explorer until the click-through bug below is fixed.

**Found by enforcing CSP in dev, all previously invisible:** three inline
`<script>` blocks had no nonce and were refused outright — the whole chat
send/stream implementation (the Send button did nothing on every
deployment) and the auto-refresh on tasks + feed detail. Alpine's *string*
`:style` binding is also dropped (it calls `setAttribute`), which killed
the wizard's busy-state affordance; the *object* form goes through the
CSSOM and is fine. A multi-line `{#…#}` comment was printing four lines of
engineering commentary above the fold on the dashboard.

## Cross-platform + docs (5.4)

- [ ] Publish `ghcr.io/<owner>/brainoutside` — multi-arch, semver, built
      on tag by GitHub Actions. + CHANGELOG. *(2026-08-08: the workflow
      (`.github/workflows/release.yml`, amd64+arm64 on `v*` tags, image
      name derived from the repository) and CHANGELOG.md are in place;
      the publish itself happens with the first tag on the public repo —
      Actions never ran in this private one, so the workflow is
      YAML-validated but unexercised.)*
- [ ] Design the contract-version story BEFORE v1.0: users' brains are
      copies of `brain-template` that never update, and we cannot migrate
      a repo we don't own. Plan: `contract-version:` in CLAUDE.md,
      startup check warns (never fails), `upgrade_brain` proposes the
      diff through the normal approval queue. *(2026-08-02: the template
      half is done — `contract-version: "1.0"` frontmatter + §10 in
      brain-template/CLAUDE.md. 2026-08-08: the warn is done too —
      `CONTRACT_VERSION` beside CONTRACT_PATHS, `contract_version_probe()`
      returning ok/older/newer/unknown, and a health row that can only be
      "ok" or "warn". Fail-open is structural, not a convention:
      `verify_contract()` stays silent about the version and
      `status_probe()` reports it beside `contract_ok` rather than folded
      into it, because readyz gates on that flag. The bump rule lives in
      the constant's docstring — bump when an existing brain would be
      SERVED DIFFERENTLY, not when the template gains text. Verified live:
      the real brain declares nothing and reports "unknown" while
      `contract_ok` stays True. **Only `upgrade_brain` is left**, and §10
      hedges it ("may propose the upgrade as a diff"), so shipping without
      it is not a broken promise.)*
- [x] bash/Makefile twin of `dev.ps1` — *done 2026-08-08: `dev.sh`,
      same commands/compose files/project name. Syntax-checked on Git
      Bash + Linux bash; `ps`/`status` ran against the live stack. NOT
      verified: `up`/`css` on a real Linux/macOS box (the fresh-box
      item below).*
- [x] README: what/why, quickstart — rewritten as BrainOutside.
- [ ] Rings hero image for the README (M3.5.2 dashboard centrepiece is
      the shot; do NOT film the graph explorer until the lens/click bug
      above is fixed).
- [x] SECURITY (the §9 honest truths — "private notes are only as
      private as your VPS"). *Written 2026-08-07 as `docs/SECURITY.md`;
      it was already cited from `.env.example`, the wizard's write step,
      the `BRAIN_GIT_WRITE_PAT` help string and `gitcreds`, all of which
      pointed at a file that did not exist.*
- [x] INSTALL (Coolify/compose) — *done 2026-08-08: `docs/INSTALL.md`
      (compose happy path + Coolify pointer + updating); DEPLOY.md §7
      corrected at the same time — the 15-min sync beat exists now, so
      "there is no periodic pull" stopped being true.*
- [ ] Verify clean setup on a fresh Linux/macOS box.

### Docs site outline (write at 5.4 — until then this list IS the doc)

Sources per page in parentheses. Voice: newcomer-facing — PLAN.md and
CLAUDE.md are engineering/agent-voiced and get rewritten, not copied.

1. [ ] **Getting started** — template repo → compose up → wizard →
       first feed → first query; the 10-minute happy path (5.3 wizard).
2. [ ] **Concepts** — what a brain repo is, the contract in human
       terms, note kinds, visibility tiers, supersede-never-delete,
       lenses (brain-template CLAUDE.md).
3. [ ] **Feeding** — the three channels, extraction, approval gate,
       validator rules in plain language (PLAN §4–§5).
4. [ ] **Reading** — read endpoints, assemble-context, MCP setup for
       Claude Code and other agents (grow from apps/docs/guides/).
5. [ ] **Self-hosting** — compose/Coolify, backups, updating
       (DEPLOY.md, de-Hasanified).
6. [ ] **Security & privacy** — tiers enforced server-side, the §9
       honest truths, credential split (PLAN §9).
7. [ ] **Troubleshooting / FAQ** — running list; seed: empty-brain
       first boot (fixed in code), credential-less pulls (--no-pull),
       `sk-ant-oat` vs API key, Coolify empty-env-var trap.
8. [ ] **API reference** — auto-generated from the endpoint registry;
       link, don't write.

## Known issues to close before the beta

- [x] **RELEASE BLOCKER — CLOSED 2026-07-31 by M5.6.2.** Every inline
      style attribute is gone (455 → 0 across 45 templates) and a test
      keeps them gone, so the CSP never needed loosening: the
      `style-src-attr` band-aid was never applied and is no longer
      needed. The policy that ships is the strict one, and it is now
      strict about things that actually work. Historical detail below,
      kept because the *reason* it hid for so long is the lesson:
      `dev.py` defaulted to report-only, so every visual check this
      project ever did ran on a permissive stack. Dev now enforces.

      **The enforced CSP strips every inline style, so the ops UI is
      largely unstyled on real deployments.**
      `apps/core/security/headers.py` sets
      `style-src 'self' 'nonce-<per-request>'`. A nonce authorises inline
      `<style>` *blocks*; it does nothing for `style="…"` *attributes*,
      which require `'unsafe-inline'` — and per CSP3, when a nonce is
      present `'unsafe-inline'` is ignored, so the two cannot simply be
      combined. Every ops template carries the design tokens as inline
      style attributes (`style="background: var(--accent)"`,
      `style="border-color: var(--line)"`), so all of them are dropped.
      **Why nobody noticed:** `dev.py` sets `CSP_REPORT_ONLY=True`, so the
      local stack logs ~104 violations and renders correctly, while the
      deployed stack (`prod.py`, enforcing) renders the same page with
      transparent buttons and no surface colours. Every visual check to
      date, including the M3.5 ones, ran on the permissive stack.
      *Measured 2026-07-31, same page on both stacks: "Test connection"
      button background `rgb(99,102,241)` under dev vs `rgba(0,0,0,0)`
      under prod; ~105 `Applying inline style violates…` console errors.*
      Three ways out, none of them free — this needs a decision, not a
      default:
      1. Drop the nonce from `style-src` and use `'unsafe-inline'`. Two
         lines. Weakens CSP against CSS injection, which is not academic
         here: the brain browser renders note markdown, so injected
         content reaches the page.
      2. Move the tokens out of style attributes into real CSS classes.
         Correct, and it is the M5 UI rewrite.
      3. `'unsafe-hashes'` plus a hash per distinct attribute value —
         impractical to maintain.
      Note the fix is NOT "add unsafe-inline alongside the nonce"; that is
      a no-op.

- [x] **The display font is not self-hosted — CLOSED 2026-08-08.**
      `SpaceGrotesk-Variable.woff2` (v2.0.0, wght 300–700, OFL) is
      vendored next to the others with an `@font-face` carrying the
      variable range — variable rather than static instances because
      the UI uses weight 600, which Space Grotesk ships no static file
      for. `static/fonts/LICENSES.md` now records all three families'
      OFL attribution. Verified in Chromium against the running stack:
      the woff2 serves 200, `document.fonts` lists the face as
      `loaded` (an OS-installed font never appears there, so this
      proves it came from the server), and headings compute to it.
      Historical detail below — the reason it hid was worth keeping: `--font-display` is `'Space Grotesk', system-ui, sans-serif`,
      but `static/fonts/` contains only Inter (4 weights) and JetBrains
      Mono (2), and the build emits `@font-face` for exactly those.
      Measured in Chromium against the prod instance:
      `document.fonts.check('16px "Space Grotesk"')` returns true here
      **because the font is installed on this laptop**, while
      `document.fonts` lists no Space Grotesk face. Every heading in the
      app therefore renders Space Grotesk for Hasan and `system-ui` for
      every self-hoster — and every screenshot taken so far shows the
      former. Fix before M5.6.4 launch assets: vendor the woff2 (SIL OFL
      1.1) next to the others and add the `@font-face`, or drop it from
      the stack. Not a deploy blocker; purely cosmetic, but it makes the
      marketing shots dishonest.

- [x] **Snapshot swap is not atomic — CLOSED 2026-08-07 by `4d0388e`**
      (the hardening pass). The swap was `rmtree(final)` then
      `rename(tmp, final)`: a whole-tier delete during which every read
      422'd, and a process killed between the calls left no directory
      at all. Now every tier is STAGED first and swapped after
      (`stage_tier` / `swap_in`), the swap renames the live directory
      aside instead of deleting it — a two-rename window, with the
      previous snapshot kept on disk — and
      `recover_interrupted_swaps()` restores a tier whose swap was
      interrupted, at the start of the next build. The same commit made
      a failed publish mark its `SyncRun` not-ok (it used to stay
      green). 15 tests in `test_snapshot_publish.py`, 12 of which fail
      against the unfixed code. The Windows bind-mount
      `PermissionError` now costs disk, not correctness (the trailing
      cleanup tolerates it).

- [ ] **Graph explorer: click-through breaks after using the lens picker.**
      Clicking a node opens its note reliably on a fresh page load (6/6),
      but once a lens has been applied the click lands on the canvas
      background instead of the node (0/6) — Cytoscape reports the tap on
      the core while the node is drawn under the cursor with an identical
      bounding box, `opacity: 1` and `events: yes`. Ruled out: toolbar
      reflow from the scope caption (fixed anyway, `.gx-scope` now
      reserves its line), and `cy.batch()` around the class changes
      (reverted, made no difference). Not root-caused. Ops-only surface
      and the note view is reachable from the brain browser, so it is not
      a release blocker — but it should not ship in a demo GIF.

## Repo hygiene (5.5)

- [x] History audit — **CLEAN** (run 2026-08-02: 109 commits, single
      branch, no tags, no stash).
      Method: full `git log --all -p` dump grepped for key shapes
      (`sk-ant`/`ghp_`/`github_pat_`/`AKIA`/`AIza`/`xox*`/`glpat`),
      private-key blocks, and secret-shaped `X=<long-value>`
      assignments — zero hits. Every path ever tracked reviewed: no
      `.env`, no `db.sqlite3`, no dumps, no `data/`; only binaries are
      fonts + favicons. `.env.example` was placeholder-only in every
      version. Every `VERBATIM` hit is rule code or templates — no real
      brain content ever landed. All IP-shaped strings are example.com
      / TEST-NET test fixtures. Personal *references* in docs and old
      code versions exist and are acceptable per the 5.1 rule.
      ~~Decided 2026-08-02: publish with FRESH history anyway.~~
      **REVERSED 2026-08-08 by the owner: the repo went public with its
      full history, deliberately.** The original reasoning (commit-email
      exposure, clean-by-construction optics) gave way to a launch
      deadline plus a better argument that surfaced late: CONTRIBUTING
      tells readers "commit messages are the project's real changelog",
      which is only true if the log exists — 216 commits of narrated
      engineering is a build-in-public asset a squash would delete.
      Before the flip stood: a fresh full-history secret scan (every
      credential shape incl. this app's own `mcpsk_`/`mcpurl_`
      prefixes, plus a secret-shaped-assignment pass over the ~107
      post-audit commits) — clean, test fixtures only. The personal
      commit email on all 216 commits is the accepted, known cost.
      Do not re-litigate; there is no archived-private twin to
      maintain.
- [x] LICENSE (MIT), CONTRIBUTING, issue templates — *done 2026-08-08.
      CONTRIBUTING states the single-user boundary up front; the bug
      template asks for `/ops/health/` + logs; contract questions route
      to the template repo's Discussions (enable Discussions there —
      LAUNCH §2 — or that link 404s).*
- [x] Vendored template code (mcp-api-starter-template) license header —
      *2026-08-08: the owner directed a public README credit to the MCP
      API boilerplate it came from and approved the release-packaging
      pass that adds the MIT LICENSE — treating that as the
      confirmation this item waited on. Say so if that's wrong.*
- [x] Vendor the graph JS lib — no CDN anywhere. Cytoscape 3.34.0 (MIT)
      for the 3.5.3 force layout only; rings (3.5.2) and timeline
      (3.5.5) are plain SVG, since a polar layout of ~50 circles and a
      bar chart don't justify a dependency someone self-hosting has to
      trust. `static/vendor/README.md` records version + license per lib
      and documents the refresh procedure.
