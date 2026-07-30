# Open-source checklist (M5 — running doc)

Decided 2026-07-30: the app goes open source as a self-hosted
"build your own brain" tool. **MIT.** Beta release after M3/M3.5; the
M4 eval then runs in public. Scope: single-user, single-brain — that is
the product's identity, not a limitation to fix.

This is a *running* checklist: items get ticked (and added) continuously
as we build, so M5 is a release step, not an excavation. Rule for every
new feature from now on: **"is this engine, or is this Hasan-config?"**
Hasan-config goes in settings/DB/brain-repo, never in code.

## De-Hasanify (5.1) — continuous

- [x] Commit identity → env settings `BRAIN_COMMIT_NAME`/`BRAIN_COMMIT_EMAIL`
      (generic default `brain-app <brain-app@localhost>`; Hasan's value
      lives in his .env).
- [ ] Theme: `lwh`/learnwithhasan naming → neutral token names (the
      palette itself can stay — it's just a default theme).
- [ ] Audit for hardcoded personal values: `learnwithhasan`,
      `hassancs91`, `brain.` domains, Windows paths. Engine code must
      grep clean; docs/history references are fine.
- [ ] GitHub-only assumptions (webhook, PAT push URL) — fine for v1,
      documented as such; GitLab/Gitea is post-release territory.

## Starter template (5.2)

- [ ] Public `brain-template` repo: folder skeleton, generalized
      CLAUDE.md contract, `_TEMPLATE.md`s, mind-feeder + mind-reader
      skills, one example lens, placeholder identity files.
- [ ] Generalize the contract text (remove Hasan-specific projects,
      taxonomy stays an editable example).
- [ ] App's startup contract check = template validator (already loud).

## Setup experience (5.3)

- [ ] First-run wizard: admin → brain repo URL + credential → Anthropic
      key OR `sk-ant-oat` subscription token → bootstrap clone → done.
- [ ] Subscription-token support is a headline feature (no API billing).
- [ ] `docker compose up` happy path documented in ≤10 lines.
- [x] First boot with a valid clone + empty DB auto-indexes + builds
      snapshots (`brain_bootstrap`) — no "empty brain" trap on day one.
- [x] `sync_brain --no-pull` for credential-less clones (local dev,
      host-owned pulls).

## Cross-platform + docs (5.4)

- [ ] bash/Makefile twin of `dev.ps1`.
- [ ] README: what/why, rings hero GIF (rings shipped in M3.5.2 — the
      dashboard centrepiece is the shot to capture), quickstart.
- [ ] INSTALL (Coolify/compose), SECURITY (the §9 honest truths —
      "private notes are only as private as your VPS").
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

## Repo hygiene (5.5)

- [ ] History audit: confirm no secrets, no `db.sqlite3`, no real brain
      content in ANY commit. If anything personal ever landed → publish
      with fresh history from a chosen commit.
- [ ] LICENSE (MIT), CONTRIBUTING, issue templates.
- [ ] Vendored template code (mcp-api-starter-template) license header —
      Hasan's own code, confirm relicensing under MIT is clean.
- [x] Vendor the graph JS lib — no CDN anywhere. Cytoscape 3.34.0 (MIT)
      for the 3.5.3 force layout only; rings (3.5.2) and timeline
      (3.5.5) are plain SVG, since a polar layout of ~50 circles and a
      bar chart don't justify a dependency someone self-hosting has to
      trust. `static/vendor/README.md` records version + license per lib
      and documents the refresh procedure.
