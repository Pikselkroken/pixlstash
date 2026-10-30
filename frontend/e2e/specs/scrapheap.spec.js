import { test, expect } from '../fixtures/test.js'

// Deleting pictures and the Scrapheap: the Media view's data-loss paths.
//
// Only reversible steps are completed. A soft delete (Delete key, Selection
// menu, context menu, lightbox) only moves a picture to the Scrapheap, and every
// test puts back what it moved there. The two irreversible actions, Empty
// scrapheap and Delete forever, are driven up to their confirm and no further: the
// confirm's count is checked, Cancel is pressed, and the purge request is
// blocked at the network so a broken Cancel fails the test instead of
// destroying the shared fixture. Confirming either one needs pictures the spec
// imported itself; the e2e backend provides them since #2320, and #2353 tracks
// running the confirms for real.

const API = '/api/v1'

test.describe('deleting pictures and the Scrapheap', () => {
  const scrapheapIds = async (apiContext) => {
    const listed = await apiContext.get(`${API}/pictures`, {
      params: { only_deleted: 'true', fields: 'grid' },
    })
    expect(listed.ok(), await listed.text()).toBe(true)
    return (await listed.json()).map((picture) => picture.id)
  }

  const libraryPictures = async (apiContext, params = {}) => {
    const listed = await apiContext.get(`${API}/pictures`, {
      params: { fields: 'grid', ...params },
    })
    expect(listed.ok(), await listed.text()).toBe(true)
    return listed.json()
  }

  const softDelete = async (apiContext, ids) => {
    const deleted = await apiContext.delete(`${API}/pictures`, {
      data: { picture_ids: ids },
    })
    expect(deleted.ok(), await deleted.text()).toBe(true)
  }

  let scrapheapBefore = null

  test.beforeEach(async ({ apiContext }) => {
    scrapheapBefore = await scrapheapIds(apiContext)
  })

  // No set stays locked (as in set-locking.spec.js, any locked set is
  // unlocked: the fixture has none), and what a test sent to the Scrapheap goes
  // back: every later spec counts on the fixture. Only the pictures THIS test
  // put there, since an earlier spec may have left pictures of its own.
  test.afterEach(async ({ apiContext }) => {
    const sets = await (await apiContext.get(`${API}/picture_sets`)).json()
    for (const set of sets) {
      if (set.locked) {
        const unlocked = await apiContext.patch(`${API}/picture_sets/${set.id}`, {
          data: { locked: false },
        })
        expect(unlocked.ok(), await unlocked.text()).toBe(true)
      }
    }
    if (!scrapheapBefore) return
    const mine = (await scrapheapIds(apiContext)).filter(
      (id) => !scrapheapBefore.includes(id),
    )
    scrapheapBefore = null
    if (!mine.length) return
    const restored = await apiContext.post(`${API}/pictures/scrapheap/restore`, {
      data: { picture_ids: mine },
    })
    expect(restored.ok(), await restored.text()).toBe(true)
  })

  /**
   * Ids of rendered cards that are not in a stack. A collapsed stack deletes
   * whole, so a stacked card would send more to the Scrapheap than it names.
   */
  const renderedUnstackedIds = async (grid, apiContext, count) => {
    await grid.waitForLoaded()
    const srcs = await grid.thumbnailImages.evaluateAll((imgs) =>
      imgs.map((img) => img.getAttribute('src')),
    )
    const rendered = srcs
      .map((src) => src?.match(/\/thumbnails\/(\d+)\./)?.[1])
      .filter(Boolean)
      .map(Number)
    const unstacked = new Set(
      (await libraryPictures(apiContext))
        .filter((picture) => !picture.stack_id)
        .map((picture) => picture.id),
    )
    const ids = [...new Set(rendered)].filter((id) => unstacked.has(id))
    expect(ids.length, 'not enough unstacked cards rendered').toBeGreaterThanOrEqual(
      count,
    )
    return ids.slice(0, count)
  }

  /** The first `count` unstacked library pictures, set up through the API. */
  const unstackedIds = async (apiContext, count) => {
    const ids = (await libraryPictures(apiContext))
      .filter((picture) => !picture.stack_id)
      .slice(0, count)
      .map((picture) => picture.id)
    expect(ids.length, 'not enough unstacked pictures').toBe(count)
    return ids
  }

  // A deleted card stays on screen, greyed, while its undo is one click away.
  // What has to go is the live card.
  const liveCard = (page, id) =>
    page.locator(`.image-card:not(.image-card--ghost):has(img[src*="/thumbnails/${id}."])`)

  const bulkDeleteAnswered = (page) =>
    page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `${API}/pictures` &&
        response.request().method() === 'DELETE',
    )

  /** The cards gone from the grid, listed by the Scrapheap, in the API and on screen. */
  const expectInScrapheap = async (page, grid, apiContext, ids) => {
    for (const id of ids) await expect(liveCard(page, id)).toHaveCount(0)
    const heap = await scrapheapIds(apiContext)
    for (const id of ids) expect(heap).toContain(id)
    await page.goto('/scrapheap')
    await grid.waitForLoaded()
    for (const id of ids) await expect(grid.card(id)).toBeVisible()
  }

  /** Block the purge so a confirm that slips through destroys nothing. */
  const blockPurges = async (page) => {
    const purges = []
    await page.route(
      (url) => url.pathname === `${API}/pictures/scrapheap`,
      (route) => {
        if (route.request().method() !== 'DELETE') return route.continue()
        purges.push(route.request().postData())
        return route.abort()
      },
    )
    return purges
  }

  const deleteForeverDialog = (page) =>
    page.getByRole('alertdialog', { name: 'Delete forever?' })

  test.describe('delete from the grid', () => {
    test('the Delete key sends the selected picture to the Scrapheap', async ({
      page,
      grid,
      apiContext,
    }) => {
      await grid.goto()
      const [id] = await renderedUnstackedIds(grid, apiContext, 1)
      await grid.card(id).locator('.thumbnail-card').click()
      await expect(grid.selectionCountLabel).toHaveText('1 selected')

      const answered = bulkDeleteAnswered(page)
      await page.keyboard.press('Delete')
      expect((await answered).ok()).toBe(true)

      await expectInScrapheap(page, grid, apiContext, [id])
    })

    test("the Selection menu's Delete sends the selection to the Scrapheap", async ({
      page,
      grid,
      apiContext,
    }) => {
      await grid.goto()
      const ids = await renderedUnstackedIds(grid, apiContext, 2)
      for (const id of ids) {
        await grid.card(id).locator('.thumbnail-card').click({ modifiers: ['ControlOrMeta'] })
      }
      await expect(grid.selectionCountLabel).toHaveText('2 selected')

      await grid.openSelectionMenu()
      const answered = bulkDeleteAnswered(page)
      await grid.selectionMenuPanel.locator('[aria-keyshortcuts="Delete"]').click()
      expect((await answered).ok()).toBe(true)

      await expectInScrapheap(page, grid, apiContext, ids)
    })

    test("the context menu's Delete sends the picture to the Scrapheap", async ({
      page,
      grid,
      apiContext,
    }) => {
      await grid.goto()
      const [id] = await renderedUnstackedIds(grid, apiContext, 1)
      await grid.openContextMenu(grid.card(id))

      const answered = bulkDeleteAnswered(page)
      await grid.contextMenu.locator('[aria-keyshortcuts="Delete"]').click()
      expect((await answered).ok()).toBe(true)

      await expectInScrapheap(page, grid, apiContext, [id])
    })

    test('a picture in a locked set is refused, with a notice saying why', async ({
      page,
      grid,
      apiContext,
    }) => {
      const sets = await (await apiContext.get(`${API}/picture_sets`)).json()
      const set = sets.find((s) => s.picture_count > 0 && !s.reference_character)
      expect(set, 'the fixture has no non-empty picture set').toBeTruthy()
      const locked = await apiContext.patch(`${API}/picture_sets/${set.id}`, {
        data: { locked: true },
      })
      expect(locked.ok(), await locked.text()).toBe(true)

      await page.goto(`/set/${set.id}`)
      const [id] = await renderedUnstackedIds(grid, apiContext, 1)
      const card = grid.card(id)
      await expect(card.locator('.thumbnail-lock-badge')).toBeVisible()

      // The keyboard: nothing leaves, and the notice names the reason.
      await card.locator('.thumbnail-card').click()
      await expect(grid.selectionCountLabel).toHaveText('1 selected')
      await page.keyboard.press('Delete')
      await expect(
        page.locator('.notice-message', {
          hasText: 'All 1 selected picture is in a locked set, so it was kept.',
        }),
      ).toBeVisible()
      await expect(liveCard(page, id)).toBeVisible()
      expect(await scrapheapIds(apiContext)).not.toContain(id)

      // The context menu does not offer the delete at all.
      await grid.openContextMenu(card)
      await expect(grid.contextMenu.locator('[aria-keyshortcuts="Delete"]')).toBeDisabled()
      await grid.closeContextMenu()
      expect(await scrapheapIds(apiContext)).not.toContain(id)
    })
  })

  test('the lightbox deletes its picture, and Restore in the Scrapheap brings it back', async ({
    page,
    grid,
    lightbox,
    apiContext,
  }) => {
    await grid.goto()
    const [id] = await renderedUnstackedIds(grid, apiContext, 1)
    await lightbox.openFromGrid(grid.card(id).locator('.thumbnail-card'))

    await page.locator('.lightbox-canvas').click({ button: 'right' })
    const answered = bulkDeleteAnswered(page)
    await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
    expect((await answered).ok()).toBe(true)
    // The lightbox never goes on showing a picture that has left the view.
    await expect(lightbox.root).toBeHidden()
    await expectInScrapheap(page, grid, apiContext, [id])

    await lightbox.openFromGrid(grid.card(id).locator('.thumbnail-card'))
    await page.locator('.lightbox-canvas').click({ button: 'right' })
    const restored = page.waitForResponse(
      (response) =>
        response.url().endsWith('/pictures/scrapheap/restore') &&
        response.request().method() === 'POST',
    )
    await page.getByRole('menuitem', { name: 'Restore', exact: true }).click()
    expect((await restored).ok()).toBe(true)
    await expect(lightbox.root).toBeHidden()

    expect(await scrapheapIds(apiContext)).not.toContain(id)
    expect((await libraryPictures(apiContext)).map((p) => p.id)).toContain(id)
  })

  test('Restore all returns every picture with its sets, character and tags', async ({
    page,
    grid,
    apiContext,
  }) => {
    // A picture that is in a set, has a character and carries tags, so there is
    // something to lose; and a plain one beside it.
    const unstacked = new Set(
      (await libraryPictures(apiContext)).filter((p) => !p.stack_id).map((p) => p.id),
    )
    const sets = (await (await apiContext.get(`${API}/picture_sets`)).json()).filter(
      (s) => s.picture_count > 0 && !s.reference_character,
    )
    const characters = await (await apiContext.get(`${API}/characters`)).json()
    const membersOf = async (params) =>
      (await libraryPictures(apiContext, params)).map((p) => p.id)

    let rich = null
    for (const character of characters) {
      const ofCharacter = (await membersOf({ character_id: character.id })).filter((id) =>
        unstacked.has(id),
      )
      for (const set of sets) {
        const inSet = await membersOf({ set_id: set.id })
        const id = ofCharacter.find((pid) => inSet.includes(pid))
        if (id) {
          rich = { id, setId: set.id, characterId: character.id }
          break
        }
      }
      if (rich) break
    }
    expect(rich, 'the fixture has no unstacked picture in both a set and a character').toBeTruthy()
    const tagsOf = async (id) => {
      const res = await apiContext.get(`${API}/pictures/${id}/tags`)
      expect(res.ok(), await res.text()).toBe(true)
      return (await res.json()).tags.map((t) => t.tag).sort()
    }
    const tagsBefore = await tagsOf(rich.id)
    expect(tagsBefore.length).toBeGreaterThan(0)
    const plain = [...unstacked].find((id) => id !== rich.id)
    expect(plain, 'the fixture has no second unstacked picture').toBeTruthy()
    const mine = [rich.id, plain]

    await softDelete(apiContext, mine)
    expect(await membersOf({ set_id: rich.setId })).not.toContain(rich.id)

    await page.goto('/scrapheap')
    await grid.waitForLoaded()
    // Restore all restores the whole heap, so it must hold only this test's.
    expect((await scrapheapIds(apiContext)).sort()).toEqual([...mine].sort())

    page.once('dialog', (dialog) => dialog.accept())
    const restored = page.waitForResponse(
      (response) =>
        response.url().endsWith('/pictures/scrapheap/restore') &&
        response.request().method() === 'POST',
    )
    await page.getByRole('button', { name: 'Restore all' }).click()
    expect((await restored).ok()).toBe(true)
    await expect(grid.thumbnails).toHaveCount(0)

    expect(await scrapheapIds(apiContext)).toEqual([])
    const library = (await libraryPictures(apiContext)).map((p) => p.id)
    for (const id of mine) expect(library).toContain(id)
    expect(await membersOf({ set_id: rich.setId })).toContain(rich.id)
    expect(await membersOf({ character_id: rich.characterId })).toContain(rich.id)
    expect(await tagsOf(rich.id)).toEqual(tagsBefore)
  })

  test('Empty scrapheap states the count, and Cancel deletes nothing', async ({
    page,
    grid,
    apiContext,
  }) => {
    const ids = await unstackedIds(apiContext, 2)
    await softDelete(apiContext, ids)
    const heap = await scrapheapIds(apiContext)
    const purges = await blockPurges(page)

    await page.goto('/scrapheap')
    await grid.waitForLoaded()
    await page.getByRole('button', { name: 'Empty scrapheap' }).click()

    const dialog = deleteForeverDialog(page)
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText(
      `This permanently deletes ${heap.length} pictures, including their files on disk.`,
    )
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toBeHidden()

    expect(purges).toEqual([])
    expect((await scrapheapIds(apiContext)).sort()).toEqual([...heap].sort())
    for (const id of ids) await expect(grid.card(id)).toBeVisible()
  })

  test('Delete forever on a selection counts only the selection, and Cancel deletes nothing', async ({
    page,
    grid,
    apiContext,
  }) => {
    const ids = await unstackedIds(apiContext, 2)
    await softDelete(apiContext, ids)
    const purges = await blockPurges(page)

    await page.goto('/scrapheap')
    await grid.waitForLoaded()
    await grid.card(ids[0]).locator('.thumbnail-card').click()
    await expect(grid.selectionCountLabel).toHaveText('1 selected')
    await grid.openSelectionMenu()
    await grid.selectionMenuPanel.locator('[aria-keyshortcuts="Delete"]').click()

    const dialog = deleteForeverDialog(page)
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText(
      'This permanently deletes 1 picture, including its file on disk.',
    )
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toBeHidden()

    expect(purges).toEqual([])
    const heap = await scrapheapIds(apiContext)
    for (const id of ids) expect(heap).toContain(id)
  })

  test('a cold load of /scrapheap renders the Scrapheap, not the library', async ({
    page,
    grid,
    apiContext,
  }) => {
    const [id] = await unstackedIds(apiContext, 1)
    await softDelete(apiContext, [id])
    const heap = await scrapheapIds(apiContext)

    await page.goto('/scrapheap')
    await grid.waitForLoaded()
    await expect(page.getByRole('button', { name: 'Empty scrapheap' })).toBeVisible()
    await expect(grid.card(id)).toBeVisible()
    // Every card rendered is a Scrapheap picture; none is from the library.
    const srcs = await grid.thumbnailImages.evaluateAll((imgs) =>
      imgs.map((img) => img.getAttribute('src')),
    )
    const rendered = srcs
      .map((src) => src?.match(/\/thumbnails\/(\d+)\./)?.[1])
      .filter(Boolean)
      .map(Number)
    expect(rendered.length, 'no thumbnail cards rendered').toBeGreaterThan(0)
    for (const renderedId of rendered) expect(heap).toContain(renderedId)
  })
})
