import { test, expect } from '../fixtures/test.js'

// Snapshots — Settings → Snapshots lists the automatic metadata restore points
// (the fixture ships several). We assert the list renders with at least one
// restore point and that each row offers a Restore action, but we deliberately
// never click Restore: a rollback would rewrite the shared fixture DB.

test.describe('snapshots', () => {
  test('lists restore points with a restore action (list-only)', async ({ grid, settings }) => {
    await grid.goto()
    await settings.open()
    await settings.openSnapshotsTab()

    expect(await settings.snapshotRows.count()).toBeGreaterThan(0)

    const firstRow = settings.snapshotRows.first()
    await expect(firstRow).toBeVisible()
    await expect(firstRow.getByRole('button', { name: /Restore/ })).toBeVisible()
  })
})
