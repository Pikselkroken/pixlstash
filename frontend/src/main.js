// Vuetify styles
import "vuetify/styles";
import "@mdi/font/css/materialdesignicons.css";
import "./styles/design-tokens.css";
import "./style.css";
import "./styles/context-menu.css";

import { createApp } from "vue";
import { createPinia } from "pinia";
import { createVuetify } from "vuetify";
import { readRememberedTheme } from "./utils/themeMemory";
import * as components from "vuetify/components";
import * as directives from "vuetify/directives";
import router from "./router/index.js";
import { markEnd, markStart } from "./utils/perfMarks.js";

import Root from "./Root.vue";

// Startup timing: see docs/frontend_architecture.md §3 for the boot sequence
// this brackets (main.js -> Root.vue's auth gate -> App.vue's mount).
markStart("pixlstash:boot");

// Tag the document when running inside the Electron desktop shell so CSS can
// apply native-app chrome (thin scrollbars, no text-selection on chrome)
// without changing the experience for plain browser visitors.
if (typeof window !== "undefined" && window.pixlstashDesktop) {
  document.documentElement.classList.add("is-desktop");
}

// Custom theme properties
//
// KEY NAMING IS LOAD-BEARING. Vuetify emits one CSS variable per key, verbatim:
// `--v-theme-<key>` (see `vuetify/lib/composables/theme.mjs`, `genCssVariables`).
// It does NOT kebab-case, so a camelCase `onSurface` key emits the never-consumed
// `--v-theme-onSurface`, and Vuetify then AUTO-DERIVES the `--v-theme-on-surface`
// the app actually reads - as pure `#000` / `#fff` from `getForeground()`. Every
// foreground pair below is therefore written in kebab-case (`on-surface`, not
// `onSurface`), which is the only spelling Vuetify treats as "already authored"
// and skips deriving. Do not "tidy" these back to camelCase: it silently reverts
// the whole warm neutral ramp to pure black/white and re-breaks the status
// foregrounds. See docs/design/notice-surface.md §3.3.
//
// Warm light theme. Elevation inverts vs dark: the content canvas is the
// brightest surface and chrome (sidebar / toolbar / panels) recedes to a warm
// tinted grey, with raised controls (cards, inputs) going pure white. Text is a
// warm near-black ramp, never pure #000. Status hues are deepened so they hold
// contrast on the light canvas - all four of them: `error` #cf3b30 (4.62:1 on
// the canvas), `warning` #b8861f (3.09:1), `success` #2e7d32 (4.87:1) and `info`
// #1a6ec4 (4.90:1). `success` and `info` were Material 500s until 2026-07 and
// measured 2.64:1 / 2.97:1, i.e. below the 3:1 UI floor, which made this comment
// false for half the set.
const pixlStashLight = {
  dark: false,
  colors: {
    // Chrome: sidebar / toolbar / panels - warm tinted grey, recedes behind the
    // canvas. In the desktop shell these are remapped to `background` (see
    // style.css) so the titlebar + toolbar + sidebar read as one strip; these
    // values drive the browser layout.
    sidebar: "#f0ede9",
    "sidebar-text": "#25231e",
    toolbar: "#f0ede9",
    "toolbar-text": "#25231e",
    // `sidebar-hover` is the accent duplicated, so it moves with it.
    "sidebar-hover": "#c47a1e",
    "on-sidebar-hover": "#f7f1ea",
    // Raised controls: inputs and buttons sit above the canvas, pure/near white.
    "input-background": "#ffffff",
    "input-text": "#23211d",
    "cancel-button": "#e6e1d8",
    "cancel-button-text": "#23211d",
    // Deliberately-dark surfaces (e.g. the full-screen image viewer chrome) stay
    // dark even in light mode.
    "dark-surface": "#242628",
    "on-dark-surface": "#f2e5da",
    // Status hues FOR the deliberately-dark surfaces. A `dark-surface` stays
    // dark in both themes, so the theme's own status hues are the wrong values
    // inside it - and in the specific direction that matters: the fill hues are
    // tuned to CARRY a light label, not to BE one. This family is their light
    // counterpart, the same four hues lifted until they read as 11px semibold
    // text (`ReviewRail`'s Abort/Clear). Identical in both themes. Measured
    // 4.60:1 – 6.16:1 plain on `#242628`, 5.23:1 – 7.01:1 on `#181b20`.
    // Deep fill hues here would fail: `error` #b0392b reads 2.51:1, `info`
    // #2f6690 2.48:1, `success` #2a7d3e 2.97:1.
    "dark-surface-error": "#c9786f",
    "dark-surface-warning": "#e8912f",
    "dark-surface-success": "#5d9c6c",
    "dark-surface-info": "#6b92b0",
    // The fifth member of the family, same rationale: `primary` as a FOREGROUND
    // on a dark card. This is the dark theme's outgoing bright olive - a good
    // foreground on a dark card and a bad fill under a white label, so it moves
    // to the token whose whole job is the former. 5.50:1 on the light theme's
    // `dark-surface` #242628, 6.25:1 on the dark theme's #181b20.
    "dark-surface-primary": "#8EA604",
    surface: "#ffffff",
    "on-surface": "#23211d",
    background: "#faf9f7",
    "on-background": "#23211d",
    // ── The action-fill tier (unified Camp B palette) ───────────────────────
    // ONE brand palette shared by both themes (design-system parity, 2026-07-24):
    // the same four brand hues in light and dark, each carrying the warm near-white
    // label #f7f1ea (never pure #fff). Label contrast: primary 4.86:1, secondary
    // 4.91:1, tertiary 4.85:1 - all AA. The amber `accent` was brightened to a
    // warmer, more-orange #c47a1e and now sits at 3.04:1: enough for the semibold
    // button label (AA large), so these fills stay label-only (buttons, chips,
    // rails, icons - never small body text on a canvas).
    accent: "#c47a1e", // warm-white 3.04:1 (brightened from #9e6727)
    "on-accent": "#f7f1ea",
    "accent-bright": "#e08a2a", // brighter, more-orange amber glow: selection / active / focus (not a text/fill token)
    primary: "#567309", // warm-white 4.86:1 (olive)
    "on-primary": "#f7f1ea",
    secondary: "#bb3566", // warm-white 4.91:1 (raspberry)
    "on-secondary": "#f7f1ea",
    tertiary: "#46707a", // warm-white 4.85:1 (teal)
    "on-tertiary": "#f7f1ea",
    // ── Folder-level hues (LibraryLayoutDialog) ────────────────────────────
    // One hue per folder level in the layout builder, so Level 1 is visibly not
    // Level 2. They walk one arc - azure, indigo, magenta, plum - through the
    // only sector the brand does not already occupy, so they read as one
    // ordered family and none of them can be mistaken for the olive Move
    // button or the amber "leaving" delta. Level 4 doubles back to plum rather
    // than continuing into red, to stay clear of `secondary` and `error`.
    //
    // **These are the one family that is NOT shared between the themes**, and
    // the arithmetic forces it: the hue is small TEXT (the select's floating
    // label), so 4.5:1 on white needs luminance <= 0.183 while 4.5:1 on the
    // dark `input-background` needs >= 0.310. Disjoint. So the light values are
    // inks and the dark values are their pale mirrors, the same shape as the
    // `dark-surface-*` family above. Colour is never the only cue - the level
    // number is on the label whatever the hue does.
    // Measured on `surface`/`input-background` #ffffff (and `background`
    // #faf9f7), plus the warm near-white label on the solid fill:
    "level-1": "#12507f", //    8.47:1 / 8.05:1, label 7.55:1
    "on-level-1": "#f7f1ea",
    "level-2": "#5138cf", //    7.46:1 / 7.09:1, label 6.65:1
    "on-level-2": "#f7f1ea",
    "level-3": "#bb1bbb", //    5.27:1 / 5.01:1, label 4.70:1
    "on-level-3": "#f7f1ea",
    "level-4": "#77154e", //   10.49:1 / 9.97:1, label 9.35:1
    "on-level-4": "#f7f1ea",
    // Warm, low-contrast borders: a visible-but-soft divider and a subtler line.
    border: "#d8d3c8",
    divider: "#e8e4dc",
    overlay: "#00000033",
    // Warm hover wash (rgba(45,32,15,.06)) instead of cold black.
    hover: "#2d200f0f",
    // Status hues + their authored foregrounds. The foreground is whichever of
    // the warm near-white / warm near-black clears 4.5:1 on the SOLID fill; it
    // is not a house style, it is the only value that passes. Measured:
    error: "#b54538",
    "on-error": "#f7f1ea", //   4.83:1 (same value in both themes)
    info: "#1a6ec4",
    "on-info": "#ffffff", //    5.16:1
    success: "#2e7d32",
    "on-success": "#ffffff", // 5.13:1
    warning: "#b8861f",
    "on-warning": "#23211d", // 4.95:1 - the warm near-black, never pure #000
    scrim: "#000000",
    shadow: "#1c160c",
    panel: "#efede9",
    "on-panel": "#23211d",
  },
};

const pixlStashDark = {
  dark: true,
  colors: {
    sidebar: "#23282f",
    "sidebar-text": "#d8d0c8",
    toolbar: "#23282f",
    "toolbar-text": "#d8d0c8",
    // `sidebar-hover` is the accent duplicated, so it moves with it.
    "sidebar-hover": "#c47a1e",
    "on-sidebar-hover": "#f7f1ea",
    "input-background": "#2b3138",
    "input-text": "#f2e5da",
    "cancel-button": "#3a4047",
    "cancel-button-text": "#f2e5da",
    "dark-surface": "#181b20",
    "on-dark-surface": "#f2e5da",
    // Same four values as the light theme by design - see the note there. They
    // are deliberately LIGHTER than this theme's own status hues: those are
    // fills, these are foregrounds, and a `dark-surface` needs the latter.
    "dark-surface-error": "#c9786f",
    "dark-surface-warning": "#e8912f",
    "dark-surface-success": "#5d9c6c",
    "dark-surface-info": "#6b92b0",
    // Identical in both themes, like the four above. Keeps the retired bright
    // olive in service as a dark-card foreground (6.25:1 on #181b20).
    "dark-surface-primary": "#8EA604",
    surface: "#23282f",
    "on-surface": "#f2e5da",
    background: "#1b1f24",
    "on-background": "#f2e5da",
    // ── The action-fill tier (unified Camp B palette) ───────────────────────
    // Identical to the light theme by design - one brand palette in both themes
    // (design-system parity, 2026-07-24). Same warm-white #f7f1ea label, same
    // contrast (primary 4.86:1, secondary 4.91:1, tertiary 4.85:1; accent
    // #c47a1e 3.04:1, AA-large for the semibold button label).
    accent: "#c47a1e", // warm-white 3.04:1 (brightened, more orange)
    "on-accent": "#f7f1ea",
    "accent-bright": "#e08a2a", // brighter, more-orange amber glow: selection / active / focus (not a text/fill token)
    primary: "#567309", // warm-white 4.86:1 (olive)
    "on-primary": "#f7f1ea",
    secondary: "#bb3566", // warm-white 4.91:1 (raspberry)
    "on-secondary": "#f7f1ea",
    tertiary: "#46707a", // warm-white 4.85:1 (teal)
    "on-tertiary": "#f7f1ea",
    // ── Folder-level hues (LibraryLayoutDialog) ────────────────────────────
    // One hue per folder level in the layout builder, so Level 1 is visibly not
    // Level 2. They walk one arc - azure, indigo, magenta, plum - through the
    // only sector the brand does not already occupy, so they read as one
    // ordered family and none of them can be mistaken for the olive Move
    // button or the amber "leaving" delta. Level 4 doubles back to plum rather
    // than continuing into red, to stay clear of `secondary` and `error`.
    //
    // **These are the one family that is NOT shared between the themes**, and
    // the arithmetic forces it: the hue is small TEXT (the select's floating
    // label), so 4.5:1 on white needs luminance <= 0.183 while 4.5:1 on the
    // dark `input-background` needs >= 0.310. Disjoint. So the light values are
    // inks and the dark values are their pale mirrors, the same shape as the
    // `dark-surface-*` family above. Colour is never the only cue - the level
    // number is on the label whatever the hue does.
    // Measured on `input-background` #2b3138 (the tightest of the three) and
    // with the warm near-black label on the solid fill:
    "level-1": "#7cbaea", //    6.30:1, label 7.71:1
    "on-level-1": "#23211d",
    "level-2": "#a898f0", //    5.26:1, label 6.44:1
    "on-level-2": "#23211d",
    "level-3": "#de7ad0", //    4.88:1, label 5.97:1
    "on-level-3": "#23211d",
    "level-4": "#ed9bcb", //    6.35:1, label 7.77:1
    "on-level-4": "#23211d",
    border: "#363d45",
    divider: "#2c323a",
    overlay: "#00000066",
    hover: "#ffffff14",
    // Status hues are DEEP in both themes (unified Camp B palette), so three of
    // the four carry the warm near-white label like every other fill tier here.
    // `warning` is the exception: it is bright enough that the label has to flip
    // to the warm near-black instead. Foreground-on-dark-chrome is a different
    // job with its own family - see `dark-surface-<status>` above; do not reach
    // for these values there. Measured on the SOLID fill:
    error: "#b54538",
    "on-error": "#f7f1ea", //   4.83:1
    info: "#3b6f97",
    "on-info": "#f7f1ea", //    4.79:1
    success: "#2a7d3e",
    "on-success": "#f7f1ea", // 4.57:1
    warning: "#e8912f",
    "on-warning": "#1b1b1b", // 6.99:1
    scrim: "#000000",
    shadow: "#2a2f36",
    panel: "#313337",
    "on-panel": "#f2e5da",
  },
};

const vuetify = createVuetify({
  theme: {
    // The first frame is the theme this browser used last, so nobody watches
    // the app change its mind on the way in. With nothing remembered - a new
    // setup, a new browser - it is dark: PixlStash is a picture app, a dark
    // canvas is what a photograph is looked at against, and it matches the
    // desktop shell the window opens from. The stored `theme_mode` still wins
    // the moment the config lands.
    defaultTheme:
      readRememberedTheme() === "light" ? "pixlStashLight" : "pixlStashDark",
    themes: {
      pixlStashLight,
      pixlStashDark,
    },
  },
  components,
  directives,
});

createApp(Root).use(createPinia()).use(vuetify).use(router).mount("#app");
// Marks the synchronous mount only (Root.vue's own auth check is async and
// timed separately, see Root.vue) - this is "first paint of *something*".
markEnd("pixlstash:boot");
