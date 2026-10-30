// The e2e backend seeds workflows, pictures and recipes for the Workflows
// specs (#2314), so they can run against the real API instead of page.route.
//
// serve_e2e_backend.py writes the seed under its throwaway work dir and the
// server imports it at start-up (`e2e_workflow_seed`). This spec is the seed's
// own check. Cards and recipes are found by name, never counted whole, so a
// spec that adds a workflow before this one runs cannot turn it red.

import { test, expect } from '../fixtures/test.js'

const LORA_WORKFLOW = 'Seeded LoRA portrait'
const PLAIN_WORKFLOW = 'Seeded plain txt2img'
const HIDDEN_WORKFLOW = 'Seeded hidden draft'

// A workflow known from seeded pictures: none stored, made with the seeded
// checkpoint, from exactly `count` pictures.
const fromSeededPictures = (count) => (c) =>
  !c.manual &&
  c.picture_count === count &&
  c.recipe_values.checkpoints.some((v) => v.name === 'RealVisXL_V4.safetensors')

async function cards(apiContext, query = '') {
  const res = await apiContext.get(`/api/v1/workflows${query}`)
  expect(res.ok(), await res.text()).toBe(true)
  return res.json()
}

test('GET /workflows lists the seeded cards, a hidden one and a one-off', async ({
  apiContext,
}) => {
  const grid = await cards(apiContext)
  const names = grid.cards.map((c) => c.name)
  expect(names).toEqual(expect.arrayContaining([LORA_WORKFLOW, PLAIN_WORKFLOW]))
  expect(names).not.toContain(HIDDEN_WORKFLOW)

  const lora = grid.cards.find((c) => c.name === LORA_WORKFLOW)
  expect(lora.loras.map((l) => l.filename)).toEqual([
    'Clementine_Portrait.safetensors',
    'DetailTweaker_v2.safetensors',
  ])
  expect(lora.models.map((m) => m.filename)).toEqual(['RealVisXL_V4.safetensors'])

  // The workflow the three seeded portraits are filed under: covers, a count,
  // and its pictures route answering the same three.
  const drawn = grid.cards.find(fromSeededPictures(3))
  expect(drawn, JSON.stringify(names)).toBeTruthy()
  expect(drawn.recipe_values.loras.map((v) => v.name)).toEqual([
    'Clementine_Portrait.safetensors',
  ])
  expect(drawn.covers).toHaveLength(3)
  const res = await apiContext.get(`/api/v1/workflows/${drawn.id}/pictures`)
  expect(res.ok(), await res.text()).toBe(true)
  expect((await res.json()).sort()).toEqual(drawn.covers.map((c) => c.picture_id).sort())

  expect(grid.hidden).toBeGreaterThanOrEqual(1)
  expect(grid.one_offs).toBeGreaterThanOrEqual(1)
  const all = await cards(apiContext, '?include_hidden=true&include_one_offs=true')
  const hidden = all.cards.find((c) => c.name === HIDDEN_WORKFLOW)
  expect(hidden?.hidden).toBe(true)
  const shown = new Set(grid.cards.map((c) => c.id))
  const oneOff = all.cards.find((c) => !shown.has(c.id) && fromSeededPictures(1)(c))
  expect(oneOff, JSON.stringify(all.cards.map((c) => c.name))).toBeTruthy()
})

test('GET /recipes lists the seeded recipes, one of them unfiled', async ({
  apiContext,
}) => {
  const { cards: listed } = await cards(apiContext)
  const lora = listed.find((c) => c.name === LORA_WORKFLOW)

  const res = await apiContext.get('/api/v1/recipes')
  expect(res.ok(), await res.text()).toBe(true)
  const recipes = Object.fromEntries((await res.json()).map((r) => [r.name, r]))
  expect(recipes['Seeded golden hour']?.workflow_id).toBe(lora.id)
  expect(recipes['Seeded studio light']?.workflow_id).toBe(lora.id)
  expect(recipes['Seeded unfiled look']?.workflow_id).toBeNull()

  const unfiled = await apiContext.get('/api/v1/recipes?unfiled=true')
  expect(unfiled.ok(), await unfiled.text()).toBe(true)
  const look = (await unfiled.json()).find((r) => r.name === 'Seeded unfiled look')
  expect(look?.extractable).toBe(true)
})

test('the Workflows view draws the seeded cards', async ({ page }) => {
  await page.goto('/workflows')
  const rows = page.locator('.wfv-row')
  await expect(rows.filter({ hasText: LORA_WORKFLOW })).toHaveCount(1, { timeout: 15_000 })
  await expect(rows.filter({ hasText: PLAIN_WORKFLOW })).toHaveCount(1)
  await expect(rows.filter({ hasText: HIDDEN_WORKFLOW })).toHaveCount(0)
})
