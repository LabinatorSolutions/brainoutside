# Deploying to Coolify (M1.11 runbook)

The repo ships everything the server needs (`Dockerfile`,
`docker-compose.yml`, `docker/entrypoint.sh`). The steps below are the
operator half — they need VPS/Coolify/GitHub access and can't be done
from the repo.

## 1. GitHub credentials (deferred from M0.5)

1. **Read-only deploy key** — `ssh-keygen -t ed25519 -f brain_deploy_key -N ""`,
   add the public half to `hassancs91/my-brain` → Settings → Deploy keys
   (WITHOUT write access). Private half becomes the
   `brain_deploy_key` secret file mounted at `/run/secrets/brain_deploy_key`.
   When using the SSH key, set `BRAIN_REPO_URL=git@github.com:hassancs91/my-brain.git`.
2. **Fine-grained write PAT** — contents:read+write on `my-brain` only.
   NOT used until M2; store it in the password manager, not in Coolify yet
   (split credentials — write cred exists only in the worker, grill C13).
3. Verify: clone with the key works; `git push` with the key is refused.
4. Enable commit email notifications on the repo so an out-of-band commit
   is noticed (PLAN.md §4).

## 2. Coolify resource

- New resource → Docker Compose → this repo, `docker-compose.yml`.
- Required env (Coolify → Environment Variables — remember: Coolify
  injects unset compose `${VAR}` as EMPTY STRING; the app treats "" as
  unset by design):

| Var | Value |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `FIELD_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `POSTGRES_PASSWORD` | random |
| `ALLOWED_HOSTS` | `brain.example.com` |
| `OAUTH_ISSUER` | `https://brain.example.com` |
| `DJANGO_ADMIN_URL_PATH` | random slug, e.g. `x7-admin/` |
| `ADMIN_PANEL_URL_PATH` | `ops/` |
| `ADMIN_IP_ALLOWLIST` | Tailscale range, e.g. `100.64.0.0/10` |
| `BRAIN_REPO_URL` | `git@github.com:hassancs91/my-brain.git` |
| `GITHUB_WEBHOOK_SECRET` | random; same value in the GitHub webhook |
| `MCP_LOOPBACK_SECRET` | random (cross-container MCP auth) |
| `SECURE_SSL_REDIRECT_ENABLED` | `1` (Coolify terminates TLS) |

- `ANTHROPIC_API_KEY` is NOT env — paste it into the ops Settings page
  after first boot (stored Fernet-encrypted). Use a dedicated
  workspace-scoped key with a console spend limit (§9).
- First boot: web runs migrate + collectstatic; the brain bootstrap
  clones the repo into the `brain-repo` volume and fails loudly if
  CLAUDE.md / `.claude/skills/` / `lenses/` are missing.
- Create the superuser once:
  `docker exec -it <web> python manage.py createsuperuser`.

## 3. Network boundary for the ops UI (§9.4)

Public internet may reach ONLY `/api/`, `/mcp`, `/webhooks/github`,
`/docs/`, `/healthz|/readyz`. The ops UI (`/ops/…`, `/x7-admin/…`) must not
be reachable from the open internet:

- Preferred: Tailscale on the VPS; set `ADMIN_IP_ALLOWLIST` to the
  tailnet range (middleware returns 404 to everyone else), or
- Cloudflare Access rule covering `/ops/*` and the admin slug.

## 4. Host baseline

- `ufw`/nftables: inbound 22 (key-only, fail2ban) + 80/443 only.
- **Egress firewall for the app containers** (C12a): allow
  `api.anthropic.com:443`, `github.com:443/22`, DNS; default-deny the
  rest. In Coolify, attach the compose network to an nftables egress
  chain or use Docker's `--iptables` DOCKER-USER hook.
- Coolify itself stays on its own admin port behind Tailscale.

## 5. Backups (C17)

Nightly cron on the host:

```sh
docker exec <postgres> pg_dump -U brain brain | gzip \
  | age -r <backup-pubkey> > /backups/brain-$(date +%F).sql.gz.age
rclone move /backups b2:brain-backups --min-age 1h
```

- Rebuildable from repo: Entity, SyncRun, snapshots. NOT rebuildable:
  Feeds, Events, SdkOperations, Chat — that's what the dump protects.
- **Restore drill** (required by the M1.11 check): restore latest dump
  into a scratch Postgres, point a scratch compose at it, `/readyz` green.

## 6. GitHub webhook

Repo → Webhooks → `https://brain.example.com/webhooks/github`,
content type JSON, secret = `GITHUB_WEBHOOK_SECRET`, push events only.
The 15-min beat pull is the fallback (worker schedule).

## 7. Post-deploy checks (M1.11 DONE =)

- [ ] `https://brain.example.com/readyz` → 200, clone head matches origin
- [ ] REST + MCP answer with a real consumer key; 401 without
- [ ] `/ops/settings/` unreachable from open internet; reachable via
      Tailscale; Test connection → model/latency/tokens
- [ ] Push to `my-brain` → server reindexes in seconds (webhook path)
- [ ] Restore-from-backup drill passes
