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
const pixlVaultTheme = {
  dark: true,
  colors: {
    'sidebar': '#393f46',
    'sidebar-text': '#f2e5da',
    'toolbar': '#444850',
    'toolbar-text': '#f2e5da',
    'sidebar-hover': '#f28f3b',
    'on-sidebar-hover': '#f2e5da',
    'input-background': '#646668',
    'input-text': '#f2e5da',
    'cancel-button': '#5f5f5f',
    'cancel-button-text': '#f2e5da',
    surface: '#545658',
    onSurface: '#f2e5da',
    background: '#d9dfe2',
    onBackground: '#393f46',
    accent: '#f28f3b',
    onAccent: '#ffffff',
    primary: '#379392',
    onPrimary: '#f2e5da',
    secondary: '#c8553d',
    onSecondary: '#f2e5da',
    tertiary: '#dce2aa',
    onTertiary: '#444850',
    border: '#262828',
    divider: '#d4c8bd',
    overlay: '#00000033',
    focus: '#7c4dff',
    hover: '#00000014',
    error: '#f44336',
    info: '#2196F3',
    success: '#4caf50',
    warning: '#fb8c00',
  },
};


const vuetify = createVuetify({
  theme: {
    defaultTheme: 'pixlVaultTheme',
    themes: {
      pixlVaultTheme,
    },
  },
  components,
  directives,
})

createApp(Root).use(vuetify).mount('#app')
