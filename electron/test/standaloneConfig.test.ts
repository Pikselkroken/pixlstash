import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, it } from 'node:test';
import { promisify } from 'node:util';

import { ExecRunner, standaloneConfigPath } from '../src/setup/StandaloneConfig';

const execFileP = promisify(execFile);
const PYTHON = process.platform === 'win32' ? 'python' : 'python3';

describe('standaloneConfigPath', () => {
  it('reads a non-ASCII config dir unchanged when Python stdout is not UTF-8 (#2802)', async () => {
    const root = mkdtempSync(join(tmpdir(), 'standalone-config-'));
    try {
      // The real snippet, run by a real interpreter, against a stub platformdirs
      // whose config dir sits under a Cyrillic folder. PYTHONIOENCODING=cp1251
      // is what a piped stdout is on a Cyrillic Windows.
      const configDir = join(root, 'Проба', 'pixlstash');
      mkdirSync(configDir, { recursive: true });
      const configPath = join(configDir, 'server-config.json');
      writeFileSync(configPath, '{}');
      const stubs = join(root, 'stubs');
      mkdirSync(stubs);
      writeFileSync(
        join(stubs, 'platformdirs.py'),
        `def user_config_dir(app):\n    return ${JSON.stringify(configDir)}\n`,
        'utf8',
      );
      const run: ExecRunner = (file, args, options) =>
        execFileP(file, args, {
          ...options,
          env: { ...process.env, PYTHONPATH: stubs, PYTHONIOENCODING: 'cp1251' },
        });

      assert.equal(await standaloneConfigPath(PYTHON, run), configPath);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it('returns null when the interpreter cannot answer', async () => {
    const run: ExecRunner = async () => {
      throw new Error('no such interpreter');
    };
    assert.equal(await standaloneConfigPath('/nowhere/python', run), null);
  });
});
