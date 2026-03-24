// Vuetify styles
import 'vuetify/styles'
import '@mdi/font/css/materialdesignicons.css'
import './style.css'

import {createApp} from 'vue'
import {createVuetify} from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'

import Root from './Root.vue'

// Custom theme properties
const pixlStashLight = {
  dark: false,
  colors: {
    'sidebar': '#696f76',
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
    background: '#C0C3C5',
    onBackground: '#393f46',
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
    panel: '#ffffff',
    onPanel: '#2f343b',
  },
};

const pixlStashDark = {
  dark: true,
  colors: {
    'sidebar': '#494f56',
    'sidebar-text': '#f2f5fa',
    'toolbar': '#2a2f36',
    'toolbar-text': '#f2e5da',
    'sidebar-hover': '#f28f3b',
    'on-sidebar-hover': '#f2e5da',
    'input-background': '#343a40',
    'input-text': '#f2e5da',
    'cancel-button': '#4f545b',
    'cancel-button-text': '#f2e5da',
    'dark-surface': '#1f2328',
    'on-dark-surface': '#f2e5da',
    surface: '#2b3138',
    onSurface: '#f2e5da',
    background: '#2a2f36',
    onBackground: '#f2e5da',
    accent: '#f28f3b',
    onAccent: '#1b1b1b',
    primary: '#8EA604',
    onPrimary: '#111111',
    secondary: '#DA4167',
    onSecondary: '#ffffff',
    tertiary: '#77A0A9',
    onTertiary: '#0f1418',
    border: '#8c838b',
    divider: '#3a4047',
    overlay: '#00000066',
    focus: '#7c4dff',
    hover: '#ffffff14',
    error: '#f44336',
    info: '#2196F3',
    success: '#4caf50',
    warning: '#db7900',
    scrim: '#000000',
    shadow: '#2a2f36',
    panel: '#111317',
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

createApp(Root).use(vuetify).mount('#app')
