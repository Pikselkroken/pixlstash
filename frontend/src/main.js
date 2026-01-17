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
    background: '#444444',
    surface: '#838d9dff',
    surfaceVariant: '#f2e5da',  // Custom slider track color
    primary: '#434e5dff',
    onPrimary: '#f2e5da',
    secondary: '#808080ff',
    onSecondary: '#f2e5da',
    tertiary: '#7f95aaff',
    onTertiary: '#f2e5da',
    accent: '#ffa600',
    onAccent: '#1f2329',
    textPrimary: '#1f2329',
    textSecondary: '#4b5563',
    textMuted: '#9aa3af',
    border: '#c8c0b8',
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
