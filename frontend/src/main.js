// Vuetify styles
import 'vuetify/styles'
import '@mdi/font/css/materialdesignicons.css'
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
const pixlStashLight = {
  dark: false,
  colors: {
    'sidebar': '#595f66',
    'sidebar-text': '#f2f5fa',
    'toolbar': '#c0c3c5',
    'toolbar-text': '#393f46',
    'sidebar-hover': '#f28f3b',
    'on-sidebar-hover': '#f2e5da',
    'input-background': '#e2e4e7',
    'input-text': '#393f46',
    'cancel-button': '#5f5f5f',
    'cancel-button-text': '#f2e5da',
    'dark-surface': '#242628',
    'on-dark-surface': '#f2e5da',
    surface: '#e5e6ea',
    onSurface: '#2f343b',
    background: '#C0C2C4',
    onBackground: '#292f36',
    accent: '#f28f3b',
    onAccent: '#ffffff',
    primary: '#8EA604',
    onPrimary: '#f2e5da',
    secondary: '#DA4167',
    onSecondary: '#f2e5da',
    tertiary: '#77A0A9',
    onTertiary: '#f2e5da',
    border: '#9aa0a6',
    divider: '#d4c8bd',
    overlay: '#00000033',
    focus: '#7c4dff',
    hover: '#00000014',
    error: '#f44336',
    info: '#2196F3',
    success: '#4caf50',
    warning: '#db7900',
    scrim: '#000000',
    shadow: '#000000',
    panel: '#c0c3c5',
    onPanel: '#2f343b',
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
