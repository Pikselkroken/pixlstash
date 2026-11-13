// The website is its own repository (pixlstash-website). The captures read its
// script.json and land in its gitignored screenshots/output/, so the harness
// needs to be told where a checkout of it is.
import { join } from 'node:path'

const checkout = process.env.PIXLSTASH_WEBSITE
if (!checkout) {
  throw new Error(
    'Set PIXLSTASH_WEBSITE to a checkout of the pixlstash-website repository: ' +
      'PIXLSTASH_WEBSITE=<path> npm run screenshots',
  )
}

export const OUT_DIR = join(checkout, 'screenshots', 'output')
export const SCRIPT = join(checkout, 'screenshots', 'script.json')
