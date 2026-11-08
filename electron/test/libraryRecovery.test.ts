import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'node:test';
import {
  activateLibrary,
  isSameOrInside,
  LIBRARY_UNAVAILABLE_PREFIX,
  LibraryUnavailableError,
  parseLibraryUnavailableReport,
  relocateLibrary,
  withImageRoot,
} from '../src/backend/LibraryRecovery';
import { startupFailureError } from '../src/backend/ServerProcess';
import { PERMISSION_REPAIR_PREFIX, PermissionRepairRequiredError } from '../src/backend/StartupPermissions';

const report = {
  version: 1 as const,
  kind: 'folder_missing' as const,
  library: {
    id: 2,
    uuid: '00000000-0000-4000-8000-0000000000bb',
    name: 'my-library',
    path: '/home/me/Downloads/my-library',
  },
  vault_path: '/home/me/Downloads/my-library/vault.db',
  reason: 'Could not verify the registered library fingerprint: unable to open database file',
  alternatives: [
    { id: 1, uuid: '00000000-0000-4000-8000-0000000000aa', name: 'Kept', path: '/home/me/kept' },
  ],
};

describe('the missing-library startup protocol', () => {
  it('parses the backend record out of ordinary diagnostic output', () => {
    const output = [
      'PixlStash could not open its library "my-library":',
      '- Could not verify the registered library fingerprint',
      `${LIBRARY_UNAVAILABLE_PREFIX}${JSON.stringify(report)}`,
    ].join('\n');

    assert.deepEqual(parseLibraryUnavailableReport(output), report);
  });

  it('turns the record into a typed error the shell can offer a way out of', () => {
    const error = startupFailureError(1, null, `${LIBRARY_UNAVAILABLE_PREFIX}${JSON.stringify(report)}`);

    assert.ok(error instanceof LibraryUnavailableError, 'a plain Error is the dead end this replaces');
    assert.deepEqual(error.report, report);
  });

  it('leaves a permission repair as a permission repair', () => {
    const output = [
      `${PERMISSION_REPAIR_PREFIX}${JSON.stringify({
        version: 1,
        issues: [{ area: 'Library', path: '/home/me/kept', current_mode: '775', repaired_mode: '700' }],
      })}`,
      `${LIBRARY_UNAVAILABLE_PREFIX}${JSON.stringify(report)}`,
    ].join('\n');

    assert.ok(startupFailureError(1, null, output) instanceof PermissionRepairRequiredError);
  });

  it('refuses malformed records rather than offering an action', () => {
    const bad = (value: unknown) =>
      parseLibraryUnavailableReport(`${LIBRARY_UNAVAILABLE_PREFIX}${JSON.stringify(value)}`);
    assert.equal(parseLibraryUnavailableReport(`${LIBRARY_UNAVAILABLE_PREFIX}{oops`), null);
    assert.equal(bad({ ...report, version: 2 }), null);
    assert.equal(bad({ ...report, kind: 'gone' }), null);
    assert.equal(bad({ ...report, library: { ...report.library, uuid: '' } }), null);
    assert.equal(bad({ ...report, alternatives: [{ name: 'no id' }] }), null);
    assert.equal(bad({ ...report, alternatives: undefined }), null);
    assert.equal(parseLibraryUnavailableReport('The PixlStash backend exited with code 1'), null);
  });

  it('takes the last record, so a retry describes the retry', () => {
    const older = { ...report, kind: 'vault_missing' };
    const output = [
      `${LIBRARY_UNAVAILABLE_PREFIX}${JSON.stringify(older)}`,
      `${LIBRARY_UNAVAILABLE_PREFIX}${JSON.stringify(report)}`,
    ].join('\n');

    assert.equal(parseLibraryUnavailableReport(output)?.kind, 'folder_missing');
  });
});

describe('the recovery actions', () => {
  it('finds the library only if the folder holds that same library', async () => {
    const calls: string[][] = [];
    await relocateLibrary('/py', '/home/me/hub.db', report.library, '/media/disk/my-library', async (file, args) => {
      calls.push([file, ...args]);
      return { stdout: '', stderr: '' };
    });

    assert.deepEqual(calls, [
      [
        '/py',
        '-m',
        'pixlstash.cli',
        '--hub',
        '/home/me/hub.db',
        'libraries',
        'relocate',
        report.library.uuid,
        '/media/disk/my-library',
        '--same-library',
      ],
    ]);
  });

  it('activates by uuid, never by a name another library could share', async () => {
    const calls: string[][] = [];
    await activateLibrary('/py', '/home/me/hub.db', report.alternatives[0], async (_file, args) => {
      calls.push(args);
      return { stdout: '', stderr: '' };
    });

    assert.deepEqual(calls[0].slice(-2), ['activate', report.alternatives[0].uuid]);
  });

  it('shows the CLI refusal itself, without its error prefix', async () => {
    const refusal = Object.assign(new Error('Command failed'), {
      stderr: 'error: /media/disk/other holds a different library, not "my-library". Nothing was changed.\n',
    });

    await assert.rejects(
      relocateLibrary('/py', '/home/me/hub.db', report.library, '/media/disk/other', async () => {
        throw refusal;
      }),
      { message: '/media/disk/other holds a different library, not "my-library". Nothing was changed.' },
    );
  });

  it('never starts a new library where the missing one belongs', () => {
    assert.ok(isSameOrInside('/home/me/Downloads/my-library', report.library.path, 'linux'));
    assert.ok(isSameOrInside('/home/me/Downloads/my-library/sub', report.library.path, 'linux'));
    assert.ok(!isSameOrInside('/home/me/Downloads/my-library-2', report.library.path, 'linux'));
    assert.ok(!isSameOrInside('/home/me/Downloads', report.library.path, 'linux'));
  });

  it('points the config at the new folder and keeps every other setting', () => {
    const before = JSON.stringify({ host: '127.0.0.1', port: 9537, image_root: '/home/me/old' });
    const after = JSON.parse(withImageRoot(before, '/home/me/new')) as Record<string, unknown>;

    assert.deepEqual(after, { host: '127.0.0.1', port: 9537, image_root: '/home/me/new' });
    assert.throws(() => withImageRoot('[]', '/home/me/new'));
  });
});

describe('the recovery screen assets', () => {
  const rendererDir = join(__dirname, '..', '..', 'src', 'renderer');

  it('drives every element it renders and offers every way out', () => {
    const html = readFileSync(join(rendererDir, 'library-recovery.html'), 'utf8');
    const script = readFileSync(join(rendererDir, 'library-recovery.js'), 'utf8');

    for (const id of ['title', 'intro', 'cause', 'reason', 'error', 'retry', 'find', 'others', 'alternatives', 'create', 'quit']) {
      assert.match(html, new RegExp(`id="${id}"`), `library-recovery.html must keep #${id}`);
      assert.match(script, new RegExp(`getElementById\\('${id}'\\)`));
    }
    for (const call of [
      'libraryRecoveryRequest',
      'findMissingLibrary',
      'switchToLibrary',
      'createReplacementLibrary',
      'retryLibrary',
      'quitLibraryRecovery',
    ]) {
      assert.match(script, new RegExp(`api\\.${call}\\(`), `the screen must call ${call}`);
    }
    // One wording per kind the backend reports.
    for (const kind of ['folder_missing', 'vault_missing', 'unreadable', 'foreign']) {
      assert.match(script, new RegExp(`${kind}:`));
    }
  });

  it('is exposed through the preload bridge on the channels main serves', () => {
    const preload = readFileSync(join(rendererDir, '..', 'preload.ts'), 'utf8');
    const main = readFileSync(join(rendererDir, '..', 'main.ts'), 'utf8');
    for (const channel of ['request', 'find', 'switch', 'create', 'retry', 'quit']) {
      assert.match(preload, new RegExp(`'libraryRecovery:${channel}'`));
      assert.match(main, new RegExp(`'libraryRecovery:${channel}'`));
    }
  });
});
