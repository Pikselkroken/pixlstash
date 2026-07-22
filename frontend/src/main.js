// Vuetify styles
import "vuetify/styles";
import "@mdi/font/css/materialdesignicons.css";
import "./styles/design-tokens.css";
import "./style.css";
import "./styles/context-menu.css";

import { createApp } from "vue";
import { createPinia } from "pinia";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import * as directives from "vuetify/directives";
import router from "./router/index.js";

import Root from "./Root.vue";

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
// the app actually reads — as pure `#000` / `#fff` from `getForeground()`. Every
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
// contrast on the light canvas — all four of them: `error` #cf3b30 (4.62:1 on
// the canvas), `warning` #b8861f (3.09:1), `success` #2e7d32 (4.87:1) and `info`
// #1a6ec4 (4.90:1). `success` and `info` were Material 500s until 2026-07 and
// measured 2.64:1 / 2.97:1, i.e. below the 3:1 UI floor, which made this comment
// false for half the set.
const pixlStashLight = {
  dark: false,
  colors: {
    // Chrome: sidebar / toolbar / panels — warm tinted grey, recedes behind the
    // canvas. In the desktop shell these are remapped to `background` (see
    // style.css) so the titlebar + toolbar + sidebar read as one strip; these
    // values drive the browser layout.
    sidebar: "#f0ede9",
    "sidebar-text": "#25231e",
    toolbar: "#f0ede9",
    "toolbar-text": "#25231e",
    // `sidebar-hover` is the accent duplicated, so it moves with it. White on
    // the new value measures 4.75:1 (was 3.94:1).
    "sidebar-hover": "#9e6727",
    "on-sidebar-hover": "#ffffff",
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
    // dark in both themes, so the theme's own status hues — tuned for that
    // theme's canvas — are the wrong values inside it: the deepened light
    // `success` reads 2.96:1 there. These four are identical in both themes
    // (they are the dark-tuned hues) and measure 4.12:1 – 5.46:1 plain on
    // `#242628`, 3.49:1 – 4.23:1 on their own 16% tint.
    "dark-surface-error": "#f44336",
    "dark-surface-warning": "#db7900",
    "dark-surface-success": "#4caf50",
    "dark-surface-info": "#2196F3",
    // The fifth member of the family, same rationale: `primary` as a FOREGROUND
    // on a dark card. This is the dark theme's outgoing bright olive — a good
    // foreground on a dark card and a bad fill under a white label, so it moves
    // to the token whose whole job is the former. 5.50:1 on the light theme's
    // `dark-surface` #242628, 6.25:1 on the dark theme's #181b20.
    "dark-surface-primary": "#8EA604",
    surface: "#ffffff",
    "on-surface": "#23211d",
    background: "#faf9f7",
    "on-background": "#23211d",
    // ── The action-fill tier ────────────────────────────────────────────────
    // The foreground on all four is #ffffff in BOTH themes, always: one label
    // colour on every branded fill, and no `on-*` pair that can silently
    // disagree with its fill. A white label fixes the fill's lightness at
    // L <= 0.1833; see visual-language.md §4 for the arithmetic.
    // Consequence: these four are NEVER small body text on a canvas (4.49-4.60:1
    // light, 3.53-3.61:1 dark) — icons, borders, rails, >=18px or >=14px bold.
    accent: "#9e6727", // white 4.75:1 (was #b0732b, 3.94:1)
    "on-accent": "#ffffff",
    primary: "#5c7c0a", // white 4.84:1 — unchanged
    "on-primary": "#ffffff",
    secondary: "#cb3a72", // white 4.79:1 — unchanged
    "on-secondary": "#ffffff",
    tertiary: "#557982", // white 4.73:1 (was #5f8790, 3.92:1)
    "on-tertiary": "#ffffff",
    // Warm, low-contrast borders: a visible-but-soft divider and a subtler line.
    border: "#d8d3c8",
    divider: "#e8e4dc",
    overlay: "#00000033",
    focus: "#7c4dff",
    // Warm hover wash (rgba(45,32,15,.06)) instead of cold black.
    hover: "#2d200f0f",
    // Status hues + their authored foregrounds. The foreground is whichever of
    // the warm near-white / warm near-black clears 4.5:1 on the SOLID fill; it
    // is not a house style, it is the only value that passes. Measured:
    error: "#cf3b30",
    "on-error": "#ffffff", //   4.86:1
    info: "#1a6ec4",
    "on-info": "#ffffff", //    5.16:1
    success: "#2e7d32",
    "on-success": "#ffffff", // 5.13:1
    warning: "#b8861f",
    "on-warning": "#23211d", // 4.95:1 — the warm near-black, never pure #000
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
    // Was #f28f3b + #f2e5da = 1.94:1 — the worst pair in either theme. Now the
    // accent value with a white label: 4.59:1.
    "sidebar-hover": "#b85c0c",
    "on-sidebar-hover": "#ffffff",
    "input-background": "#2b3138",
    "input-text": "#f2e5da",
    "cancel-button": "#3a4047",
    "cancel-button-text": "#f2e5da",
    "dark-surface": "#181b20",
    "on-dark-surface": "#f2e5da",
    // Same four values as the light theme by design — see the note there. In
    // this theme they coincide with the theme's own status hues, so pointing a
    // dark-surface consumer at them is a no-op here and a fix in light mode.
    "dark-surface-error": "#f44336",
    "dark-surface-warning": "#db7900",
    "dark-surface-success": "#4caf50",
    "dark-surface-info": "#2196F3",
    // Identical in both themes, like the four above. Keeps the retired bright
    // olive in service as a dark-card foreground (6.25:1 on #181b20).
    "dark-surface-primary": "#8EA604",
    surface: "#23282f",
    "on-surface": "#f2e5da",
    background: "#1b1f24",
    "on-background": "#f2e5da",
    // ── The action-fill tier ────────────────────────────────────────────────
    // White labels are the invariant (see the light theme above), so the FILLS
    // moved rather than the labels. Every dark action fill sits in the window
    // L in [0.1624, 0.1833]: bright enough to clear 3:1 on the dark canvas,
    // dark enough to carry white at 4.5:1. Hue is untouched (H28/H69/H345/H191);
    // only lightness moves. These sit at the bright end of the legal window, so
    // there is no value that is both brighter and legal.
    accent: "#b85c0c", // white 4.59:1 (was #f28f3b + #1b1b1b, 2.40:1)
    "on-accent": "#ffffff",
    primary: "#6b7d04", // white 4.60:1 (was #8EA604 + #111111, 2.76:1)
    "on-primary": "#ffffff",
    secondary: "#d13a5f", // white 4.69:1 (was #DA4167, 4.26:1)
    "on-secondary": "#ffffff",
    tertiary: "#547b84", // white 4.62:1 (was #77A0A9 + #0f1418, 2.84:1)
    "on-tertiary": "#ffffff",
    border: "#363d45",
    divider: "#2c323a",
    overlay: "#00000066",
    focus: "#7c4dff",
    hover: "#ffffff14",
    // Status hues stay BRIGHT in dark mode: their dominant use is as a
    // foreground on a dark surface, where deepening them would hurt. A bright
    // fill takes a dark foreground, so all four pair with the same warm
    // near-black the theme already uses for `on-accent`. Measured:
    error: "#f44336",
    "on-error": "#1b1b1b", //   4.68:1
    info: "#2196F3",
    "on-info": "#1b1b1b", //    5.51:1
    success: "#4caf50",
    "on-success": "#1b1b1b", // 6.20:1
    warning: "#db7900",
    "on-warning": "#1b1b1b", // 5.53:1
    scrim: "#000000",
    shadow: "#2a2f36",
    panel: "#313337",
    "on-panel": "#f2e5da",
  },
};

const vuetify = createVuetify({
  theme: {
    defaultTheme: "pixlStashLight",
    themes: {
      pixlStashLight,
      pixlStashDark,
    },
  },
  components,
  directives,
});

createApp(Root).use(createPinia()).use(vuetify).use(router).mount("#app");
