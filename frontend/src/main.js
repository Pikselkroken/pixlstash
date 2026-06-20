// Vuetify styles
import 'vuetify/styles'
import '@mdi/font/css/materialdesignicons.css'
import './styles/design-tokens.css'
import './style.css'
import './styles/context-menu.css'

import {createApp} from 'vue'
import {createPinia} from 'pinia'
import {createVuetify} from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import router from './router/index.js'

import Root from './Root.vue'

// Tag the document when running inside the Electron desktop shell so CSS can
// apply native-app chrome (thin scrollbars, no text-selection on chrome)
// without changing the experience for plain browser visitors.
if (typeof window !== 'undefined' && window.pixlstashDesktop) {
  document.documentElement.classList.add('is-desktop')
}

// Custom theme properties
// Warm light theme. Elevation inverts vs dark: the content canvas is the
// brightest surface and chrome (sidebar / toolbar / panels) recedes to a warm
// tinted grey, with raised controls (cards, inputs) going pure white. Text is a
// warm near-black ramp, never pure #000. Status hues are deepened so they hold
// contrast on the light canvas. (Designed to replace the old cold LCD grey.)
const pixlStashLight = {
  dark: false,
  colors: {
    // Chrome: sidebar / toolbar / panels — warm tinted grey, recedes behind the
    // canvas. In the desktop shell these are remapped to `background` (see
    // style.css) so the titlebar + toolbar + sidebar read as one strip; these
    // values drive the browser layout.
    'sidebar': '#f0ede9',
    'sidebar-text': '#23211d',
    'toolbar': '#f0ede9',
    'toolbar-text': '#23211d',
    'sidebar-hover': '#b0732b',
    'on-sidebar-hover': '#ffffff',
    // Raised controls: inputs and buttons sit above the canvas, pure/near white.
    'input-background': '#ffffff',
    'input-text': '#23211d',
    'cancel-button': '#e6e1d8',
    'cancel-button-text': '#23211d',
    // Deliberately-dark surfaces (e.g. the full-screen image viewer chrome) stay
    // dark even in light mode.
    'dark-surface': '#242628',
    'on-dark-surface': '#f2e5da',
    surface: '#ffffff',
    onSurface: '#23211d',
    background: '#faf9f7',
    onBackground: '#23211d',
    accent: '#b0732b',
    onAccent: '#ffffff',
    primary: '#5c7c0a',
    onPrimary: '#ffffff',
    secondary: '#cb3a72',
    onSecondary: '#ffffff',
    tertiary: '#5f8790',
    onTertiary: '#ffffff',
    // Warm, low-contrast borders: a visible-but-soft divider and a subtler line.
    border: '#d8d3c8',
    divider: '#e8e4dc',
    overlay: '#00000033',
    focus: '#7c4dff',
    // Warm hover wash (rgba(45,32,15,.06)) instead of cold black.
    hover: '#2d200f0f',
    error: '#cf3b30',
    info: '#2196F3',
    success: '#4caf50',
    warning: '#b8861f',
    scrim: '#000000',
    shadow: '#1c160c',
    panel: '#efede9',
    onPanel: '#23211d',
  },
};

const pixlStashDark = {
  dark: true,
  colors: {
    'sidebar': '#23282f',
    'sidebar-text': '#f2e5da',
    'toolbar': '#23282f',
    'toolbar-text': '#f2e5da',
    'sidebar-hover': '#f28f3b',
    'on-sidebar-hover': '#f2e5da',
    'input-background': '#2b3138',
    'input-text': '#f2e5da',
    'cancel-button': '#3a4047',
    'cancel-button-text': '#f2e5da',
    'dark-surface': '#181b20',
    'on-dark-surface': '#f2e5da',
    surface: '#23282f',
    onSurface: '#f2e5da',
    background: '#1b1f24',
    onBackground: '#f2e5da',
    accent: '#f28f3b',
    onAccent: '#1b1b1b',
    primary: '#8EA604',
    onPrimary: '#111111',
    secondary: '#DA4167',
    onSecondary: '#ffffff',
    tertiary: '#77A0A9',
    onTertiary: '#0f1418',
    border: '#363d45',
    divider: '#2c323a',
    overlay: '#00000066',
    focus: '#7c4dff',
    hover: '#ffffff14',
    error: '#f44336',
    info: '#2196F3',
    success: '#4caf50',
    warning: '#db7900',
    scrim: '#000000',
    shadow: '#2a2f36',
    panel: '#313337',
    onPanel: '#f2e5da',
  },
};


const vuetify = createVuetify({
  theme: {
    defaultTheme: 'pixlStashLight',
    themes: {
      pixlStashLight,
      pixlStashDark,
    },
  },
  components,
  directives,
})

createApp(Root).use(createPinia()).use(vuetify).use(router).mount('#app')
