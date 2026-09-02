import assert from 'node:assert/strict';
import { describe, it, beforeEach } from 'node:test';

import { runFirstRunSetup, type SetupChoices, type SetupDeps } from '../src/setup/RunSetup';

/**
 * First run has more outcomes than it looks like it does. The download and the
 * folder read run at the same time, so EITHER can finish first; the read can
 * fail, return nothing, or throw; the identity import can refuse; the backend
 * can refuse to start; and a machine with no GPU skips half of it. This is the
 * whole matrix, driven through fakes that record the order of what happened.
 */

type Accel = 'cu128';

/** A promise you resolve by hand, for deciding who finishes first. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const READ_RESULT = { picture_count: 153, levels: [] };

let log: string[];
let parkedTelemetry: unknown[];
let parkedMapping: unknown[];
let started: Array<{ accel: Accel | null; navigate: boolean }>;

function makeDeps(overrides: Partial<SetupDeps<Accel>> = {}): SetupDeps<Accel> {
  return {
    gpu: 'cu128',
    legacyIdentitySource: null,
    resolvePath: (p) => p,
    setBackendsRoot: (location) => log.push(`backendsRoot:${location}`),
    prepareLegacyIdentity: async (source) => {
      log.push(`prepareIdentity:${source}`);
    },
    writeConfig: (imageRoot) => log.push(`config:${imageRoot}`),
    parkTelemetry: (patch) => {
      log.push('parkTelemetry');
      parkedTelemetry.push(patch);
    },
    parkMapping: (entry) => {
      log.push(entry ? 'parkMapping' : 'clearMapping');
      parkedMapping.push(entry);
    },
    setActiveAccel: async (accel) => log.push(`activeAccel:${accel ?? 'none'}`),
    activeOverlayAccel: async () => null,
    startBackend: async (accel, navigate) => {
      log.push(`start:${accel ?? 'bundled'}:${navigate ? 'navigate' : 'stay'}`);
      started.push({ accel, navigate });
    },
    installOverlay: async (accel) => log.push(`install:${accel}`),
    readFolder: async () => {
      log.push('read');
      return READ_RESULT;
    },
    announceReading: () => log.push('announceReading'),
    ...overrides,
  };
}

const CHOICES: SetupChoices = { imageRoot: '/home/me/Pictures', useGpu: true };

beforeEach(() => {
  log = [];
  parkedTelemetry = [];
  parkedMapping = [];
  started = [];
});

describe('first-run setup, with a GPU runtime to install', () => {
  it('starts the backend before the download, or nothing overlaps', async () => {
    await runFirstRunSetup(CHOICES, makeDeps());

    const startedAt = log.indexOf('start:bundled:stay');
    const installedAt = log.indexOf('install:cu128');
    assert.ok(startedAt >= 0 && installedAt > startedAt, log.join(' → '));
  });

  it('reads and downloads at the same time, then restarts onto the GPU', async () => {
    await runFirstRunSetup(CHOICES, makeDeps());

    assert.deepEqual(log, [
      'config:/home/me/Pictures',
      'parkTelemetry',
      'activeAccel:none',
      'start:bundled:stay',
      'announceReading',
      'clearMapping',
      'read',
      'install:cu128',
      'parkMapping',
      'activeAccel:cu128',
      'start:cu128:navigate',
    ]);
    assert.deepEqual(parkedMapping.at(-1), {
      path: '/home/me/Pictures',
      result: READ_RESULT,
    });
  });

  it('waits for a read that is still going when the download finishes first', async () => {
    const read = deferred<Record<string, unknown> | null>();
    const setup = runFirstRunSetup(
      CHOICES,
      makeDeps({
        readFolder: () => read.promise,
        installOverlay: async (accel) => log.push(`install:${accel}`),
      }),
    );
    // Let the install finish while the read is still outstanding.
    await new Promise((r) => setImmediate(r));
    assert.ok(log.includes('install:cu128'), 'the download got there first');
    assert.ok(!started.some((s) => s.navigate), 'nothing may navigate yet');

    read.resolve(READ_RESULT);
    await setup;

    assert.deepEqual(parkedMapping.at(-1), {
      path: '/home/me/Pictures',
      result: READ_RESULT,
    });
    assert.deepEqual(started.at(-1), { accel: 'cu128', navigate: true });
  });

  it('does not restart early when the read finishes first', async () => {
    const install = deferred<void>();
    const setup = runFirstRunSetup(
      CHOICES,
      makeDeps({ installOverlay: async () => install.promise }),
    );
    await new Promise((r) => setImmediate(r));
    assert.ok(log.includes('read'), 'the read got there first');
    assert.equal(started.length, 1, 'the GPU restart must wait for the download');

    install.resolve();
    await setup;

    assert.deepEqual(started.at(-1), { accel: 'cu128', navigate: true });
  });

  it('parks nothing when the read finds nothing, and still finishes setup', async () => {
    await runFirstRunSetup(CHOICES, makeDeps({ readFolder: async () => null }));

    assert.deepEqual(parkedMapping, [null], 'only the clear at the start');
    assert.deepEqual(started.at(-1), { accel: 'cu128', navigate: true });
  });

  it('survives a read that throws, because the app can read the folder itself', async () => {
    await runFirstRunSetup(
      CHOICES,
      makeDeps({
        readFolder: async () => {
          throw new Error('the server went away');
        },
      }),
    );

    assert.deepEqual(started.at(-1), { accel: 'cu128', navigate: true });
    assert.deepEqual(parkedMapping, [null]);
  });

  it('lets a failed download reach the screen, and does not restart onto a GPU it has not got', async () => {
    const setup = runFirstRunSetup(
      CHOICES,
      makeDeps({
        installOverlay: async () => {
          throw new Error('no wheels for this CUDA generation');
        },
      }),
    );

    await assert.rejects(setup, /no wheels/);
    assert.ok(
      !started.some((s) => s.accel === 'cu128'),
      'a failed install must not be activated',
    );
    assert.ok(log.includes('read'), 'the read still ran, and still got to finish');
  });

  it('records the install location before anything downloads into it', async () => {
    await runFirstRunSetup({ ...CHOICES, installLocation: '/mnt/big' }, makeDeps());

    const rootAt = log.indexOf('backendsRoot:/mnt/big');
    assert.ok(rootAt >= 0 && rootAt < log.indexOf('install:cu128'), log.join(' → '));
  });
});

describe('first-run setup with nothing to download', () => {
  it('starts once, navigating, when the machine has no GPU', async () => {
    await runFirstRunSetup(CHOICES, makeDeps({ gpu: null }));

    assert.deepEqual(started, [{ accel: null, navigate: true }]);
    assert.deepEqual(parkedMapping, [], 'no read: there is no wait to share');
  });

  it('does the same when the GPU was offered and declined', async () => {
    await runFirstRunSetup({ ...CHOICES, useGpu: false }, makeDeps());

    assert.deepEqual(started, [{ accel: null, navigate: true }]);
    assert.ok(!log.includes('install:cu128'));
  });

  it('starts on an overlay that is already installed', async () => {
    await runFirstRunSetup(
      { ...CHOICES, useGpu: false },
      makeDeps({ activeOverlayAccel: async () => 'cu128' }),
    );

    assert.deepEqual(started, [{ accel: 'cu128', navigate: true }]);
  });
});

describe('first-run setup that must refuse', () => {
  it('refuses without a library folder, before touching anything', async () => {
    await assert.rejects(runFirstRunSetup({ imageRoot: '  ', useGpu: true }, makeDeps()), /folder/);
    assert.deepEqual(log, []);
  });

  it('refuses an identity import with no detected library, and writes no config', async () => {
    await assert.rejects(
      runFirstRunSetup({ ...CHOICES, importLegacyIdentity: true }, makeDeps()),
      /No existing standalone/,
    );
    assert.ok(!log.some((entry) => entry.startsWith('config:')));
  });

  it('refuses an identity import pointed at a different folder', async () => {
    await assert.rejects(
      runFirstRunSetup(
        { ...CHOICES, importLegacyIdentity: true },
        makeDeps({ legacyIdentitySource: '/home/me/Old' }),
      ),
      /keep the detected existing library selected/,
    );
    assert.ok(!log.some((entry) => entry.startsWith('config:')));
  });

  it('writes no config when the identity preparation itself fails', async () => {
    // Fail closed: a server that looks migrated and is not is worse than a
    // setup screen that stayed open.
    await assert.rejects(
      runFirstRunSetup(
        { ...CHOICES, importLegacyIdentity: true },
        makeDeps({
          legacyIdentitySource: '/home/me/Pictures',
          prepareLegacyIdentity: async () => {
            throw new Error('vault validation failed');
          },
        }),
      ),
      /vault validation failed/,
    );
    assert.ok(!log.some((entry) => entry.startsWith('config:')));
  });

  it('leaves the privacy answer unparked when the backend will not start', async () => {
    // The answer is parked before the start, so a refusal here leaves one
    // behind: it is cleared by the next attempt's own park, and the app takes
    // it only once. What must not happen is a config written and no answer.
    await assert.rejects(
      runFirstRunSetup(
        CHOICES,
        makeDeps({
          startBackend: async () => {
            throw new Error('unsafe file permissions');
          },
        }),
      ),
      /unsafe file permissions/,
    );
    assert.deepEqual(parkedTelemetry, [null]);
  });
});

describe('the privacy answer', () => {
  it('is parked after the config, so a refused setup leaves none', async () => {
    const patch = { check_for_updates: true, telemetry_consent_prompted: true };
    await runFirstRunSetup({ ...CHOICES, telemetry: patch }, makeDeps());

    assert.ok(log.indexOf('config:/home/me/Pictures') < log.indexOf('parkTelemetry'));
    assert.deepEqual(parkedTelemetry, [patch]);
  });

  it('clears a stale one when this run answered nothing', async () => {
    await runFirstRunSetup(CHOICES, makeDeps());
    assert.deepEqual(parkedTelemetry, [null]);
  });
});
