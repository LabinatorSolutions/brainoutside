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

## Run the full stack locally (Docker)

```powershell
.\dev.ps1
```

Builds the image on first run and starts web + mcp + worker + postgres +
redis, waits for the healthcheck, and prints the URLs. Same containers as
the deploy — only `docker-compose.local.yml` differs (DEBUG settings,
bind-mounted source, bind-mounted `data/brain-repo`, fixed host ports).

| Command | |
|---|---|
| `.\dev.ps1` | build if needed, start everything, wait for health |
| `.\dev.ps1 reload [svc]` | restart app containers — picks up code, no rebuild |
| `.\dev.ps1 rebuild [-NoCache]` | rebuild images and recreate containers |
| `.\dev.ps1 down [-Volumes]` | stop and remove (`-Volumes` also drops the DB) |
| `.\dev.ps1 logs [svc]` / `ps` / `status` | follow logs / container state / `+ /readyz` |
| `.\dev.ps1 shell [svc]` / `manage <args>` / `superuser` | bash in / `manage.py` in / create a login |

Source edits are live — the repo is bind-mounted, so `web` reloads itself
(runserver). `mcp` and `worker` don't self-reload: `.\dev.ps1 reload`.
Only a `requirements.txt` or `Dockerfile` change needs `rebuild`.

App on <http://localhost:8000>, Postgres on `localhost:5433`, Redis on
`localhost:6380` (offset so a host install keeps its default port).

Deploying to the VPS is a different path — see [`docs/DEPLOY.md`](docs/DEPLOY.md).

See [`docs/PLAN.md`](docs/PLAN.md) for the full phase-2 plan, data model,
and milestones.
