import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  ExecRunner,
  prepareLegacyIdentity,
} from '../src/setup/LegacyIdentityPreparation';

describe('prepareLegacyIdentity', () => {
  it('uses the bundled CLI module with an explicit hub and absolute vault folder', async () => {
    let call: { file: string; args: string[]; timeout: number } | null = null;
    const run: ExecRunner = async (file, args, options) => {
      call = { file, args, timeout: options.timeout };
      return {
        stdout: 'uuid-123 /photos/family\n',
        stderr: '',
      };
    };

    const output = await prepareLegacyIdentity(
      '/app/python/bin/python3',
      '/config/hub.db',
      '/photos/family',
      run,
    );

    assert.equal(output, 'uuid-123 /photos/family');
    assert.deepEqual(call, {
      file: '/app/python/bin/python3',
      args: [
        '-m',
        'pixlstash.cli',
        '--hub',
        '/config/hub.db',
        'libraries',
        'prepare-legacy-identity',
        '/photos/family',
      ],
      timeout: 30_000,
    });
  });

  it('surfaces a nonzero CLI failure so setup cannot launch silently', async () => {
    const run: ExecRunner = async () => {
      const error = new Error('process exited 1') as Error & { stderr: string };
      error.stderr = 'vault fingerprint does not match';
      throw error;
    };

    await assert.rejects(
      prepareLegacyIdentity(
        '/app/python/bin/python3',
        '/config/hub.db',
        '/photos/family',
        run,
      ),
      /Could not prepare.*vault fingerprint does not match/,
    );
  });
});
