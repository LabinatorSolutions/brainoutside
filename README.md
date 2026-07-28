# my-brain-web-app

The online head for Hasan's central mind (the `my-brain` repo).

A Django app (forked from `solo-mcp-api-starter`) that owns a server-side
clone of the brain repo and exposes it as:

- **REST + MCP** — dumb layer (index, notes, lenses, raw) + smart layer
  (`assemble_context`, powered by the Claude Agent SDK)
- **A gated write path** — feed proposals from UI/API/MCP land in an approval
  queue; approval = one `feed: <source-id>` commit, pushed to GitHub
- **An ops UI** — dashboard, brain browser, feed queue, chat test bench,
  structured event + token logs, settings (learnwithhasan theme)

**The git repo stays the single source of truth.** The database here is a
rebuildable index; if they ever disagree, the repo wins and a drift event is
logged.

See [`docs/PLAN.md`](docs/PLAN.md) for the full phase-2 plan, data model,
and milestones.
