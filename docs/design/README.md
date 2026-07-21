# PixlStash Design

This folder is the canonical reference for what PixlStash looks like. It is the law for
the visual language: colors, type, spacing, radius, elevation, motion, icons, and the
brand. New features and any UI change follow it. If a screen does not match this folder,
the screen is wrong, not the folder.

Owner: **lead designer.** Changes to *values* (color, type, spacing, radius, shadow,
motion) go through the lead designer. Changes to *behaviour* (a flow, a state, what a
control does) are agreed with the **UI/UX expert** first, then made beautiful.

## What's here

| File | What it is |
|---|---|
| `visual-language.md` | The full spec, with the reason behind every rule. **Read this.** |
| `design-tokens.css` | The machine-readable tokens: spacing, radius, type ramp, elevation, motion. Copy-pasteable, the single source of truth for non-color scales. |
| `drift-audit-2026-06.md` | The measured gap between the spec and the codebase as of the v1.6.0+ slip, and the order to close it. |
| `assets/logo/` | Logo, horizontal watermark lockup, favicon. The brand kit. |
| `assets/fonts/` | Tiny5, the brand pixel face (wordmark and brand moments only). |

## Where the actual values live

- **Color** is in the Vuetify themes in `frontend/src/main.js` (`pixlStashLight` /
  `pixlStashDark`), consumed as `rgb(var(--v-theme-*))`. That file is the source of
  truth for color; `visual-language.md` §4 documents how to use it.
- **Everything else** (spacing, radius, type, elevation, motion) is in
  `design-tokens.css` here.

## The short version

- Warm neutrals, never cold grey, never pure black or white. One amber accent, spent
  sparingly. The photos are the color; the chrome stays quiet.
- System sans for all UI. **Tiny5 is brand-only** (wordmark, logo, splash). Never set
  UI or body text in the pixel font. PressStart2P is retired.
- 4px spacing grid. Four radii (`sm`/`md`/`lg`/`pill`). Four elevation levels, all on
  the theme shadow token. A real type ramp in rem, headings at 600.
- Reach for a token before you type a value. If it is not a token, you want the nearest
  one. A genuinely new value is a design decision, not an inline tweak.
- Design focus, hover, selected, empty, and loading states. That is where polish lives.

Start with `visual-language.md`.
