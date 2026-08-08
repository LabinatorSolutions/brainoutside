# Launch plan — three repos, one story

Decided 2026-08-02. This doc covers what `OPEN-SOURCE.md` does not:
the public story, `brainoutside-template` as its own product, and the
brainoutside.com website. The server-repo release checklist stays in
[OPEN-SOURCE.md](OPEN-SOURCE.md) — nothing there is duplicated here.

## The map

| Repo | Visibility | Role |
|---|---|---|
| `my-brain` | private, forever | Hasan's actual brain. Never published; the generalized version is `brainoutside-template`, not a cleaned copy of this. |
| `brainoutside-template` | public, template-flagged | The local-brain product. Split out from `brain-template/` in this repo. |
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
   approves, approval is one commit authored by the server — so its
   writes are always distinguishable from yours. Supersede, never delete.
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

**Credits section** (landing page + template README) — resolved
2026-08-02: the research survey (many projects, open source and
commercial, explored with Claude at the time) was never written down
and the names are not recoverable, so there will be no retro-fitted
list. The credit is told exactly as it happened: Karpathy's llm-wiki
named and linked (verified), plus "we surveyed the landscape of memory
and knowledge-base tools and built where they stopped", plus the
standing README invitation — recognize your project in this lineage,
open an issue, get a link. "Inspired by" phrasing only; no affiliation
implied.

---

## 2. `brainoutside-template` — the local brain as a product

Goal: someone who never runs the server gets full value. Clone it, open
VS Code, Claude Code is the interface; `mind-feeder` and `mind-reader`
work with zero infrastructure. This is Path A on the landing page and a
complete product on its own.

- [x] Split `brain-template/` into its own public repo; mark it a
      **GitHub template repo** — *published 2026-08-02 as
      `hassancs91/brainoutside-template` (one commit, noreply author)
      and verified: remote HEAD == the locally-built commit, public,
      `is_template: true` via API, description set, published tarball
      byte-identical to the local repo, every server CONTRACT_PATH
      present. NOT verified: the `/generate` click-through — GitHub's
      web tier was 503ing on all HTML pages at check time (data plane
      fine); `is_template: true` is the condition it depends on, so
      re-check the link when GitHub recovers. Still to do on GitHub:
      enable Discussions, add topics.*
- [x] README rewritten local-first — *done 2026-08-02: quickstart
      ("Get the loop running", with the make-it-private warning),
      lineage/story, license/ownership, outgrow-local pointer. NOT
      done: the screenshot/GIF of a real local session — that is a
      launch asset, made once against the final flow (§5.5).*
- [x] `contract-version:` field in CLAUDE.md + the upgrade story —
      *done 2026-08-02: `contract-version: "1.0"` frontmatter + §10 in
      the template CLAUDE.md (§9 until the blocked-scripts section
      renumbered it). Verified both server taxonomy parsers anchor to
      the §7 heading (position-independent), so top-of-file frontmatter
      cannot break them. **Server-side warn check done 2026-08-08** —
      `CONTRACT_VERSION`, `contract_version_probe()`, a warn-only health
      row; warn-only in three places rather than by convention, since
      readyz gates on `contract_ok`. Stayed at "1.0": blocked-scripts is
      additive and defaults to off, so no existing brain is served
      differently. `upgrade_brain` remains 5.4 work — §10 hedges it
      ("may propose"), so it is not a broken promise.*
- [x] Example-content decision — *decided 2026-08-02: ships as
      recommended (one project card + one lens, zero knowledge notes).
      Both files were already DELETE-THIS/edit-or-delete marked — the
      card even demos the staleness rule deliberately. No change was
      needed.*
- [x] LICENSE (MIT) + README ownership line — *done 2026-08-02: "the
      license governs the template, not your mind."*
- [x] Issue templates; Discussions on (questions will be contract
      questions, not bug reports) — *done 2026-08-08: Discussions
      enabled via API (page 200s); `.github/ISSUE_TEMPLATE/` (config.yml
      routing + a contract-problem form) pushed as `ee27de6`, noreply
      author, mirrored into the in-tree fixture. Kept to two small
      files deliberately — template generation copies `.github/` into
      every generated brain, where they read as pointers back to the
      product.*
- [x] Repo meta: description, topics, social-preview image — *2026-08-08:
      description was already set; topics extended to
      ai-brain, brain-mcp, mcp, second-brain, claude-code,
      knowledge-base, markdown, pkm. NOT done: the social-preview
      image — GitHub has no API for it (manual upload in repo
      settings), and the image itself is a §5.5 launch asset.*
- [x] Canonical-copy decision — *decided 2026-08-08: the public repo is
      canonical, `brain-template/` is a fixture, sync is a checklist line
      (below), no cross-repo CI. Contract changes are authored in
      `brainoutside-template` and copied down here. The copies had
      already drifted — §8 blocked-scripts existed only in-tree — so the
      first act of the decision was bringing them to parity; verified
      byte-identical by `diff -r`. `test_brain_template_is_a_fixture.py`
      pins the fixture against CONTRACT_PATHS and forbids engine code
      from reaching for it. NOT covered: fixture-vs-published drift,
      which needs network from the suite; the checklist line is the
      accepted answer.*
- [ ] **Before each release: `diff -r --strip-trailing-cr
      brain-template/ <a fresh clone of brainoutside-template>` is
      empty.** The fixture is only evidence about the published
      template while they match, and nothing enforces it.
      (`--strip-trailing-cr` because a Windows checkout with autocrlf
      holds CRLF working copies of byte-identical blobs — the bare
      `diff -r` this line used to prescribe can never come back empty
      there. Last run clean 2026-08-08, after the issue-routing push.)
- [ ] Release gate: the server boots clean against a **fresh clone of
      the published repo** (re-run of the 5.2 verification, against the
      real thing). *(Published content is byte-identical to the tree
      5.2 boot-verified, but contract-version/LICENSE/README landed
      after that boot — fold this into the pre-launch wizard walk on a
      fresh stack.)*

## 3. `brainoutside` — this repo

The release checklist is [OPEN-SOURCE.md](OPEN-SOURCE.md) and stays
there. This planning round adds only:

- [x] README gains the short-form story (§1) and links: site, template
      repo, and the two-paths framing up top — *done 2026-08-08, plus
      an owner-requested lineage credit to the MCP API boilerplate the
      server grew from (the honest explanation for any unused feature
      someone finds). The brainoutside.com link goes live with §4.*
- [x] Publish mode — decided 2026-08-02: **fresh history**, even though
      the audit (5.5) came back clean. It closes the commit-email
      exposure categorically and matches how `brainoutside-template` launches.
      At release: current tree → one initial commit → public repo
      becomes the working repo; this one is archived as private
      pre-history. GitHub noreply commit email set in the new repo
      before the first public commit.

## 4. The website — private repo, public site

**Status 2026-08-02: scaffolded, built, verified** at
`D:\repos\brainoutside-site` (one commit, noreply author, 40 files).
Landing complete — all 8 sections with real copy; docs shell live —
index + 7 outline stubs + the API-ref-lives-on-your-instance card. CSP
enforced with a per-request nonce, dev included. `tw.css` committed
(60KB). Verified in a real browser: every route 200, dark mode toggles
and persists across pages, zero console errors in both themes. One
site-only CSS addition, documented in the file: the typography
plugin's `.prose` colours are mapped onto the semantic tokens in an
**unlayered** block — the plugin emits into `@layer utilities`, which
no layered override can beat (layer order trumps specificity).
Remaining: write the 7 docs pages, an OG image, Hasan pushes to a
private GitHub repo, Coolify deploy + DNS. Note: `--font-display` is
Space Grotesk and still not self-hosted — the product's open font item
now covers the site too; everyone else sees system-ui until it's fixed.

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
2. **`brainoutside-template` live** (unblocks the wizard deep link; smallest
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
- ~~Credits list~~ — resolved 2026-08-02 (see §1): honest survey
  phrasing + the open-issue invitation; the original name list is not
  recoverable and won't be faked.
- Example content, canonical copy — recommendations inline in §2;
  treated as decided unless vetoed.
