import assert from 'node:assert/strict';
import { describe, it, beforeEach } from 'node:test';

import { runFirstRunSetup, type SetupChoices, type SetupDeps } from '../src/setup/RunSetup';

/**
 * First run has more outcomes than it looks like it does. The download has to
 * finish before the backend starts, or the read it feeds runs on the CPU
 * runtime; the read can fail, return nothing, or throw; the identity import can
 * refuse; the backend can refuse to start; and a machine with no GPU skips half
 * of it. This is the whole matrix, driven through fakes that record the order
 * of what happened.
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
    clearConfig: () => log.push('clearConfig'),
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
    navigateToApp: async () => {
      log.push('navigate');
    },
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
  it('downloads before the backend starts, or the read runs on the CPU runtime', async () => {
    // The device is fixed when the backend process is spawned. A backend
    // started before the overlay lands reads the whole library on the CPU, and
    // nothing can move it afterwards - which is what this order exists for.
    await runFirstRunSetup(CHOICES, makeDeps());

    const installedAt = log.indexOf('install:cu128');
    const startedAt = log.indexOf('start:cu128:stay');
    const readAt = log.indexOf('read');
    assert.ok(installedAt >= 0 && installedAt < startedAt, log.join(' → '));
    assert.ok(startedAt < readAt, log.join(' → '));
  });

  it('starts the backend exactly once, on the GPU', async () => {
    await runFirstRunSetup(CHOICES, makeDeps());

    assert.deepEqual(started, [{ accel: 'cu128', navigate: false }]);
  });

  it('downloads, starts on the GPU, reads on it, then hands the window over', async () => {
    await runFirstRunSetup(CHOICES, makeDeps());

    assert.deepEqual(log, [
      'config:/home/me/Pictures',
      'parkTelemetry',
      'install:cu128',
      'activeAccel:cu128',
      'start:cu128:stay',
      'announceReading',
      'clearMapping',
      'read',
      'parkMapping',
      'navigate',
    ]);
    assert.deepEqual(parkedMapping.at(-1), {
      path: '/home/me/Pictures',
      result: READ_RESULT,
    });
  });

  it('does not hand the window over until the read has finished', async () => {
    // The app collects the parked mapping as it loads, so a window handed over
    // mid-read opens on a progress bar with nothing parked for it.
    const read = deferred<Record<string, unknown> | null>();
    const setup = runFirstRunSetup(CHOICES, makeDeps({ readFolder: () => read.promise }));
    await new Promise((r) => setImmediate(r));

    assert.ok(log.includes('start:cu128:stay'), 'the GPU backend is up');
    assert.ok(!log.includes('navigate'), 'nothing may navigate yet');

    read.resolve(READ_RESULT);
    await setup;

    assert.equal(log.at(-1), 'navigate');
    assert.deepEqual(parkedMapping.at(-1), {
      path: '/home/me/Pictures',
      result: READ_RESULT,
    });
  });

  it('parks nothing when the read finds nothing, and still finishes setup', async () => {
    await runFirstRunSetup(CHOICES, makeDeps({ readFolder: async () => null }));

    assert.deepEqual(parkedMapping, [null], 'only the clear at the start');
    assert.equal(log.at(-1), 'navigate');
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

    assert.equal(log.at(-1), 'navigate');
    assert.deepEqual(parkedMapping, [null]);
  });

  it('starts nothing at all when the download fails', async () => {
    // The download is now the first thing that happens, so its failure reaches
    // the screen before a backend, a read or an activated overlay exists.
    const setup = runFirstRunSetup(
      CHOICES,
      makeDeps({
        installOverlay: async () => {
          throw new Error('no wheels for this CUDA generation');
        },
      }),
    );

    await assert.rejects(setup, /no wheels/);
    assert.deepEqual(started, [], 'no backend was started');
    assert.ok(!log.includes('activeAccel:cu128'), 'a failed install must not be activated');
    assert.ok(!log.includes('read'), 'nothing was read on a runtime that never arrived');
    assert.ok(!log.includes('navigate'), 'the setup screen keeps the message');
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
    assert.deepEqual(parkedTelemetry, [null, null], 'parked, then rolled back');
  });

  it('takes the config back off disk when the setup it was written for fails', async () => {
    // Launch shows the wizard only when there is NO config, so one left behind
    // by a failed setup removes the folder picker from every later launch - and
    // the folder it names is the one the failure was about. Declining the
    // recreate offer trapped the choice it was asking you to change.
    await assert.rejects(
      runFirstRunSetup(
        CHOICES,
        makeDeps({
          startBackend: async () => {
            throw new Error('could not open the library database');
          },
        }),
      ),
      /library database/,
    );
    assert.ok(log.indexOf('config:/home/me/Pictures') < log.indexOf('clearConfig'));
    assert.ok(log.includes('clearConfig'), log.join(' → '));
  });

  it('rolls the config back when the GPU download is what failed', async () => {
    await assert.rejects(
      runFirstRunSetup(
        CHOICES,
        makeDeps({
          installOverlay: async () => {
            throw new Error('no wheels for this CUDA generation');
          },
        }),
      ),
      /no wheels/,
    );
    assert.ok(log.includes('clearConfig'), log.join(' → '));
  });

  it('leaves the config alone when the setup finished', async () => {
    await runFirstRunSetup(CHOICES, makeDeps());
    assert.ok(!log.includes('clearConfig'), log.join(' → '));
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
