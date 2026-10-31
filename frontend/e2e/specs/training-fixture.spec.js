// The e2e backend can train a LoRA (#2359).
//
// serve_e2e_backend.py lays out a fake ai-toolkit checkout under its throwaway
// work dir, whose run.py is the backend suite's CPU stand-in
// (tests/training/fake_run.py), and gives training a pretend card, so a run
// neither needs a GPU (CI has none) nor leases this machine's. The checkout is
// connected here with the PUT the Settings dialog sends: the setting lives on
// the owner's row, which does not exist until global-setup registers one. This
// spec is the fixture's own check: a run started with the Train LoRA dialog's
// POST is seen running, finishes, and everything it wrote is in the work dir.

import { existsSync, readdirSync, realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, sep } from 'node:path'
import { test, expect } from '../fixtures/test.js'
import { seededFolder } from '../pages/ModelShelfPage.js'

const WORK_DIR_PREFIX = `${realpathSync(tmpdir())}${sep}pixlstash-e2e-`

test.describe('training on the fake ai-toolkit', () => {
  let jobId = null

  // Every later spec sees AI-toolkit unconnected again, whatever the test did.
  // The run's LoRA and the checkout's output folder stay on the shelf, both in
  // the work dir.
  test.afterAll(async ({ apiContext }) => {
    try {
      if (jobId !== null) {
        const job = await (await apiContext.get(`/api/v1/training/jobs/${jobId}`)).json()
        if (['queued', 'running', 'stopping'].includes(job.status)) {
          await apiContext.post(`/api/v1/training/jobs/${jobId}/cancel`, { data: {} })
        }
        // 409 until the run has stopped, or finished importing.
        await expect
          .poll(async () => (await apiContext.delete(`/api/v1/training/jobs/${jobId}`)).status(), {
            timeout: 15_000,
          })
          .toBe(200)
      }
    } finally {
      const disconnected = await apiContext.put('/api/v1/training/ai-toolkit/config', {
        data: { repo_path: null, python: null },
      })
      expect(disconnected.ok(), await disconnected.text()).toBe(true)
    }
  })

  test('a run starts, is seen running, finishes and writes only under the work dir', async ({
    apiContext,
  }) => {
    // Server start-up is the same for every spec; the run itself is ~6 s, and
    // its two polls may not add up to more than the test is given.
    test.setTimeout(60_000)
    const { workDir } = await seededFolder(apiContext)
    expect(workDir.startsWith(WORK_DIR_PREFIX), workDir).toBe(true)
    const checkout = join(workDir, 'ai-toolkit')
    const connected = await apiContext.put('/api/v1/training/ai-toolkit/config', {
      data: { repo_path: checkout, python: join(checkout, '.venv', 'bin', 'python') },
    })
    expect(connected.ok(), await connected.text()).toBe(true)

    const status = await (await apiContext.get('/api/v1/training/ai-toolkit/status')).json()
    expect(status.configured).toBe(true)
    for (const path of [status.repo_path, status.python, status.datasets.path, status.runs.path]) {
      expect(path.startsWith(`${workDir}${sep}`), path).toBe(true)
    }

    const sets = await (await apiContext.get('/api/v1/picture_sets')).json()
    const set = sets.find((s) => s.picture_count > 0)
    expect(set, 'the fixture has a picture set to train on').toBeTruthy()
    // The dialog's body, with the steps pinned (as its Advanced section can)
    // so the run is short: 20 steps at the launcher's step delay.
    const created = await apiContext.post('/api/v1/training/jobs', {
      data: {
        set_id: set.id,
        name: 'e2e fixture LoRA',
        trigger_word: 'e2efixture',
        effort: 'standard',
        fit: 'auto',
        subject: 'thing',
        pins: { 'train.steps': 20, 'save.save_every': 10 },
      },
    })
    expect(created.ok(), await created.text()).toBe(true)
    const { id, uuid } = await created.json()
    jobId = id

    const read = async () => (await apiContext.get(`/api/v1/training/jobs/${id}`)).json()
    await expect.poll(async () => (await read()).status, { intervals: [100] }).toBe('running')
    // Finished means imported too: completed_at is set once the LoRA is on
    // the shelf, and the job can be deleted only then.
    await expect
      .poll(async () => {
        const job = await read()
        return `${job.status}:${job.step}:${Boolean(job.completed_at)}:${job.error ?? ''}`
      }, { timeout: 20_000 })
      .toBe('completed:20:true:')
    expect((await read()).model_ids.length).toBeGreaterThan(0)

    // The job database, the dataset, the run and its LoRA, on disk where the
    // backend put them (the API names no host path).
    expect(existsSync(join(workDir, 'images', 'training', uuid, 'job.db'))).toBe(true)
    expect(existsSync(join(status.datasets.path, `pixlstash-${uuid}`))).toBe(true)
    const runs = readdirSync(status.runs.path).filter((name) => name.endsWith(uuid.slice(0, 8)))
    expect(runs).toHaveLength(1)
    expect(existsSync(join(status.runs.path, runs[0], `${runs[0]}.safetensors`))).toBe(true)
    expect(existsSync(join(workDir, 'models', `${runs[0]}.safetensors`))).toBe(true)
  })
})
