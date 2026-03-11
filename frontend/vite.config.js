import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))

// Read the PixlStash version from the root pyproject.toml
function readPixlStashVersion() {
  try {
    const toml = readFileSync(resolve(__dirname, '../pyproject.toml'), 'utf-8')
    const match = toml.match(/^version\s*=\s*"([^"]+)"/m)
    return match ? match[1] : 'unknown'
  } catch {
    return 'unknown'
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify(readPixlStashVersion()),
  },
  build: {
    outDir: '../pixlstash/frontend/dist',
    emptyOutDir: true,
  },
  server: {
    host: true, // Listen on all network interfaces
    port: 5173, // Optional: Ensure the port is set to 5173
    hmr: {
      protocol: 'ws',
      host: 'localhost',
      port: 5173,
      clientPort: 5173,
    },
  },
})
