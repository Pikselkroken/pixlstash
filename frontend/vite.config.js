import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))

// Read the PixlStash version from the root pyproject.toml.
//
// PIXLSTASH_VERSION_OVERRIDE pins it instead. Only the screenshot harness sets
// it (`npm run screenshots 1.9.0`, see e2e/screenshots/run.js), so marketing
// captures can be stamped with a release number while the tree is still on an
// rc or dev version. A normal `npm run build` never sets it and always reports
// what the tree actually is.
function readPixlStashVersion() {
  const pinned = process.env.PIXLSTASH_VERSION_OVERRIDE?.trim()
  if (pinned) return pinned
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
    // Resolves to <repo>/pixlstash/frontend/dist — i.e. the in-repo Python
    // *package* (named "pixlstash"), which is what `python -m pixlstash.app`
    // serves. NOT the sibling ~/Projects/pixlstash repo. Do not change to
    // "../pixlstash-main/..." — that resolves to a doubled, bogus path.
    outDir: '../pixlstash/frontend/dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1024,
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
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.{js,ts}'],
    // jsdom has no layout, so the observer APIs any measuring component needs
    // are stubbed once here rather than re-declared per suite.
    setupFiles: ['src/testing/setup.js'],
    server: {
      deps: {
        // Vuetify ships its component CSS as sibling `.css` imports, which Node
        // cannot load. Any test that mounts a component importing from
        // "vuetify/components" therefore died at import with
        // `Unknown file extension ".css"`. Inlining routes Vuetify through
        // Vite's transform, where the CSS import is handled, so a component that
        // uses AppDialog or AppButton is testable at all.
        inline: ['vuetify'],
      },
    },
  },
})
