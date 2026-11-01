import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { crc32, deflateSync } from 'node:zlib'
import { test, expect } from '../fixtures/test.js'

const __dirname = dirname(fileURLToPath(import.meta.url))

// Release plan §2 — Picture Import, for real: files go through the Import
// panel's file input or a real `drop` on the grid, stream into a staging
// session, commit to a background PictureImportTask, and land in the library.
// The e2e backend runs submitted tasks (#2320) but loads no models, so the
// commit completes here while tagging, embeddings and scores still never fire.
//
// Every picture is generated per test (seeded noise PNGs, a canvas-recorded
// webm), so nothing collides with the fixture or another test. Each test
// purges what it imported, soft delete and then the Scrapheap, and deletes the
// sets, characters and projects it made: the Scrapheap specs (#2308) find the
// Scrapheap as the fixture left it.

const SOURCE_JPG = resolve(
  __dirname,
  '../../../test-data/images/041514da-7779-4544-8e9b-e1dbe19540a4.jpg',
)

/**
 * A small RGB PNG of seeded noise. Different seeds give different pixels, and
 * the importer de-duplicates by pixel hash, so the seed is the identity.
 */
function noisePng(seed, size = 48) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
    const out = Buffer.alloc(body.length + 8)
    out.writeUInt32BE(data.length, 0)
    body.copy(out, 4)
    out.writeUInt32BE(crc32(body), body.length + 4)
    return out
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(size, 0)
  header.writeUInt32BE(size, 4)
  header[8] = 8 // bit depth
  header[9] = 2 // truecolour
  const stride = size * 3 + 1
  const raw = Buffer.alloc(stride * size)
  let state = (seed * 2654435761) >>> 0
  for (let i = 0; i < raw.length; i++) {
    if (i % stride === 0) continue // filter byte: none
    state = (Math.imul(state, 1103515245) + 12345) >>> 0
    raw[i] = state >>> 24
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

/** A file payload for `chooseFiles` and `dropFiles`. */
function pngFile(name, seed) {
  return { name, mimeType: 'image/png', buffer: noisePng(seed) }
}

/**
 * Dispatch a real dragenter/dragover/drop carrying `files` onto `target`.
 * The DataTransfer is built in the page, so the app's own
 * `extractSupportedImportFilesFromDataTransfer` reads it as a browser would.
 */
async function dropFiles(page, target, files) {
  files.forEach((f) => made.names.add(f.name))
  const payload = files.map((f) => ({
    name: f.name,
    type: f.mimeType,
    b64: f.buffer.toString('base64'),
  }))
  const dataTransfer = await page.evaluateHandle((items) => {
    const dt = new DataTransfer()
    for (const item of items) {
      const bytes = Uint8Array.from(atob(item.b64), (c) => c.charCodeAt(0))
      dt.items.add(new File([bytes], item.name, { type: item.type }))
    }
    return dt
  }, payload)
  await target.dispatchEvent('dragenter', { dataTransfer })
  await target.dispatchEvent('dragover', { dataTransfer })
  await target.dispatchEvent('drop', { dataTransfer })
}

// Request predicates; a response is matched through `r.request()`.
/** Pick `files` in the open Import panel's file input. */
async function chooseFiles(page, files) {
  files.forEach((f) => made.names.add(f.name))
  await page.locator('.local-import-input').setInputFiles(files)
}

const opensSession = (r) =>
  r.method() === 'POST' && /\/pictures\/import\/staging$/.test(r.url())
const stagesFiles = (r) =>
  r.method() === 'POST' && /\/pictures\/import\/staging\/[^/]+\/files$/.test(r.url())

/** Resolve with the terminal status body of the next import the page runs. */
function importCompleted(page) {
  return page
    .waitForResponse(
      async (r) => {
        // The path: the poll carries `?outcomes_from=` (#2512).
        const path = new URL(r.url()).pathname
        if (!/\/pictures\/import\/staging\/[^/]+\/status$/.test(path) || !r.ok())
          return false
        const body = await r.json().catch(() => null)
        return body?.stage === 'completed'
      },
      { timeout: 30_000 },
    )
    .then((r) => r.json())
}

async function pictureIds(apiContext) {
  const res = await apiContext.get('/api/v1/pictures', {
    params: { fields: 'grid' },
  })
  expect(res.ok(), await res.text()).toBe(true)
  return new Set((await res.json()).map((p) => p.id))
}

async function pictureCount(apiContext) {
  const res = await apiContext.get('/api/v1/pictures/count')
  expect(res.ok(), await res.text()).toBe(true)
  return (await res.json()).count
}

async function metadata(apiContext, id) {
  const res = await apiContext.get(`/api/v1/pictures/${id}/metadata`)
  expect(res.ok(), await res.text()).toBe(true)
  return res.json()
}

/** The grid card showing picture `id`, by its thumbnail or video poster. */
function cardFor(page, id) {
  const thumb = `/thumbnails/${id}.`
  return page.locator(
    `.thumbnail-card:has([src*="${thumb}"]), .thumbnail-card:has([poster*="${thumb}"])`,
  )
}

/** The count on the sidebar's All media row, i.e. what `/` holds. */
function allMediaCount(page) {
  return page
    .locator('.sidebar-list-item', {
      has: page.locator('.sidebar-list-label', { hasText: /^All media$/ }),
    })
    .locator('.sidebar-list-count')
}

/**
 * Everything a test makes, so afterEach can undo it whether or not the test
 * got to the end: imported pictures are soft deleted and then purged, and the
 * entities deleted. `names` holds every file name the test handed the app.
 */
const made = {
  names: new Set(),
  pictures: new Set(),
  sets: [],
  characters: [],
  projects: [],
}

/**
 * Pictures that appeared since `before` from a file this test handed the app,
 * recorded for clean-up. Matching on the original file name keeps anything
 * else that lands meanwhile (the e2e backend's folder sweeps run) out of both
 * the assertions and the purge.
 */
async function newPictureIds(apiContext, before) {
  const fresh = []
  for (const id of await pictureIds(apiContext)) {
    if (before.has(id)) continue
    if (!made.names.has((await metadata(apiContext, id)).original_file_name)) continue
    fresh.push(id)
    made.pictures.add(id)
  }
  return fresh
}

async function createSet(apiContext, name) {
  const res = await apiContext.post('/api/v1/picture_sets', { data: { name } })
  expect(res.ok(), await res.text()).toBe(true)
  const set = (await res.json()).picture_set
  made.sets.push(set.id)
  return set
}

async function createCharacter(apiContext, name) {
  const res = await apiContext.post('/api/v1/characters', { data: { name } })
  expect(res.ok(), await res.text()).toBe(true)
  const character = (await res.json()).character
  made.characters.push(character.id)
  return character
}

async function createProject(apiContext, name) {
  const res = await apiContext.post('/api/v1/projects', { data: { name } })
  expect(res.ok(), await res.text()).toBe(true)
  const body = await res.json()
  const project = body.project ?? body
  made.projects.push(project.id)
  return project
}

async function purgePictures(apiContext, ids) {
  if (!ids.length) return
  for (const id of ids) {
    const res = await apiContext.delete(`/api/v1/pictures/${id}`)
    expect(res.ok(), `soft delete ${id}: ${await res.text()}`).toBe(true)
  }
  const preview = await apiContext.post('/api/v1/pictures/scrapheap/delete-preview', {
    data: { ids },
  })
  expect(preview.ok(), await preview.text()).toBe(true)
  const { confirm_token } = await preview.json()
  const purge = await apiContext.delete('/api/v1/pictures/scrapheap', {
    data: { picture_ids: ids, confirm_token },
  })
  expect(purge.ok(), await purge.text()).toBe(true)
}

test.describe('picture import', () => {
  test.afterEach(async ({ apiContext }) => {
    // Taken and reset first, so a failing clean-up cannot carry this test's
    // leftovers into the next one's.
    const { pictures, sets, characters, projects } = made
    made.names = new Set()
    made.pictures = new Set()
    made.sets = []
    made.characters = []
    made.projects = []
    try {
      // Unlock first: a locked set would refuse the purge of its members.
      for (const id of sets) {
        const unlock = await apiContext.patch(`/api/v1/picture_sets/${id}`, {
          data: { locked: false },
        })
        expect(unlock.ok(), `unlock set ${id}: ${await unlock.text()}`).toBe(true)
      }
      await purgePictures(apiContext, [...pictures])
    } finally {
      for (const id of sets) await apiContext.delete(`/api/v1/picture_sets/${id}`)
      for (const id of characters) await apiContext.delete(`/api/v1/characters/${id}`)
      for (const id of projects) await apiContext.delete(`/api/v1/projects/${id}`)
    }
  })

  test('dragging files over the grid raises the import overlay; leaving clears it (§2)', async ({
    page,
    grid,
  }) => {
    await grid.goto()
    await grid.waitForThumbnailLoaded()

    const payload = readFileSync(SOURCE_JPG).toString('base64')
    const dataTransfer = await page.evaluateHandle(
      ([b64, name]) => {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
        const dt = new DataTransfer()
        dt.items.add(new File([bytes], name, { type: 'image/jpeg' }))
        return dt
      },
      [payload, 'e2e-import.jpg'],
    )

    // Dragging a file over the grid raises the full-grid drop overlay.
    await grid.scrollWrapper.dispatchEvent('dragenter', { dataTransfer })
    await expect(page.locator('.drag-overlay')).toBeVisible()
    await expect(page.locator('.drag-overlay-message')).toHaveText(
      'Drop files here to import',
    )

    // Dragging back out dismisses it — no stuck overlay over the grid.
    await grid.scrollWrapper.dispatchEvent('dragleave', { dataTransfer })
    await expect(page.locator('.drag-overlay')).toBeHidden()
  })

  test('the Import panel imports one file, then several, and All media counts them (§2)', async ({
    page,
    grid,
    apiContext,
  }) => {
    await grid.goto()
    const startCount = await pictureCount(apiContext)
    await expect(allMediaCount(page)).toHaveText(String(startCount))
    // The import happens in the grid: arriving tiles under one band (#2512).
    const arriving = page.getByTestId('arriving-tiles')

    // ── One file. Hold its upload so the staging phase can be seen. ──
    let releaseUpload
    const uploadHeld = new Promise((r) => (releaseUpload = r))
    await page.route('**/pictures/import/staging/*/files', async (route) => {
      await uploadHeld
      await route.continue()
    })
    let before = await pictureIds(apiContext)
    await page.locator('.sidebar-import-btn').click()
    const done = importCompleted(page)
    await chooseFiles(page, [pngFile('panel-one.png', 101)])

    // Acknowledged before a byte has gone up: the file's own tile, marked.
    await expect(arriving).toContainText('Keep this tab open until the upload finishes')
    await expect(arriving).toContainText('Uploading')
    await expect(arriving.locator('[data-arriving-state="uploading"]')).toHaveCount(1)
    releaseUpload()
    await page.unroute('**/pictures/import/staging/*/files')

    expect(await done).toMatchObject({ imported_count: 1, duplicate_count: 0 })
    // The band becomes the receipt and stays until Done.
    await expect(arriving).toContainText('Added just now')
    await expect(arriving).toContainText('1 picture')
    await arriving.getByRole('button', { name: 'Done' }).click()
    await expect(arriving).toBeHidden()
    const [oneId, ...extra] = await newPictureIds(apiContext, before)
    expect(extra).toEqual([])
    expect((await metadata(apiContext, oneId)).original_file_name).toBe('panel-one.png')
    await expect(cardFor(page, oneId)).toBeVisible()
    await expect(allMediaCount(page)).toHaveText(String(startCount + 1))

    // ── Several files in one pick: one session, every file staged. ──
    before = await pictureIds(apiContext)
    await page.locator('.sidebar-import-btn').click()
    const staged = page.waitForResponse((r) => stagesFiles(r.request()))
    const doneSeveral = importCompleted(page)
    await chooseFiles(page, [
      pngFile('panel-a.png', 102),
      pngFile('panel-b.png', 103),
      pngFile('panel-c.png', 104),
    ])
    expect(await (await staged).json()).toMatchObject({
      staged: 3,
      received: ['panel-a.png', 'panel-b.png', 'panel-c.png'],
      skipped: [],
    })
    expect(await doneSeveral).toMatchObject({ imported_count: 3 })
    const several = await newPictureIds(apiContext, before)
    expect(several).toHaveLength(3)
    for (const id of several) await expect(cardFor(page, id)).toBeVisible()
    await expect(allMediaCount(page)).toHaveText(String(startCount + 4))
    expect(await pictureCount(apiContext)).toBe(startCount + 4)
  })

  test('a real drop on the grid uploads the dropped file and imports it (§2)', async ({
    page,
    grid,
    apiContext,
  }) => {
    await grid.goto()
    const before = await pictureIds(apiContext)

    // The overlay test above proves only the drag affordance. Here the proof is
    // the request the drop handler makes: the dropped file's own bytes, by name.
    const upload = page.waitForRequest(stagesFiles)
    const done = importCompleted(page)
    await dropFiles(page, grid.scrollWrapper, [pngFile('dropped.png', 201)])
    expect((await upload).postDataBuffer().toString('latin1')).toContain(
      'filename="dropped.png"',
    )
    expect(await done).toMatchObject({ imported_count: 1 })

    const [id] = await newPictureIds(apiContext, before)
    expect((await metadata(apiContext, id)).original_file_name).toBe('dropped.png')
    await expect(cardFor(page, id)).toBeVisible()
  })

  test('a drop on /set/:id joins that set (§2)', async ({ page, grid, apiContext }) => {
    const set = await createSet(apiContext, 'e2e import into set')
    await page.goto(`/set/${set.id}`)
    await expect(page.locator('.grid-breadcrumb')).toContainText(set.name)
    const before = await pictureIds(apiContext)

    const opened = page.waitForRequest(opensSession)
    const done = importCompleted(page)
    await dropFiles(page, grid.scrollWrapper, [pngFile('into-set.png', 301)])
    expect((await opened).postDataJSON()).toMatchObject({ set_id: set.id })
    await done

    const [id] = await newPictureIds(apiContext, before)
    const members = await apiContext.get(`/api/v1/picture_sets/${set.id}/members`)
    expect((await members.json()).picture_ids).toEqual([id])
    await expect(cardFor(page, id)).toBeVisible()
  })

  test('a drop on /character/:id is filed for that character (§2)', async ({
    page,
    grid,
    apiContext,
  }) => {
    const character = await createCharacter(apiContext, 'e2e import into character')
    await page.goto(`/character/${character.id}`)
    await expect(page.locator('.grid-breadcrumb')).toContainText(character.name)
    const before = await pictureIds(apiContext)

    const opened = page.waitForRequest(opensSession)
    const done = importCompleted(page)
    await dropFiles(page, grid.scrollWrapper, [pngFile('into-character.png', 401)])
    expect((await opened).postDataJSON()).toMatchObject({ character_id: character.id })
    await done

    // A picture joins a character through its faces. Face extraction does not
    // run in e2e, so the import's association is the pending assignment the
    // extractor consumes - that is the server's record of "for this character".
    const [id] = await newPictureIds(apiContext, before)
    expect((await metadata(apiContext, id)).pending_character_id).toBe(character.id)
  })

  test('the Import panel inside a project imports into that project (§2)', async ({
    page,
    apiContext,
  }) => {
    const project = await createProject(apiContext, 'e2e import into project')
    await page.goto(`/project/${project.id}`)
    await expect(page.getByTestId('sidebar-scope')).toContainText(project.name)
    const before = await pictureIds(apiContext)

    await page.locator('.sidebar-import-btn').click()
    const opened = page.waitForRequest(opensSession)
    const done = importCompleted(page)
    await chooseFiles(page, [pngFile('into-project.png', 501)])
    expect((await opened).postDataJSON()).toMatchObject({ project_id: project.id })
    await done

    const [id] = await newPictureIds(apiContext, before)
    const inProject = await apiContext.get('/api/v1/pictures', {
      params: { project_id: project.id, fields: 'grid' },
    })
    expect((await inProject.json()).map((p) => p.id)).toEqual([id])
    await expect(cardFor(page, id)).toBeVisible()
  })

  test('a locked set refuses the import and says why (§2)', async ({
    page,
    grid,
    apiContext,
  }) => {
    const set = await createSet(apiContext, 'e2e locked import target')
    const locked = await apiContext.patch(`/api/v1/picture_sets/${set.id}`, {
      data: { locked: true },
    })
    expect(locked.ok(), await locked.text()).toBe(true)
    await page.goto(`/set/${set.id}`)
    await expect(page.locator('.grid-breadcrumb')).toContainText(set.name)
    const startCount = await pictureCount(apiContext)

    let uploaded = false
    page.on('request', (r) => {
      if (stagesFiles(r)) uploaded = true
    })
    const refused = page.waitForResponse((r) => opensSession(r.request()))
    await dropFiles(page, grid.scrollWrapper, [pngFile('into-locked.png', 601)])
    expect((await refused).status()).toBe(409)

    const arriving = page.getByTestId('arriving-tiles')
    await expect(arriving).toContainText('Import failed')
    await expect(arriving).toContainText(
      `Picture set '${set.name}' is locked and cannot take new members`,
    )
    expect(uploaded).toBe(false)
    expect(await pictureCount(apiContext)).toBe(startCount)
    const members = await apiContext.get(`/api/v1/picture_sets/${set.id}/members`)
    expect((await members.json()).picture_ids).toEqual([])
    await arriving.getByRole('button', { name: 'Dismiss' }).click()
    await expect(arriving).toBeHidden()
  })

  test('unsupported files are left out, and a drop of nothing else is reported (§2)', async ({
    page,
    grid,
    notices,
    apiContext,
  }) => {
    await grid.goto()
    const before = await pictureIds(apiContext)

    // Beside a picture, the unsupported file never leaves the browser.
    const upload = page.waitForRequest(stagesFiles)
    const done = importCompleted(page)
    await dropFiles(page, grid.scrollWrapper, [
      pngFile('kept.png', 701),
      { name: 'notes.xyz', mimeType: 'application/octet-stream', buffer: Buffer.from('x') },
    ])
    const body = (await upload).postDataBuffer().toString('latin1')
    expect(body).toContain('filename="kept.png"')
    expect(body).not.toContain('notes.xyz')
    expect(await done).toMatchObject({ imported_count: 1 })
    expect(await newPictureIds(apiContext, before)).toHaveLength(1)

    // On its own, it opens no session and the user is told.
    let opened = false
    page.on('request', (r) => {
      if (opensSession(r)) opened = true
    })
    await dropFiles(page, grid.scrollWrapper, [
      { name: 'notes.xyz', mimeType: 'application/octet-stream', buffer: Buffer.from('x') },
    ])
    await expect(notices.messages).toContainText([
      'None of those files are a supported image, video or archive.',
    ])
    expect(opened).toBe(false)
  })

  test('importing the same picture twice keeps one copy (§2)', async ({
    page,
    grid,
    apiContext,
  }) => {
    await grid.goto()
    const before = await pictureIds(apiContext)
    const first = importCompleted(page)
    await dropFiles(page, grid.scrollWrapper, [pngFile('twice.png', 801)])
    expect(await first).toMatchObject({ imported_count: 1, duplicate_count: 0 })
    const [id] = await newPictureIds(apiContext, before)
    const countAfterFirst = await pictureCount(apiContext)

    // Same pixels under another name: the server matches on content.
    const second = importCompleted(page)
    await dropFiles(page, grid.scrollWrapper, [pngFile('twice-again.png', 801)])
    expect(await second).toMatchObject({ imported_count: 0, duplicate_count: 1 })
    expect(await newPictureIds(apiContext, before)).toEqual([id])
    expect(await pictureCount(apiContext)).toBe(countAfterFirst)
    await expect(allMediaCount(page)).toHaveText(String(countAfterFirst))
  })

  test('leaving is guarded until the commit has returned (§2)', async ({ page, grid }) => {
    await grid.goto()
    // Would the browser ask before leaving? (A synthetic event: no dialog.)
    const leaveGuarded = () =>
      page.evaluate(() => {
        const e = new Event('beforeunload', { cancelable: true })
        window.dispatchEvent(e)
        return e.defaultPrevented
      })
    let releaseCommit
    const commitHeld = new Promise((r) => (releaseCommit = r))
    let commitSeen
    const commitStarted = new Promise((r) => (commitSeen = r))
    await page.route('**/pictures/import/staging/*/commit', async (route) => {
      commitSeen()
      await commitHeld
      await route.continue()
    })
    const done = importCompleted(page)
    await dropFiles(page, grid.scrollWrapper, [pngFile('guarded.png', 851)])

    // Mid-commit the staged session has no owner but this tab.
    await commitStarted
    expect(await leaveGuarded()).toBe(true)
    releaseCommit()
    expect(await done).toMatchObject({ imported_count: 1 })
    expect(await leaveGuarded()).toBe(false)
  })

  test('a .txt beside an image tags it (§2)', async ({ page, grid, apiContext }) => {
    await grid.goto()
    const before = await pictureIds(apiContext)
    const done = importCompleted(page)
    await dropFiles(page, grid.scrollWrapper, [
      pngFile('captioned.png', 901),
      {
        name: 'captioned.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from('red lantern, harbour at night'),
      },
    ])
    expect(await done).toMatchObject({ imported_count: 1 })

    const [id] = await newPictureIds(apiContext, before)
    const tags = (await metadata(apiContext, id)).tags.map((t) => t.tag)
    expect(tags).toEqual(expect.arrayContaining(['red lantern', 'harbour at night']))
  })

  test('a video imports and plays in the lightbox (§2)', async ({
    page,
    grid,
    lightbox,
    apiContext,
  }) => {
    await grid.goto()
    // A one-second VP8 clip recorded from a canvas: no binary fixture, and a
    // codec the bundled Chromium can decode.
    const clipB64 = await page.evaluate(async () => {
      const canvas = document.createElement('canvas')
      canvas.width = 64
      canvas.height = 48
      const ctx = canvas.getContext('2d')
      const recorder = new MediaRecorder(canvas.captureStream(15), {
        mimeType: 'video/webm;codecs=vp8',
      })
      const chunks = []
      recorder.ondataavailable = (e) => chunks.push(e.data)
      const stopped = new Promise((r) => (recorder.onstop = r))
      recorder.start()
      let frame = 0
      const paint = setInterval(() => {
        ctx.fillStyle = `hsl(${frame++ * 40}, 80%, 50%)`
        ctx.fillRect(0, 0, 64, 48)
      }, 60)
      await new Promise((r) => setTimeout(r, 1000))
      clearInterval(paint)
      recorder.stop()
      await stopped
      const bytes = new Uint8Array(await new Blob(chunks).arrayBuffer())
      let binary = ''
      for (const b of bytes) binary += String.fromCharCode(b)
      return btoa(binary)
    })

    const before = await pictureIds(apiContext)
    const done = importCompleted(page)
    await dropFiles(page, grid.scrollWrapper, [
      { name: 'clip.webm', mimeType: 'video/webm', buffer: Buffer.from(clipB64, 'base64') },
    ])
    expect(await done).toMatchObject({ imported_count: 1 })
    const [id] = await newPictureIds(apiContext, before)
    expect((await metadata(apiContext, id)).is_video).toBe(true)

    await lightbox.openFromGrid(cardFor(page, id))
    const video = page.locator('.lightbox video.lightbox-video')
    await expect(video).toBeVisible()
    await expect(page.locator('.lightbox-video-error')).toHaveCount(0)
    await video.evaluate((v) => {
      v.muted = true
      return v.play()
    })
    await expect.poll(() => video.evaluate((v) => v.currentTime)).toBeGreaterThan(0)
  })
})
