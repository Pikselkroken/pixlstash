// Release plan §21 — the Duplicates destination, driven by the keyboard.
//
// REQUIRES THE BACKEND BRANCH. The `/dedup` routes ship on
// `feature/dedup-tiers`; this spec lives on the frontend branch and will fail
// with 404s until the two are merged. That is deliberate: the spec is the
// frontend lane's half of the contract and is committed with it rather than
// held back.
//
// The queue is a keyboard surface before it is a pointer one, so every case
// here sends real keypresses at the queue root (the element that actually owns
// the keydown handler) rather than clicking the buttons that duplicate them.
// Clicking would exercise the emits and prove nothing about the model that
// makes the feature worth having.
//
// The fixture is seeded by `e2e/seed_dedup_fixture.py`, which copies fixture
// images to new files, inserts a `picture` row per copy and then runs the real
// `DedupScanTask`. It exists because the e2e backend boots with
// `disable_background_workers: true`: `POST /pictures/import` refuses without
// the face worker, a reference folder's initial scan is planner-driven, and a
// `DedupGroup` row is only ever written by the scan task the same disabled
// planner never runs. See that script's own docstring for the full reasoning.

import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from '../fixtures/test.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SEED_SCRIPT = resolve(__dirname, '../seed_dedup_fixture.py')
const REPO_ROOT = resolve(__dirname, '../../..')

// Three groups of three: three is the smallest size that can prove both halves
// of the exclusion floor, because it accepts exactly one exclusion and then
// refuses the next.
const SEED_GROUPS = 3
const SEED_COPIES = 2

/** Run the seed script and parse its JSON report. */
function runSeedScript(args) {
  const python = process.env.PIXLSTASH_PYTHON || 'python'
  const stdout = execFileSync(python, [SEED_SCRIPT, ...args], {
    // The backend is launched from `frontend/`, so the repo is not on
    // `sys.path` there. The seed imports the same pixlstash the server runs.
    env: { ...process.env, PYTHONPATH: REPO_ROOT },
    encoding: 'utf8',
  })
  return JSON.parse(stdout)
}

/** Seed the duplicates and detect them, returning the scan's own report. */
function seedDuplicates() {
  return runSeedScript([
    '--groups',
    String(SEED_GROUPS),
    '--copies',
    String(SEED_COPIES),
  ])
}

/** The unresolved-group count the sidebar badge is rendered from. */
async function unresolvedGroups(apiContext) {
  const res = await apiContext.post('/api/v1/dedup/counts', { data: {} })
  expect(res.ok()).toBeTruthy()
  return (await res.json()).unresolved_groups
}

/** The signatures the queue would serve right now, confidence descending. */
async function queuedSignatures(apiContext) {
  const res = await apiContext.get('/api/v1/dedup/groups?limit=50')
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  return body.groups.map((g) => g.signature)
}

// One shared, mutable backend and verdicts that are remembered for good, so the
// cases run in order and each one starts from the state the last left.
test.describe.serial('duplicates queue (§21)', () => {
  let seeded

  test.beforeAll(() => {
    seeded = seedDuplicates()
    expect(seeded.scan.groups_found).toBe(SEED_GROUPS)
    expect(seeded.signatures).toHaveLength(SEED_GROUPS)
  })

  // The suite shares one mutable backend, and these cases are the only ones
  // that add pictures and create stacks. Handing that on would leave a picture
  // set counting copies that later specs then fail to export or tag-health, so
  // the seed is undone here rather than left for the next file to survive.
  test.afterAll(() => {
    runSeedScript(['--cleanup'])
  })

  test('opens with the first group focused and walks it with the arrows', async ({
    duplicates,
  }) => {
    await duplicates.goto()
    await expect(duplicates.rows).toHaveCount(SEED_GROUPS)

    // Exactly one row is focused, and it is the first: "which row does Enter
    // hit" is the question the whole focused-row treatment exists to answer.
    await expect(duplicates.focusedRow).toHaveCount(1)
    const first = await duplicates.focusedSignature()
    expect(first).toBe(seeded.signatures[0])

    await duplicates.pressKey('ArrowDown')
    await expect(duplicates.row(seeded.signatures[1])).toHaveClass(
      /grow--focus/,
    )
    await duplicates.pressKey('ArrowDown')
    await expect(duplicates.row(seeded.signatures[2])).toHaveClass(
      /grow--focus/,
    )

    // The focus is clamped, not wrapped: an arrow key that jumped from the last
    // row back to the first would move the cursor a screen away from where the
    // user is looking.
    await duplicates.pressKey('ArrowDown')
    await expect(duplicates.row(seeded.signatures[2])).toHaveClass(
      /grow--focus/,
    )

    await duplicates.pressKey('ArrowUp')
    await expect(duplicates.row(seeded.signatures[1])).toHaveClass(
      /grow--focus/,
    )
  })

  // The QA finding this spec was written for: the floor is two INCLUDED
  // members, not one. A group that fell to a single member would still offer a
  // Stack button, and pressing it would be a guaranteed 400 from a server that
  // refuses a one-member stack.
  test('X excludes down to two members and then refuses', async ({
    duplicates,
  }) => {
    await duplicates.goto()
    await expect(duplicates.focusedCandidates()).toHaveCount(SEED_COPIES + 1)
    expect(await duplicates.focusedStackSize()).toBe(SEED_COPIES + 1)

    // The first exclusion is allowed: three members can spare one.
    await duplicates.pressKey('x')
    await expect(duplicates.focusedRow.locator('.gthumb--out')).toHaveCount(1)
    expect(await duplicates.focusedStackSize()).toBe(SEED_COPIES)

    // The second is refused, and said out loud rather than dropped silently.
    await duplicates.pressKey('x')
    await expect(duplicates.focusedRow.locator('.gthumb--out')).toHaveCount(1)
    expect(await duplicates.focusedStackSize()).toBe(SEED_COPIES)
    await expect(duplicates.announcement).toContainText(
      'A stack needs at least two pictures',
    )
  })

  test('C opens Compare on the focused group and Escape closes it', async ({
    duplicates,
  }) => {
    await duplicates.goto()
    await duplicates.pressKey('c')
    await expect(duplicates.compareDialog).toBeVisible()
    // Compare shows every candidate field by field; the row deliberately shows
    // none of those fields, which is why the second view exists at all.
    await expect(duplicates.compareCards).toHaveCount(SEED_COPIES + 1)

    await duplicates.page.keyboard.press('Escape')
    await expect(duplicates.compareDialog).toBeHidden()
  })

  test('Enter stacks the focused group, auto-advances and moves the badge', async ({
    duplicates,
    apiContext,
  }) => {
    const target = seeded.signatures[0]
    const before = await unresolvedGroups(apiContext)
    await duplicates.goto()
    // The sidebar shows PRESENCE, not a number: the count moved with the tier
    // gate, so it was retired for a dot. The numbers live in the queue header.
    await expect(duplicates.sidebarDot).toBeVisible()

    const memberIds = await duplicates
      .focusedCandidates()
      .evaluateAll((nodes) =>
        nodes
          .map((n) => n.querySelector('img')?.getAttribute('src') || '')
          .map((src) => Number((src.match(/thumbnails\/(\d+)/) || [])[1]))
          .filter(Boolean),
      )
    expect(memberIds.length).toBe(SEED_COPIES + 1)

    await duplicates.pressKey('Enter')

    // Auto-advance: the row is gone and the cursor has landed on the next open
    // group without a further keystroke.
    await expect(duplicates.row(target)).toHaveCount(0)
    await expect(duplicates.rows).toHaveCount(SEED_GROUPS - 1)
    await expect(duplicates.row(seeded.signatures[1])).toHaveClass(
      /grow--focus/,
    )

    // Server truth, not just a row that vanished: every member now sits in one
    // stack, and the group is out of the queue for good. An unstacked picture
    // answers `{ stack_id: null, picture_ids: [] }` here, so a real stack is
    // the one shape that can satisfy this.
    const stackRes = await apiContext.get(
      `/api/v1/pictures/${memberIds[0]}/stack`,
    )
    expect(stackRes.ok()).toBeTruthy()
    const stack = await stackRes.json()
    expect(stack.id).toBeTruthy()
    expect(stack.picture_ids).toEqual(expect.arrayContaining(memberIds))
    expect(await queuedSignatures(apiContext)).not.toContain(target)

    // The counts are reconciled from POST /dedup/counts after the verdict, not
    // inferred from a WebSocket event; groups remain, so the dot stays.
    expect(await unresolvedGroups(apiContext)).toBe(before - 1)
    await expect(duplicates.sidebarDot).toBeVisible()
  })

  test('S keeps the focused group separate and it stops being offered', async ({
    duplicates,
    apiContext,
  }) => {
    const target = seeded.signatures[1]
    const before = await unresolvedGroups(apiContext)
    await duplicates.goto()
    await expect(duplicates.row(target)).toHaveClass(/grow--focus/)

    await duplicates.pressKey('s')

    await expect(duplicates.row(target)).toHaveCount(0)
    await expect(duplicates.row(seeded.signatures[2])).toHaveClass(
      /grow--focus/,
    )
    expect(await queuedSignatures(apiContext)).not.toContain(target)

    // A keep-separate mutates no picture row, so it raises no WebSocket event.
    // The dot can only be right here because the verdict refetches the counts
    // itself — this is the assertion that pins that fix.
    expect(await unresolvedGroups(apiContext)).toBe(before - 1)
    await expect(duplicates.sidebarDot).toBeVisible()
  })

  test('working the last group through lands on the done state', async ({
    duplicates,
  }) => {
    await duplicates.goto()
    await expect(duplicates.rows).toHaveCount(1)
    await duplicates.pressKey('Enter')
    await expect(duplicates.doneState).toBeVisible()
    await expect(duplicates.doneState).toContainText('Queue clear')
    // The done state has to be true when it is shown: no picture is deleted, so
    // it says so rather than implying the copies went somewhere.
    await expect(duplicates.doneState).toContainText(
      'Every picture is still in your library',
    )
    // Nothing left to review in ANY tier, so the presence dot goes with it.
    await expect(duplicates.sidebarDot).toHaveCount(0)
  })
})
