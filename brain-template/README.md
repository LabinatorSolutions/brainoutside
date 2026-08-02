# Your mind

This repo is a **brain**: one structured, git-versioned knowledge base that
your AI agents read from for context, facts, and voice. Plain markdown, one
idea per file, with a contract strict enough that agents can be trusted to
retrieve from it — and to propose additions to it.

It is designed to be served by [BrainOutside](https://brainoutside.com),
which clones this repo and exposes it over REST + MCP with visibility
tiers and a human-gated write path. It also works perfectly well on its
own, read directly by Claude Code.

**`CLAUDE.md` is the contract.** Read it before anything else. Two skills in
`.claude/skills/` are the only interfaces: `mind-feeder` writes (gated),
`mind-reader` reads.

## Make it yours (about 20 minutes)

1. **Write your identity core.** `identity/core.md`, `identity/beliefs.md`
   and `identity/voice.md` ship as placeholders with instructions inside.
   These get loaded on nearly every retrieval, so they matter more than any
   single note. Keep them short and specific — "I explain things by building
   the smallest working version first" beats "I am passionate about tech".
2. **Edit the taxonomy** in `CLAUDE.md` §7 to your actual topics. The list
   there is an example. Short is better: ~15–25 tags.
3. **Add one project card** per thing you build, from
   `projects/_TEMPLATE.md`. Delete `projects/example-project.md`.
4. **Adjust the example lens** in `lenses/`, or delete it and write your own
   once you know what your agents keep asking for.
5. **Feed your first source.** A video, a blog post, a thread, or just a raw
   thought. The feeder proposes notes; you approve; it commits.

Optional: the skills and contract say "the owner" throughout. Search-replace
that with your name if you'd rather the agents read it that way — it makes
voice-grounded output slightly more consistent.

## What goes where

| Folder | What belongs |
|---|---|
| `identity/` | Who you are, what you believe, how you write. 3 small files. |
| `projects/` | One card per project: pitch, status, numbers, architecture, pointers. |
| `knowledge/takes/` | Opinionated positions — "my angle on X". |
| `knowledge/stories/` | Personal narratives with numbers, failures, outcomes. |
| `knowledge/lessons/` | Transferable "what I learned building/testing X". |
| `knowledge/facts/` | Stable, citable facts about your work and results. |
| `content-catalog/` | Inventory of what you've already published, per platform. |
| `lenses/` | Named retrieval scopes: topics + note types + a visibility ceiling. |
| `raw/` | Full transcripts and source text. Linked from notes, never browsed. |
| `eval/` | The falsification test — does the mind actually improve output? |

## The two rules that make it work

**Supersede, never delete.** When you change your mind, the old note stays
and gets marked `superseded`. Your brain becomes a record of how your
thinking moved, not just where it landed.

**Nothing enters without your approval.** Agents propose; you decide. A
brain that fills itself with unreviewed extractions is a brain you stop
trusting, and an untrusted brain is worse than no brain.

## Start empty on purpose

There are no example notes in `knowledge/` — an empty brain that is truly
yours beats a seeded one you have to clean out. The `_TEMPLATE.md` files
show the shape; the app ignores them when indexing.

Run the eval in `eval/` once you have ~10 notes. If retrieval doesn't
visibly improve your agents' output, fix the notes before adding more.

## Lineage

This design descends from [Andrej Karpathy's llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
idea — plain markdown that a coding agent compiles knowledge into, so
the artifact compounds instead of being re-retrieved per query. The
template adds what a wiki doesn't have: identity and voice as
first-class files (so agents can write *as* you, not just recall),
content-shaped note kinds, and a hard human gate on every write. And
when you want this brain readable by every agent you run — not just
the one on this machine — [BrainOutside](https://brainoutside.com) is
its online head.
