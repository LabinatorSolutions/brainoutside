# Launch plan — three repos, one story

Decided 2026-08-02. This doc covers what `OPEN-SOURCE.md` does not:
the public story, `brain-template` as its own product, and the
brainoutside.com website. The server-repo release checklist stays in
[OPEN-SOURCE.md](OPEN-SOURCE.md) — nothing there is duplicated here.

## The map

| Repo | Visibility | Role |
|---|---|---|
| `my-brain` | private, forever | Hasan's actual brain. Never published; the generalized version is `brain-template`, not a cleaned copy of this. |
| `brain-template` | public, template-flagged | The local-brain product. Split out from `brain-template/` in this repo. |
| `brainoutside` | public (this repo) | The server — the online head. Publishes with fresh history (decided 2026-08-02). |
| site repo | **private** | Django site serving brainoutside.com (landing + docs). The site is public; its source is not. |

---

## 1. The story (write once, reuse everywhere)

The narrative for the landing page, both READMEs (short form), and the
launch post (long form). Grounded in how the project actually happened.

**Prior art.** Karpathy's llm-wiki
([gist, April 2026](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f))
made the case that plain markdown plus a coding agent beats RAG for
personal knowledge: compile at ingestion time, not retrieval time, and
the artifact compounds instead of being rebuilt per query. We researched
that wave and built on it.

**The gaps that made this project:**

1. **A wiki holds knowledge, not you.** No identity, no voice, no
   beliefs — an agent can recall from it, but it cannot *write as you*.
   Our brain makes `identity/` a first-class citizen (core, voice,
   beliefs), and the note kinds — `take`, `story`, `lesson`, `fact` —
   are content-shaped, not just reference-shaped.
2. **No safe way to grow.** A self-maintaining wiki fills itself with
   unreviewed extractions, and a brain you don't trust is a brain you
   stop using. Our write path is gated: agents *propose*, a human
   approves, approval is one signed commit. Supersede, never delete.
3. **Creation, not just recall.** The point is to *make things* from
   the brain — replies, posts, scripts — in your own voice. Lenses
   (named retrieval scopes) and context packs turn the brain into a
   writing instrument, not a lookup table.
4. **One machine, one tool.** A local folder serves Claude Code on your
   laptop and nothing else. The online head — this server — lets every
   agent you run, anywhere, read your mind over MCP and REST:
   self-hosted, private, visibility tiers enforced server-side.

**The close:** the two paths compose. The local repo IS the repo the
server clones. Start local today; add the server when you want it;
nothing to migrate.

**Credits section** (landing page + template README): link the actual
inspirations, honestly. The llm-wiki gist is verified. The rest of the
research (the "other projects we relied on") was never written down in
either repo — **TODO(Hasan): name them**, so the list is real and not
retro-fitted. "Inspired by" phrasing only; no affiliation implied.

---

## 2. `brain-template` — the local brain as a product

Goal: someone who never runs the server gets full value. Clone it, open
VS Code, Claude Code is the interface; `mind-feeder` and `mind-reader`
work with zero infrastructure. This is Path A on the landing page and a
complete product on its own.

- [ ] Split `brain-template/` into its own public repo; mark it a
      **GitHub template repo** (the setup wizard deep-links to
      `/generate` — currently 404s, this is the unblock).
- [x] README rewritten local-first — *done 2026-08-02: quickstart
      ("Get the loop running", with the make-it-private warning),
      lineage/story, license/ownership, outgrow-local pointer. NOT
      done: the screenshot/GIF of a real local session — that is a
      launch asset, made once against the final flow (§5.5).*
- [x] `contract-version:` field in CLAUDE.md + the upgrade story —
      *done 2026-08-02: `contract-version: "1.0"` frontmatter + §9 in
      the template CLAUDE.md. Verified both server taxonomy parsers
      anchor to the §7 heading (position-independent), so top-of-file
      frontmatter cannot break them. The server-side warn check +
      `upgrade_brain` remain 5.4 work in OPEN-SOURCE.md.*
- [x] Example-content decision — *decided 2026-08-02: ships as
      recommended (one project card + one lens, zero knowledge notes).
      Both files were already DELETE-THIS/edit-or-delete marked — the
      card even demos the staleness rule deliberately. No change was
      needed.*
- [x] LICENSE (MIT) + README ownership line — *done 2026-08-02: "the
      license governs the template, not your mind."*
- [ ] Issue templates; Discussions on (questions will be contract
      questions, not bug reports).
- [ ] Repo meta: description, topics, social-preview image.
- [ ] Canonical-copy decision. Recommendation: the public repo becomes
      canonical; `brain-template/` in this repo becomes a test fixture
      (the app's startup contract check boots against it) with a
      "fixture synced with template repo" item on the release
      checklist. No CI cross-repo diffing — a checklist line is enough
      at this scale.
- [ ] Release gate: the server boots clean against a **fresh clone of
      the published repo** (re-run of the 5.2 verification, against the
      real thing).

## 3. `brainoutside` — this repo

The release checklist is [OPEN-SOURCE.md](OPEN-SOURCE.md) and stays
there. This planning round adds only:

- [ ] README gains the short-form story (§1) and links: site, template
      repo, and the two-paths framing up top.
- [x] Publish mode — decided 2026-08-02: **fresh history**, even though
      the audit (5.5) came back clean. It closes the commit-email
      exposure categorically and matches how `brain-template` launches.
      At release: current tree → one initial commit → public repo
      becomes the working repo; this one is archived as private
      pre-history. GitHub noreply commit email set in the new repo
      before the first public commit.

## 4. The website — private repo, public site

**Stack (decided 2026-08-02):** a deliberately small Django project —
a handful of template views, no DB-driven content, no accounts — in a
**private** repo, deployed as one more Coolify resource on the VPS.
Vendors the app's design system directly: `tokens.css` + the component
layer from `assets/css/app.css`, built with the same standalone
Tailwind CLI pattern (committed `tw.css`, no Node). The marketing site,
the docs, and the product screenshots read as one thing.

Consequence of a private source repo: no community docs PRs. Docs
feedback routes to `brainoutside` issues — put that link in the site
footer so the path is explicit.

**Landing page structure:**

1. Hero — tagline ("Your brain, kept outside your head — where your
   agents can read it"), rings visual, two CTAs: *Start local* (template
   repo) / *Self-host* (server repo).
2. Substrate — "your brain is a git repo of markdown": readable,
   leavable, with history.
3. Two paths, side by side — A: VS Code + Claude Code, free, private,
   zero infra. B: self-hosted server, MCP + REST, tiers, approval
   queue, every agent you own.
4. **They compose** — same repo, two heads, nothing to migrate.
5. The story (§1) with the honest credits list.
6. Feature grid — approval queue, enforced tiers, MCP + REST, token
   ledger, the visuals, and the subscription-token path (**no API
   billing**) as a headline item.
7. The honest security note — "private notes are only as private as
   your VPS." On-brand trust move, not fine print.
8. Footer — GitHub (both repos), docs, feedback→issues link.

**Docs section:** the 8-page outline in OPEN-SOURCE.md §5.4 (getting
started, concepts, feeding, reading, self-hosting, security & privacy,
FAQ, API reference). API reference stays auto-generated at `/docs/` on
the reader's own instance; the site links to it rather than mirroring.

**Pre-work:** confirm brainoutside.com is registered and DNS is
pointable at the VPS before building against the name.

## 5. Sequence

1. **History audit** — done 2026-08-02, clean; publish mode decided:
   fresh history.
2. **`brain-template` live** (unblocks the wizard deep link; smallest
   full product).
3. **Server release checklist** (OPEN-SOURCE.md 5.1–5.5 remainder).
4. **Site build + deploy** (landing + docs on brainoutside.com).
5. **Launch assets** — rings hero GIF (not the graph explorer until the
   lens click-through bug is fixed), both-theme screenshots against the
   shipped UI.
6. **Publish + launch post** (long-form §1 story) — then the M4 eval
   runs in public, build-in-public style.

## 6. Open decisions

- **GitHub account vs org.** Recommendation: publish under the personal
  account — distribution is Hasan's existing audience, and a later
  transfer to an org is safe (GitHub redirects old URLs). Revisit only
  if contributors multiply.
- **Credits list** — TODO(Hasan) in §1; blocks the landing page's story
  section and the template README, nothing else.
- Example content, canonical copy — recommendations inline in §2;
  treated as decided unless vetoed.
