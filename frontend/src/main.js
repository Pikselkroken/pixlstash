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
    "sidebar-hover": "#b0732b",
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
    surface: "#ffffff",
    "on-surface": "#23211d",
    background: "#faf9f7",
    "on-background": "#23211d",
    accent: "#b0732b",
    "on-accent": "#ffffff",
    primary: "#5c7c0a",
    "on-primary": "#ffffff",
    secondary: "#cb3a72",
    "on-secondary": "#ffffff",
    tertiary: "#5f8790",
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
    "sidebar-hover": "#f28f3b",
    "on-sidebar-hover": "#f2e5da",
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
    surface: "#23282f",
    "on-surface": "#f2e5da",
    background: "#1b1f24",
    "on-background": "#f2e5da",
    accent: "#f28f3b",
    "on-accent": "#1b1b1b",
    primary: "#8EA604",
    "on-primary": "#111111",
    secondary: "#DA4167",
    "on-secondary": "#ffffff",
    tertiary: "#77A0A9",
    "on-tertiary": "#0f1418",
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
