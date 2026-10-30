import { test, expect } from '../fixtures/test.js'

// Editing in the lightbox (#2326): rotate, crop, the description, and naming a
// face. Every result is read back through the API or after a reload, not only
// off the screen, and every test puts back what it changed: the suite shares
// one backend.
//
// Not here: painting a mask and taking it to a new picture. A selection is read
// only by an inpaint workflow, which runs on ComfyUI, and the e2e backend has
// none (the paint tools are not even offered without one). Crop, the one tool
// that works without it, is covered below. Nor does a reset description come
// back as generated text here: the captioner is a background sweep, and the
// e2e backend runs without workers. The reset is checked up to the point the
// text is cleared for it.
//
// Nothing here turns a seeded picture. A turn NULLs the picture's embedding and
// perceptual hash for the sweeps to redo, and with no workers nothing redoes
// them, so a turned seeded picture drops out of every later spec's tag scan
// (review-session counts on exactly two "black hair" suspects). Undo puts the
// orientation back, not the embedding. The rotate tests turn a copy instead.

const API = '/api/v1'

// The first cards are where the read-only lightbox specs and the rating spec
// work; start past them so no two specs edit one picture.
const SKIP_LEADING_CARDS = 4

async function metadata(apiContext, id) {
  const res = await apiContext.get(`${API}/pictures/${id}/metadata`)
  expect(res.ok(), await res.text()).toBe(true)
  return res.json()
}

async function faces(apiContext, id) {
  const res = await apiContext.get(`${API}/pictures/${id}/faces`)
  expect(res.ok(), await res.text()).toBe(true)
  const body = await res.json()
  return Array.isArray(body) ? body : body.faces ?? []
}

/** An unturned file reads `null` (never written) or 1 (turned back). */
function upright(orientation) {
  return orientation == null || orientation === 1
}

/**
 * Pictures the grid has on screen that any of these edits may touch: a JPEG
 * of our own (not a reference-folder file), upright, in no stack. In grid
 * order, past the leading cards.
 */
async function editablePictures(page, apiContext) {
  const srcs = await page
    .locator('.thumbnail-img')
    .evaluateAll((imgs) => imgs.map((img) => img.getAttribute('src') || ''))
  const ids = [
    ...new Set(
      srcs.map((src) => src.match(/\/thumbnails\/(\d+)\./)?.[1]).filter(Boolean).map(Number),
    ),
  ].slice(SKIP_LEADING_CARDS)
  const out = []
  for (const id of ids) {
    const m = await metadata(apiContext, id)
    if (
      m.format === 'JPEG' &&
      m.stack_id == null &&
      m.reference_folder_id == null &&
      upright(m.orientation) &&
      !m.locked
    ) {
      out.push(id)
    }
  }
  expect(out.length, 'the grid should show several editable JPEGs').toBeGreaterThan(5)
  return out
}

/** Pictures from `ids` with exactly one face, and no person on it yet. */
async function withOneUnnamedFace(apiContext, ids) {
  const out = []
  for (const id of ids) {
    const rows = await faces(apiContext, id)
    if (rows.length === 1 && rows[0].character_id == null) out.push({ id, face: rows[0] })
  }
  return out
}

async function openPicture(page, lightbox, id) {
  await page.goto(`/?overlay=${id}`)
  await expect(lightbox.root).toBeVisible({ timeout: 15_000 })
  await lightbox.openRail()
  // The picture itself, not the previous one or a placeholder.
  await expect(lightbox.mainImage).toHaveAttribute('src', new RegExp(`/pictures/${id}\\.`))
  await expect.poll(() => lightbox.mainImageSize().then(([w]) => w)).toBeGreaterThan(0)
}

function rotateResponse(page) {
  return page.waitForResponse(
    (res) => res.url().includes('/pictures/rotate') && res.request().method() === 'POST',
  )
}

/**
 * A picture of the spec's own to turn: a near-full crop of `sourceId`, out of
 * any stack. Portrait, like its source.
 */
async function makeCopy(apiContext, sourceId) {
  const res = await apiContext.post(`${API}/pictures/plugins/crop`, {
    data: {
      picture_ids: [sourceId],
      parameters: { left: 0.01, top: 0.01, width: 0.98, height: 0.98 },
      stack: false,
    },
  })
  expect(res.ok(), await res.text()).toBe(true)
  const ids = (await res.json()).created_picture_ids ?? []
  expect(ids, 'the copy should be a new picture').toHaveLength(1)
  return ids[0]
}

/**
 * Delete forever what a test made, and only that: moved to the Scrapheap, then
 * purged by id, after the preview confirms it names nothing else.
 */
async function purgePictures(apiContext, ids) {
  if (!ids.length) return
  for (const id of ids) {
    const res = await apiContext.delete(`${API}/pictures/${id}`)
    expect(res.ok(), await res.text()).toBe(true)
  }
  const preview = await apiContext.post(`${API}/pictures/scrapheap/delete-preview`, {
    data: { picture_ids: ids },
  })
  expect(preview.ok(), await preview.text()).toBe(true)
  const { total_count: total, confirm_token: confirmToken } = await preview.json()
  expect(total, 'the purge must name only what this test made').toBe(ids.length)
  const purged = await apiContext.delete(`${API}/pictures/scrapheap`, {
    data: { picture_ids: ids, include_protected: false, confirm_token: confirmToken },
  })
  expect(purged.ok(), await purged.text()).toBe(true)
}

async function expectOrientation(apiContext, id, expected) {
  await expect
    .poll(async () => (await metadata(apiContext, id)).orientation)
    .toBe(expected)
}

/** Landscape once turned a quarter, portrait upright (the fixture's are portrait). */
async function expectLandscape(lightbox, landscape) {
  await expect
    .poll(async () => {
      const [w, h] = await lightbox.mainImageSize()
      return w > h
    })
    .toBe(landscape)
}

test.describe('lightbox editing', () => {
  test.describe('rotate', () => {
    let pictureId = null

    test.afterEach(async ({ apiContext }) => {
      if (pictureId != null) await purgePictures(apiContext, [pictureId])
      pictureId = null
    })

    test('] turns the picture and its grid thumbnail, survives a reload, and Ctrl+Z turns it back', async ({
      page,
      grid,
      lightbox,
      apiContext,
    }) => {
      await grid.goto()
      pictureId = await makeCopy(apiContext, (await editablePictures(page, apiContext))[0])
      await openPicture(page, lightbox, pictureId)
      const [w0, h0] = await lightbox.mainImageSize()
      expect(w0, 'the fixture picture starts portrait').toBeLessThan(h0)

      const turned = rotateResponse(page)
      await page.keyboard.press(']')
      expect((await turned).ok()).toBe(true)
      // EXIF 6 is a quarter turn clockwise.
      await expectOrientation(apiContext, pictureId, 6)
      await expectLandscape(lightbox, true)

      // The turn is the file's, not the screen's.
      await page.reload()
      await expect(lightbox.root).toBeVisible({ timeout: 15_000 })
      await expectLandscape(lightbox, true)

      // The grid card behind it turned too: its thumbnail is now landscape.
      await lightbox.close()
      const thumb = grid.card(pictureId).locator('img').first()
      await expect
        .poll(() =>
          thumb.evaluate((img) => img.complete && img.naturalWidth > img.naturalHeight),
        )
        .toBe(true)

      // Undo, from the lightbox's own keyboard.
      await lightbox.openFromGrid(grid.card(pictureId))
      await page.keyboard.press('Control+z')
      await expectOrientation(apiContext, pictureId, 1)
      await expectLandscape(lightbox, false)
    })

    test('the Edit tab rotates left and right', async ({ page, grid, lightbox, apiContext }) => {
      await grid.goto()
      pictureId = await makeCopy(apiContext, (await editablePictures(page, apiContext))[1])
      await openPicture(page, lightbox, pictureId)
      await lightbox.openEditTab()

      let turned = rotateResponse(page)
      await lightbox.rotateLeftButton.click()
      expect((await turned).ok()).toBe(true)
      // EXIF 8 is a quarter turn counter-clockwise.
      await expectOrientation(apiContext, pictureId, 8)
      await expectLandscape(lightbox, true)

      turned = rotateResponse(page)
      await lightbox.rotateRightButton.click()
      expect((await turned).ok()).toBe(true)
      await expectOrientation(apiContext, pictureId, 1)
      await expectLandscape(lightbox, false)
    })
  })

  test.describe('crop', () => {
    let sourceId = null
    const created = []

    // What a crop made goes (`purgePictures`). Then the stack the crop opened
    // is taken apart, as the source was in none before.
    test.afterEach(async ({ apiContext }) => {
      try {
        await purgePictures(apiContext, created)
      } finally {
        await unstackSource(apiContext)
        created.length = 0
        sourceId = null
      }
    })

    async function unstackSource(apiContext) {
      if (sourceId != null) {
        const { stack_id: stackId } = await metadata(apiContext, sourceId)
        if (stackId != null) {
          const res = await apiContext.delete(`${API}/stacks/${stackId}/members`, {
            data: { picture_ids: [sourceId] },
          })
          expect(res.ok(), await res.text()).toBe(true)
        }
      }
    }

    test('each framing crops to its size as a new member of the stack, and the original is untouched', async ({
      page,
      grid,
      lightbox,
      apiContext,
    }) => {
      await grid.goto()
      sourceId = (await editablePictures(page, apiContext))[2]
      // Square on face sizes itself to the largest stored face.
      const rows = await faces(apiContext, sourceId)
      expect(rows.length, 'the source picture needs a stored face').toBeGreaterThan(0)
      const area = ({ bbox: [a, b, c, d] }) => (c - a) * (d - b)
      const face = rows.reduce((best, row) => (area(row) > area(best) ? row : best))
      const before = await metadata(apiContext, sourceId)
      const W = before.width
      const H = before.height

      // The sizes the framings promise, restated from their definition
      // (utils/cropFramings.js) against the stored face and the picture, and
      // read back from the server's file. One pixel of slack for rounding.
      const [x1, y1, x2, y2] = face.bbox
      const largest = (aspect) => (W / H > aspect ? [H * aspect, H] : [W, W / aspect])
      const buckets = [
        [1024, 1024], [1152, 896], [896, 1152], [1216, 832], [832, 1216],
        [1344, 768], [768, 1344], [1536, 640], [640, 1536],
      ]
      const [bw, bh] = buckets.reduce((best, b) =>
        Math.abs(Math.log(b[0] / b[1] / (W / H))) < Math.abs(Math.log(best[0] / best[1] / (W / H)))
          ? b
          : best,
      )
      const side = Math.min(Math.max(x2 - x1, y2 - y1) * 2.5, W, H)
      const framings = [
        { label: 'Square on face', size: [side, side] },
        { label: '3:4 on face', size: largest(3 / 4) },
        { label: `Training bucket ${bw}×${bh}`, size: largest(bw / bh) },
      ]

      for (const { label, size } of framings) {
        await openPicture(page, lightbox, sourceId)
        await lightbox.openEditTab()
        await lightbox.cropButton.click()
        await lightbox.cropFraming(label).click()
        const ran = page.waitForResponse(
          (res) => res.url().includes('/pictures/plugins/crop') && res.request().method() === 'POST',
        )
        await lightbox.applyCropButton.click()
        const res = await ran
        expect(res.ok(), await res.text()).toBe(true)
        const ids = (await res.json()).created_picture_ids ?? []
        expect(ids, `${label} should make one new picture`).toHaveLength(1)
        created.push(ids[0])

        const made = await metadata(apiContext, ids[0])
        expect(Math.abs(made.width - size[0]), `${label} width ${made.width}`).toBeLessThanOrEqual(1)
        expect(Math.abs(made.height - size[1]), `${label} height ${made.height}`).toBeLessThanOrEqual(1)
        // The lightbox steps onto what the crop made.
        await expect(lightbox.mainImage).toHaveAttribute('src', new RegExp(`/pictures/${ids[0]}\\.`))
      }

      const stack = await (await apiContext.get(`${API}/pictures/${sourceId}/stack`)).json()
      expect([...stack.picture_ids].sort()).toEqual([sourceId, ...created].sort())

      const after = await metadata(apiContext, sourceId)
      for (const key of ['width', 'height', 'size_bytes', 'pixel_sha', 'orientation']) {
        expect(after[key], `the original's ${key}`).toEqual(before[key])
      }
    })
  })

  test.describe('description', () => {
    let pictureId = null
    let original = null

    test.afterEach(async ({ apiContext }) => {
      if (pictureId != null) {
        const res = await apiContext.patch(`${API}/pictures/${pictureId}`, {
          data: { description: original },
        })
        expect(res.ok(), await res.text()).toBe(true)
      }
      pictureId = null
      original = null
    })

    test('an edit survives a reload, and the reset clears it for the captioner', async ({
      page,
      grid,
      lightbox,
      apiContext,
    }) => {
      await grid.goto()
      ;[, , , pictureId] = await editablePictures(page, apiContext)
      original = (await metadata(apiContext, pictureId)).description ?? null
      expect(original, 'the seeded picture has a generated description').toBeTruthy()
      await openPicture(page, lightbox, pictureId)
      await expect(lightbox.descriptionField).toHaveValue(original)

      const edited = `e2e edited description ${Date.now()}`
      await lightbox.descriptionField.click()
      await lightbox.descriptionField.fill(edited)
      const saved = page.waitForResponse(
        (res) =>
          res.url().endsWith(`/pictures/${pictureId}`) && res.request().method() === 'PATCH',
      )
      await lightbox.descriptionField.press('Enter')
      expect((await saved).ok()).toBe(true)
      expect((await metadata(apiContext, pictureId)).description).toBe(edited)

      await page.reload()
      await expect(lightbox.root).toBeVisible({ timeout: 15_000 })
      await lightbox.openRail()
      await expect(lightbox.descriptionField).toHaveValue(edited)

      await lightbox.resetDescriptionButton.click()
      await expect.poll(async () => (await metadata(apiContext, pictureId)).description).toBeNull()
      await expect(lightbox.descriptionField).not.toHaveValue(edited)
    })
  })

  test.describe('faces', () => {
    let characterId = null
    const assigned = [] // [characterId, faceId] pairs this test made

    test.afterEach(async ({ apiContext }) => {
      for (const [cid, faceId] of assigned) {
        const res = await apiContext.delete(`${API}/characters/${cid}/faces`, {
          data: { face_ids: [faceId] },
        })
        expect(res.ok(), await res.text()).toBe(true)
      }
      if (characterId != null) {
        const res = await apiContext.delete(`${API}/characters/${characterId}`)
        expect(res.ok(), await res.text()).toBe(true)
      }
      assigned.length = 0
      characterId = null
    })

    async function openPicker(page, lightbox, id) {
      await openPicture(page, lightbox, id)
      await lightbox.facesPencil.click()
      await expect(lightbox.facesPanel).toBeVisible()
      await lightbox.facesPanel
        .getByRole('button', { name: 'Assign this face to a person' })
        .first()
        .click()
      const search = page.getByRole('textbox', { name: 'Search people' })
      await expect(search).toBeVisible()
      return search
    }

    test('a new person made from a face exists, owns the face, and is in the sidebar', async ({
      page,
      grid,
      lightbox,
      sidebar,
      apiContext,
    }) => {
      await grid.goto()
      const [target] = await withOneUnnamedFace(apiContext, await editablePictures(page, apiContext))
      expect(target, 'the grid should show a picture with an unnamed face').toBeTruthy()

      const name = `E2E Person ${Date.now()}`
      const search = await openPicker(page, lightbox, target.id)
      await search.fill(name)
      await page.getByRole('button', { name: `Create "${name}"…` }).click()

      const dialog = page.getByRole('dialog', { name: 'New person' })
      await expect(dialog).toBeVisible()
      const createdRes = page.waitForResponse(
        (res) => /\/characters$/.test(new URL(res.url()).pathname) && res.request().method() === 'POST',
      )
      const assignedRes = page.waitForResponse(
        (res) => /\/characters\/\d+\/faces$/.test(res.url()) && res.request().method() === 'POST',
      )
      await dialog.getByRole('button', { name: 'Save' }).click()
      const createdBody = await (await createdRes).json()
      characterId = createdBody.id ?? createdBody.character?.id
      expect(characterId, JSON.stringify(createdBody)).toBeTruthy()
      expect((await assignedRes).ok()).toBe(true)
      assigned.push([characterId, target.face.id])

      const character = await (await apiContext.get(`${API}/characters/${characterId}`)).json()
      expect(character.name).toBe(name)
      await expect
        .poll(async () => (await faces(apiContext, target.id))[0].character_id)
        .toBe(characterId)
      await expect(sidebar.characterItems.filter({ hasText: name })).toHaveCount(1)
    })

    test('a face can be given to an existing person', async ({ page, grid, lightbox, apiContext }) => {
      await grid.goto()
      const unnamed = await withOneUnnamedFace(apiContext, await editablePictures(page, apiContext))
      // Another picture than the create test's, so a cleanup that failed there
      // cannot hand this test a face that already has a person.
      const target = unnamed[1] ?? unnamed[0]
      expect(target, 'the grid should show a picture with an unnamed face').toBeTruthy()
      const listed = await (await apiContext.get(`${API}/characters`)).json()
      const person = (Array.isArray(listed) ? listed : listed.characters ?? []).find(
        (c) => c.name && !String(c.name).startsWith('E2E Person'),
      )
      expect(person, 'the fixture should have a named person').toBeTruthy()

      const search = await openPicker(page, lightbox, target.id)
      await search.fill(person.name)
      const assignedRes = page.waitForResponse(
        (res) =>
          res.url().includes(`/characters/${person.id}/faces`) && res.request().method() === 'POST',
      )
      await page.getByRole('option', { name: person.name }).first().click()
      expect((await assignedRes).ok()).toBe(true)
      assigned.push([person.id, target.face.id])

      await expect
        .poll(async () => (await faces(apiContext, target.id))[0].character_id)
        .toBe(person.id)
    })
  })

  test.describe('edit keys while typing', () => {
    let pictureId = null

    test.afterEach(async ({ apiContext }) => {
      if (pictureId != null) await purgePictures(apiContext, [pictureId])
      pictureId = null
    })

    test('[ and ] typed into the tag field or the description never rotate', async ({
      page,
      grid,
      lightbox,
      apiContext,
    }) => {
      await grid.goto()
      // A copy, because the control at the end really turns it.
      pictureId = await makeCopy(apiContext, (await editablePictures(page, apiContext))[4])
      const described = await apiContext.patch(`${API}/pictures/${pictureId}`, {
        data: { description: 'A copy made by the typing-guard test.' },
      })
      expect(described.ok(), await described.text()).toBe(true)
      const before = await metadata(apiContext, pictureId)
      await openPicture(page, lightbox, pictureId)

      const rotates = []
      page.on('request', (req) => {
        if (req.url().includes('/pictures/rotate')) rotates.push(req.url())
      })

      await lightbox.tagInput.click()
      await page.keyboard.type('a[b]c')
      await expect(lightbox.tagInput).toHaveValue('a[b]c')
      await lightbox.tagInput.fill('')
      await page.keyboard.press('Escape')

      await lightbox.descriptionField.click()
      await page.keyboard.press('Control+End')
      await page.keyboard.type(' [x]')
      await expect(lightbox.descriptionField).toHaveValue(`${before.description} [x]`)
      // Escape drops the edit, so the description is as it was.
      await page.keyboard.press('Escape')
      await expect(lightbox.descriptionField).toHaveValue(before.description)

      // Give a stray request the time to leave, then read the server.
      await page.waitForTimeout(500)
      expect(rotates, 'no rotate request while typing').toEqual([])
      const after = await metadata(apiContext, pictureId)
      expect(after.orientation).toEqual(before.orientation)
      expect(after.description).toEqual(before.description)

      // The control: the same key on the picture does rotate, so the silence
      // above is the field's doing and not a dead shortcut.
      const turned = rotateResponse(page)
      await page.keyboard.press(']')
      expect((await turned).ok()).toBe(true)
      await expectOrientation(apiContext, pictureId, 6)
    })
  })
})
