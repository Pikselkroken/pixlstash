# Visual drift audit — June 2026

Why this exists: the sleekness slipped after v1.6.0. This is the evidence, measured
not eyeballed, plus the order to fix it in. Numbers are from a scan of
`frontend/src/components` + `App.css` + `style.css`.

The token *system* in `frontend/src/main.js` is good. The problem is that components
reach past it and hardcode values. Drift is arithmetic: count the distinct values that
should be a small fixed set, and the gap is the bug list.

## What the scan found

| Dimension | Distinct values in code | Should be | Worst offenders |
|---|---|---|---|
| **Hardcoded hex colors** | **38** | 0 (use `--v-theme-*`) | 5 greys (`#9aa0a6`, `#cdd0d6`, `#80868b`, `#888`, `#ccc`), 8+ reds (`#e53935`, `#c62828`, `#c5362d`, `#b3261e`, `#ff5252`, `#e81123`…), 4 greens (`#1e7d44`, `#258a4d`, `#81c995`, `#9ad6ab`), 4 ambers (`#e57c00`, `#c96000`, `#9a6b1f`, `#b07d27`) |
| **Border-radius** | **14** (2/3/4/5/6/8/9/10/12/14/16/18/999/9999) | 4 (`sm`/`md`/`lg`/`pill`) | 3px (×18) and 5px (×14) fighting 4px; 9/10px fighting 8px; a `9999px` typo |
| **Font-size** | **30+**, mixing px / rem / **em** | the `--text-*` ramp, rem only | `em` units that compound (`0.9em`, `0.85em`, `0.8em`, `0.92em`…) drifting nested labels |
| **Box-shadow** | **58** | 4 (`--elevation-1..4`) | most hardcode `rgba(0,0,0,…)` instead of `--v-theme-shadow`, reads cold on the warm canvas |
| **Spacing (px)** | on-grid 4/8 dominate, but a tail of | 4px grid (`--space-*`) | off-grid 5, 7, 10, 11, 14, 18, 26, 30, 36, 78px |
| **Stale font** | PressStart2P `.ttf` in `frontend/dist/` | gone | retired in favour of Tiny5; lingers only as a build artifact |

## Contrast findings

- `primary` olive `#5c7c0a` + white = **4.83:1** — passes 4.5:1. Good as the
  primary-action button color.
- `accent` amber `#b0732b` + white = **3.94:1** — fails the 4.5:1 body floor, passes
  the 3:1 large/UI floor. Amber is for large labels, icons, borders, and washes, not a
  background under small white text. See `visual-language.md` §4.

## Fix order (lowest risk first)

This is migration, not a rewrite. Each step is mechanical and independently shippable.
Anything that visibly moves pixels (radius snapping, type resizing) gets UI/UX sign-off
before merge, per the security/authz-style discipline the repo already runs on UI too.

1. **Purge the stale PressStart2P artifact** and grep the source for any live
   reference. Zero behaviour change.
2. **Adopt `design-tokens.css`** (import it once, globally). Defines the vocabulary;
   restyles nothing on its own.
3. **Color first, highest payoff.** Replace the 38 hardcoded hex values with
   `rgb(var(--v-theme-*))`. Collapse the ad-hoc greens/reds/ambers onto
   `success`/`error`/`warning`/`accent`. This is where "looks like PixlStash" lives.
4. **Shadows.** Swap the 58 hardcoded shadows for `--elevation-1..4`. Mechanical.
5. **Radius.** Snap 14 radii to the 4-value set. Touches rendering — UI/UX glance.
6. **Spacing.** Snap off-grid px to `--space-*`. Touches rendering — UI/UX glance.
7. **Type.** Hardest, save for last: move `em`/`px` sizes onto the rem `--text-*` ramp.
   Do it per component family so nested `em` compounding is unwound deliberately.

## How to keep it from coming back

The lasting fix is the same shape as the repo's authz direction: stop relying on a
human remembering. Candidates, in order of effort:

- A lint rule (stylelint) that flags hex literals, `em` font-sizes, and off-scale
  radius/spacing in component styles, so new drift fails CI instead of shipping.
- A small set of utility classes / shared component styles for the common patterns
  (the `.section-label` precedent) so the easy path is the correct path.
