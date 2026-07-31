# UI rewrite (M5.6) — design

The ops UI is the product. Everything a person will see, screenshot, or
judge BrainOutside by is one of ~13 pages behind the login. This is the
plan to make them good, and to make the styling *correct* — which it
currently is not, in a way that only shows up in production.

**Decided 2026-07-31 (Hasan):**
1. **Redesign the ops UI**, not just re-plumb it. New layout, navigation,
   typography and density across every page, on a real component layer.
2. **Tailwind v4.** CSS-first `@theme`, no JS config.
3. **This runs BEFORE M5.4/M5.5.** Launch assets, docs screenshots and the
   README hero get made once, against what actually ships.

Numbered 5.6 but sequenced before 5.4 — the same convention PLAN.md
already uses for "M4 runs after M5".

---

## Why this is not optional

Three problems that are really one problem.

**1. The enforced CSP deletes every inline style.**
`apps/core/security/headers.py` sets `style-src 'self' 'nonce-…'`. A nonce
authorises inline `<style>` *blocks*; it does nothing for `style="…"`
*attributes*, and per CSP3 a nonce makes `'unsafe-inline'` inert, so they
cannot be combined. Every ops page carries its design tokens as inline
style attributes, so on any deployment running `prod.py` they are all
dropped. Measured on the same page across both stacks: the "Test
connection" button computes `rgb(99,102,241)` under dev and
`rgba(0,0,0,0)` under prod, with ~105 blocked-inline-style console errors.

**It hid because `dev.py` sets `CSP_REPORT_ONLY=True`.** Every visual
check this project has ever done, including all of M3.5, ran on the
permissive stack. That is the real lesson, and §6 turns it into a
guardrail.

**2. The Tailwind build does not exist.**
`tailwind.config.js` is vendored from the starter template and nothing
reads it: no `package.json`, no `node_modules`, no `npm`/`npx` in
`dev.ps1` or the `Dockerfile`. `static/css/tw.css` is a committed,
minified ~60 KB artifact of ~700 rules, frozen on 2026-07-28. Any utility
added to a template since then silently does nothing — confirmed by spot
check: `max-w-4xl` present, `max-w-3xl` / `max-w-2xl` / `ps-5` /
`list-decimal` / `last:border-0` absent. Pages added during M5.3 are
already affected.

**3. So inline styles were the only bridge.** With a frozen utility set
and the theme living as CSS variables (`--ink`, `--line`, `--surface`),
`style="border-color: var(--line)"` was the only way to reach a token.
This is inherited constraint, not sloppiness — and it is why fixing the
CSP without fixing the build would just move the problem.

### The CSP band-aid is now unnecessary — do NOT apply it

Earlier in this work the recommendation was to add `style-src-attr
'unsafe-inline'` to unblock a release. **That recommendation is withdrawn
given the decision to launch with the new UI.** Nothing ships before this
rewrite, and the rewrite removes the last `style="…"` attribute, so the
CSP never needs loosening at all. Shipping a temporary weakening that
gets reverted three weeks later is strictly worse than shipping neither.

The end state is a CSP *stricter* than today's — because today's is only
strict by accident, in the sense that everything it governs is broken.

---

## Starting point, measured

455 inline `style="…"` attributes across 24 templates:

| Area | Templates | Inline styles |
|---|---|---|
| `ops/` | 13 | 378 |
| `setup/` | 7 | 66 |
| `partials/` | 3 | 11 |
| `docs/`, `errors/`, `components/`, `login`, `home`, `base` | 25 | **0** |

The vendored pages are already clean; the drift is entirely in the pages
written for this app. That bounds the work: 24 files, and the conversion
target already exists as a pattern to copy.

`static/css/tokens.css` is in good shape and survives: it already splits
**palette primitives** (`--c-indigo`) from a **semantic layer**
(`--accent`, `--line`, `--surface`), which is exactly the shape Tailwind
v4's `@theme` wants. The token architecture is right; it was simply never
wired to Tailwind.

---

## Architecture

### Build

Tailwind v4 via the **standalone CLI binary** — no Node, no
`node_modules`, no `package.json` in the repo.

**Docker does not build CSS.** `tw.css` stays a committed artifact, so
self-hosters and the GHCR image need no toolchain, and `collectstatic`
keeps working unchanged. The difference from today is only that it becomes
*regenerable*, and §6 adds a check that proves it still matches source.

    .\dev.ps1 css          # one-shot rebuild
    .\dev.ps1 css -Watch   # rebuild on template save

### Tokens

`tokens.css` keeps the primitive → semantic split and gains an `@theme`
block so the semantic layer becomes real utilities:

```css
@theme {
  --color-surface:   var(--c-paper);
  --color-surface-2: var(--c-cream);
  --color-ink:       var(--c-ink);
  --color-muted:     var(--c-muted);
  --color-line:      color-mix(in srgb, var(--c-ink) 12%, transparent);
  --color-accent:    var(--c-indigo);
  --color-danger:    var(--c-red);
  --font-display: 'Space Grotesk', system-ui, sans-serif;
  --radius-card: 14px;
}
```

`bg-surface`, `border-line`, `text-muted`, `bg-accent` become ordinary
utilities. Re-theming stays a one-file edit, and the toggle keeps working
by swapping variables.

**This is where v4 pays for itself:** on v3 the same thing requires
rewriting every token as a channel triple
(`rgb(var(--ink) / <alpha-value>)`) or opacity modifiers like
`bg-surface/50` silently fail. v4 handles var-backed colours natively.

The palette itself does not change. OPEN-SOURCE.md 5.1 already settled
that: the paper/ink/indigo theme stays as the default, only its *naming*
had to stop being personal. What changes is layout, hierarchy and
consistency — not hue.

### Components

The current pages repeat the same eleven-class button string a dozen
times, with drift each time. "Professional" mostly means *consistent*,
which copy-paste does not survive. So: raw utilities for layout, a
component layer for the repeated furniture.

`btn` / `btn-primary` / `btn-danger` / `btn-ghost`, `card` + `card-header`,
`table`, `badge-{ok,warn,danger,info}`, `field` + `field-label` +
`field-hint`, `stat-tile`, `pane-terminal` (exists already), `empty-state`.

A **living style guide at `/ops/styleguide/`** (staff-only, `DEBUG`-only)
renders every component in both themes. It is what makes reviewing the
system possible without clicking through 13 pages, and it is where dark
mode gets checked.

---

## Build order

Each step is committed on its own. DONE = the check passes.

### 5.6.0 — Foundation (no visual change)
- **0.1** Standalone Tailwind v4 CLI, `dev.ps1 css [-Watch]`, `tw.css`
  regenerated from source. Verify `@tailwindcss/forms` and `typography`
  have v4-compatible releases; if not, hand-roll the form styles — they
  are a small part of the surface.
  *Check: delete `tw.css`, rebuild, the app renders identically; a newly
  added utility class appears in the output.*
- **0.2** `tokens.css` gains `@theme`; semantic tokens become utilities.
  *Check: a scratch page using `bg-surface border-line text-muted` renders
  the same colours as the inline-style equivalents.*
- **0.3** **Flip `CSP_REPORT_ONLY` to False in `dev.py`.** Non-negotiable
  and it comes first: developing against report-only CSP is exactly what
  hid this for the whole project.
  *Check: the dev stack now shows the same broken styling prod does — the
  bug becomes visible instead of theoretical.*

### 5.6.1 — Component layer
- **1.1** The vocabulary above, as `@apply` components.
- **1.2** `/ops/styleguide/`.
  *Check: every component renders in light and dark under ENFORCED CSP;
  zero inline styles on the page.*

### 5.6.2 — Convert the 24 templates
In risk order, easiest first so the component vocabulary is exercised and
corrected before it hits the hard pages:
1. `setup/*` (66) — recently written, simple, and the wizard is the
   first thing a new user sees.
2. `ops/settings`, `ops/health`, `ops/tasks`, `ops/logs` (158) — forms,
   tables, status.
3. `ops/browser`, `ops/entity`, `ops/feeds`, `ops/feed_detail` (135) —
   data-heavy; `feed_detail` is the single worst file at 64.
4. `ops/chat`, `ops/chat_session` (24) — streaming SSE, so behaviour
   needs re-checking, not just looks.
5. `ops/dashboard`, `ops/graph`, `ops/timeline`, `ops/_rings` (61) —
   **last, and carefully**: JS reads and writes these nodes, and the M3.5
   visuals are the differentiator. A token rename here breaks silently.

*Check, per group: rendered in a real browser under ENFORCED CSP; zero
`style="` remaining in those files; and the behaviour checks from M3.5 and
M5.3 re-run green for the affected pages.*

### 5.6.3 — The actual redesign
- **3.1** Navigation: grouped sidebar, icons, and an **active state** —
  `nav.py` currently emits no active flag, so the sidebar never shows
  where you are.
- **3.2** Page furniture: consistent headers, breadcrumb-or-title,
  primary-action placement, and real density instead of card-soup.
- **3.3** Tables: the browser/logs/feeds pages are the ones people will
  live in. Sorting affordances, sane column widths, sticky headers.
- **3.4** Responsive: the ops UI is desktop-shaped today.
- **3.5** **Dark mode.** `darkMode: "class"` is configured and a
  theme-toggle partial exists, but `tokens.css` has no dark block, so the
  toggle has nothing to toggle. Decide: ship it (cheap now that tokens are
  variables, and close to table stakes for a dev tool) or delete the
  toggle rather than ship a dead control.
  *Check: three viewports × two themes, in a real browser.*

### 5.6.4 — Launch assets
Rings hero for the README, screenshot set, and the demo GIF — but **not**
the graph explorer until the lens/click-through bug in OPEN-SOURCE.md is
fixed.

Then M5.4 (GHCR, INSTALL, SECURITY, bash/Makefile twin) and M5.5
(LICENSE, CONTRIBUTING, history audit) run against the final UI.

---

## Guardrails, so this cannot come back

The failure here was not the inline styles; it was that **nothing could
tell us they were broken.** Three cheap, permanent checks:

1. **CSP enforced in dev** (5.6.0.3). The single highest-value change in
   this document.
2. **A test that fails on `style="` in any template.** Turned on at the
   end of 5.6.2. One grep, permanent.
3. **A test that rebuilds `tw.css` and fails if it differs from the
   committed artifact.** Proves it stays regenerable and that nobody
   hand-edits the build output.

---

## Risks

- **Re-verification cost.** M5.3 was verified page-by-page in a real
  browser; converting the templates invalidates that. Mitigated by making
  the per-group check re-run those flows rather than only eyeballing.
- **The M3.5 visuals are JS-coupled.** `brain-viz.js`, `rings.js`,
  `activity.js` and the explorer read classes and CSS variables. Converted
  last, with their own checks.
- **Tailwind v4 plugin compatibility** is the one unknown in 5.6.0.1, and
  it is the first thing checked, so it fails fast and cheap.
- **Scope creep into a full design system.** The target is a coherent
  internal tool, not a component library. The style guide is a review aid;
  it is not a deliverable to polish for its own sake.

## Visual direction — decided 2026-07-31 (Hasan)

**Keep the look, fix the craft.** The paper/ink/indigo palette and the
Space Grotesk / Inter / JetBrains Mono type stack stay. Every hour goes
into layout, hierarchy, density and consistency instead of hue.

That is a real constraint, not a shrug: it means a change is only worth
making if it improves how fast the page can be *read*. Reviewers of 5.6.3
should be able to point at what a change made clearer.

**Dark mode ships.** So `tokens.css` grows a dark block, every component
is checked in both themes on the style guide, and the existing theme
toggle stops being a dead control. Practically this means one extra rule
during the conversion: components style against semantic tokens only
(`bg-surface`, `text-muted`), never against a palette primitive or a
literal hex — a hardcoded `#0d1117` is invisible in light mode review and
wrong in dark. The terminal/log panes are the deliberate exception; they
are dark in both themes by design.
