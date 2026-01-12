// Vuetify styles
import 'vuetify/styles'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import '@mdi/font/css/materialdesignicons.css'

import { createApp } from 'vue'
import './style.css'
import Root from './Root.vue'

// Custom theme properties
const pixlVaultTheme = {
  dark: false,
  colors: {
    background: "#f2e5da",
    surface: "#838d9dff",
    surfaceVariant: "#f2e5da", // Custom slider track color
    primary: "#434e5dff",
    onPrimary: "#f2e5da",
    secondary: "#606268ff",
    error: "#f44336",
    info: "#2196F3",
    success: "#4caf50",
    warning: "#fb8c00",
  },
};


const vuetify = createVuetify({
    theme: {
        defaultTheme: "pixlVaultTheme",
        themes: {
        pixlVaultTheme,
        },
    },
    components,
    directives,
})

createApp(Root)
    .use(vuetify)
    .mount('#app')
