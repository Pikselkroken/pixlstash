// Global setup for the website captures: build what the desktop app runs, and
// lay out the demo-data library it will open. electronApp.js launches it.
//
//   1. the SPA (`npm run build`, into pixlstash/frontend/dist, which the backend
//      serves) — vite reads PIXLSTASH_VERSION_OVERRIDE here, so a pinned run's
//      title bar and settings header carry the pinned version;
//   2. the Electron shell (`npm run build` in electron/);
//   3. the throwaway work dir (`serve_e2e_backend.py --screenshots`), whose
//      server-config.json becomes the shell's own — it is its userData.
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const FRONTEND = resolve(here, '..', '..')
const REPO = resolve(FRONTEND, '..')
const ELECTRON_DIR = join(REPO, 'electron')
export const WORK_INFO = join(here, '..', '.auth', 'electron-shots.json')

const run = (cmd, args, cwd, env = process.env) =>
  execFileSync(cmd, args, {
    cwd,
    env,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'inherit'],
    shell: process.platform === 'win32',
  })

export default async function electronSetup() {
  run('npm', ['run', 'build'], FRONTEND)
  run('npm', ['run', 'build'], ELECTRON_DIR)

  // The shell checks the interpreter exists, so hand it an absolute path. Probe
  // a backend dependency here: a bare system python otherwise gets as far as
  // the app window and dies there in an error dialog.
  const venv = join(REPO, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const pythonCmd = process.env.PIXLSTASH_PYTHON || (existsSync(venv) ? venv : 'python')
  let python
  try {
    python = run(pythonCmd, ['-c', 'import sys, passlib, fastapi; print(sys.executable)'], REPO).trim()
  } catch (err) {
    throw new Error(
      `${pythonCmd} cannot run the backend (${err.message.split('\n')[0]}). ` +
        'Set PIXLSTASH_PYTHON to the interpreter of a venv with pixlstash installed.',
      { cause: err },
    )
  }

  const prepared = run(python, [join(FRONTEND, 'e2e', 'serve_e2e_backend.py'), '--screenshots'], REPO, {
    ...process.env,
    PIXLSTASH_E2E_DATA: join(REPO, 'demo-data'),
    PYTHONPATH: REPO,
  })
  const configPath = prepared.trim().split('\n').pop()

  mkdirSync(dirname(WORK_INFO), { recursive: true })
  const electron = createRequire(join(ELECTRON_DIR, 'package.json'))('electron')
  writeFileSync(
    WORK_INFO,
    JSON.stringify({ python, electron, electronDir: ELECTRON_DIR, userData: dirname(configPath) }, null, 2),
  )
}
