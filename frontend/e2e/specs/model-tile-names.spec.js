// Every name a workflow card's tile can print fits it (#1951).
//
// A known model's tile carries the short name people write for it, sized to
// fill the tile's line (the card's `--wf-mark` less 2px padding a side) and
// shrunk for a long one, but never below 8.25px (`.mmark-name` in
// `ModelMark.vue`). A name that would need less overflows the tile, so each
// one in `KNOWN_BASE_MODELS` and `KNOWN_FINE_TUNES` is rendered here, in the
// tile's face in Chromium, where jsdom cannot measure text at all. CI's
// `system-ui` is DejaVu Sans, the widest face measured, which is the point. A
// tab's role word is held to the tile: the tab hangs as far past the tile's
// right edge as its padding and border take, so a word wider than the tile
// pushes the tab past its left edge.

import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from '../fixtures/test.js'

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
// The card's mark size, read off the card so this follows it.
const TILE_PX = Number(
  readFileSync(resolve(REPO_ROOT, 'frontend/src/components/widgets/WorkflowCard.vue'), 'utf8').match(
    /--wf-mark: (\d+)px;/,
  )[1],
)
const LINE_PX = TILE_PX - 2 * 2

const READ_NAMES = `
import json
from pixlstash.utils.known_base_models import KNOWN_BASE_MODELS, KNOWN_FINE_TUNES, ROLE_WORDS
tiles = [*KNOWN_BASE_MODELS.values(), *KNOWN_FINE_TUNES.values()]
print(json.dumps({
    "lines": sorted({line for t in tiles for line in t.get("mark", [])}),
    "roles": sorted({t["role"] for t in tiles if t.get("role")} | set(ROLE_WORDS.values())),
}))
`

function knownNames() {
  const python = process.env.PIXLSTASH_PYTHON || 'python'
  const stdout = execFileSync(python, ['-c', READ_NAMES], {
    encoding: 'utf8',
    env: { ...process.env, PYTHONPATH: REPO_ROOT },
  })
  return JSON.parse(stdout)
}

test('every known name fits the tile at the smallest size it may take', async ({ page }) => {
  const { lines, roles } = knownNames()
  expect(lines.length).toBeGreaterThan(30)

  await page.goto('/')
  const widths = await page.evaluate(
    ({ lines, roles }) => {
      const measure = (text, style) => {
        const span = document.createElement('span')
        Object.assign(span.style, { position: 'absolute', whiteSpace: 'nowrap', ...style })
        span.textContent = text
        document.body.appendChild(span)
        const width = span.getBoundingClientRect().width
        span.remove()
        return [text, width]
      }
      return {
        // `.mmark-name`: semibold, tracked -0.02em, at the floor.
        lines: lines.map((line) =>
          measure(line, {
            fontFamily: 'var(--font-ui)',
            fontSize: '8.25px',
            fontWeight: '600',
            letterSpacing: '-0.02em',
          }),
        ),
        // `.mmark-tab`: the type ramp's 3xs, bold.
        roles: roles.map((role) =>
          measure(role, { fontSize: 'var(--text-3xs)', fontWeight: '700' }),
        ),
      }
    },
    { lines, roles },
  )

  const tooWide = (measured, budget) =>
    measured.filter(([, width]) => width > budget).map(([text, width]) => `${text} ${width}px`)
  expect(tooWide(widths.lines, LINE_PX), `names wider than the ${LINE_PX}px line at 8.25px`).toEqual(
    [],
  )
  expect(tooWide(widths.roles, TILE_PX), `tab words wider than the ${TILE_PX}px tile`).toEqual([])
})
