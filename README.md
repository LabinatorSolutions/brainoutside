# BrainOutside

**Your brain, kept outside your head — where your agents can read it.**

A self-hosted memory server for your AI agents. One git repo full of
markdown is your brain; BrainOutside serves it over REST and MCP, with
visibility tiers enforced server-side and a human gate on every write.

Single-user and single-brain by design. That is the product, not a
limitation waiting to be fixed.

> Status: pre-release. The engine is built and running; the setup
> experience described in [`docs/SETUP-DESIGN.md`](docs/SETUP-DESIGN.md)
> is being implemented before the public beta.

## Why not just use a vector database

Because you cannot read one. Your brain here is plain markdown in a
normal git repo:

- **You can read it.** Open it in any editor. `git log` it. Fix a note by
  editing a file.
- **You can leave.** It is your repo. Delete the server and the brain is
  still there, intact and useful.
- **It has history.** Change your mind and the old note is *superseded*,
  not deleted — so your brain records how your thinking moved, not just
  where it landed.

## How it works

**One repo is the brain.** Atomic notes with a strict frontmatter
contract: opinionated `take`s, `story`s with real numbers, `lesson`s,
citable `fact`s. Every note carries provenance back to its source.

**Agents read it through a lens.** A lens is a named retrieval scope —
topics, note types, and a visibility ceiling. Ask for a context pack and
you get the right 3–7 files, not the whole repo.

**Nothing enters without you.** Feed a source — a video, a post, a
transcript, a raw thought — and an agent *proposes* notes. You approve in
a UI. Approval is one signed git commit. A brain that fills itself with
unreviewed extractions is a brain you stop trusting.

**Tiers are enforced, not decorative.** Every note resolves to `public`,
`agents-only` or `private`, and each API key sees only its tier — because
the reader agent runs against a materialized snapshot of that tier and
physically cannot read above it.

## What you get

- **REST + MCP** — point Claude Code, or any MCP client, at your own mind
- **A gated write path** — proposals land in an approval queue; approval
  commits and pushes
- **A chat test bench** — talk to your brain at any tier, with the sources
  it used shown per message
- **Visuals** — visibility rings, a topic graph, live read activity, and a
  timeline of every position you have revised
- **A full ledger** — every token, every read, every SDK run

## Requirements

Somewhere to run Docker (a VPS with Coolify, or `docker compose` on any
box), a GitHub account, and a Claude credential — an Anthropic API key
**or** a Claude subscription token (`sk-ant-oat`), so you can run this
without API billing.

## Getting started

Not yet — the first-run wizard lands with the beta. Until then the manual
path is [`docs/DEPLOY.md`](docs/DEPLOY.md).

## Your brain repo

Start from [`brain-template/`](brain-template/): the contract, both agent
skills, note templates and placeholder identity files. It ships with zero
notes on purpose — an empty brain that is truly yours beats a seeded one
you have to clean out.

## Docs

| | |
|---|---|
| [`docs/PLAN.md`](docs/PLAN.md) | Full architecture, data model, security posture, milestones |
| [`docs/SETUP-DESIGN.md`](docs/SETUP-DESIGN.md) | How setup and deployment are being rebuilt for the beta |
| [`docs/OPEN-SOURCE.md`](docs/OPEN-SOURCE.md) | Running release checklist |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Current (manual) Coolify runbook |

## Running it locally

```powershell
.\dev.ps1
```

Builds on first run and starts web + mcp + worker + postgres + redis,
waits for the healthcheck, prints the URLs. Same containers as the
deploy; only `docker-compose.local.yml` differs. A bash/Makefile twin is
coming with the beta.

| Command | |
|---|---|
| `.\dev.ps1` | build if needed, start everything, wait for health |
| `.\dev.ps1 reload [svc]` | restart app containers — picks up code, no rebuild |
| `.\dev.ps1 rebuild [-NoCache]` | rebuild images and recreate containers |
| `.\dev.ps1 down [-Volumes]` | stop and remove (`-Volumes` also drops the DB) |
| `.\dev.ps1 logs [svc]` / `ps` / `status` | follow logs / container state / `+ /readyz` |
| `.\dev.ps1 shell [svc]` / `manage <args>` / `superuser` | bash in / `manage.py` in / create a login |

Source edits are live — the repo is bind-mounted, so `web` reloads
itself. `mcp` and `worker` need `.\dev.ps1 reload`. Only a
`requirements.txt` or `Dockerfile` change needs `rebuild`.

App on <http://localhost:8000>, Postgres on `localhost:5433`, Redis on
`localhost:6380` (offset so a host install keeps its default port).
