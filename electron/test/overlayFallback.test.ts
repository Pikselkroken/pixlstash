import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  OVERLAY_FALLBACK_MESSAGE,
  OverlayFallbackHooks,
  launchWithOverlayFallback,
} from '../src/backend/BackendManager';
import { Accel } from '../src/config';

/** Recording hooks: every effect is captured; `failOn` makes start() reject for those accels. */
function recordingHooks(failOn: ReadonlyArray<Accel | null>): {
  hooks: OverlayFallbackHooks;
  starts: Array<Accel | null>;
  deactivations: number;
  notifications: string[];
  logs: string[];
} {
  const rec = {
    starts: [] as Array<Accel | null>,
    deactivations: 0,
    notifications: [] as string[],
    logs: [] as string[],
    hooks: {} as OverlayFallbackHooks,
  };
  rec.hooks = {
    start: async (accel) => {
      rec.starts.push(accel);
      if (failOn.includes(accel)) {
        throw new Error(
          accel === null
            ? 'The PixlStash backend exited with code 1 during startup. (bundled env)'
            : 'The PixlStash backend exited with code 1 during startup.\n\nImportError: libcudart.so.13',
        );
      }
    },
    deactivateOverlay: async () => {
      rec.deactivations += 1;
    },
    notify: (message) => {
      rec.notifications.push(message);
    },
    log: (message) => {
      rec.logs.push(message);
    },
  };
  return rec;
}

describe('launchWithOverlayFallback — a broken overlay must never prevent launch', () => {
  it('healthy overlay launch: one start, no deactivation, no notification', async () => {
    const rec = recordingHooks([]);
    await launchWithOverlayFallback('cu128', rec.hooks);
    assert.deepEqual(rec.starts, ['cu128'], 'exactly one launch attempt');
    assert.equal(rec.deactivations, 0);
    assert.deepEqual(rec.notifications, []);
  });

  it('startup failure WITH an overlay → deactivate, notify, retry once WITHOUT it', async () => {
    // The incident shape: onnxruntime-gpu of the wrong CUDA generation makes the
    // backend die during startup — but only while the overlay is active.
    const rec = recordingHooks(['cu128']);
    await launchWithOverlayFallback('cu128', rec.hooks);

    assert.deepEqual(rec.starts, ['cu128', null], 'retried exactly once, overlay-free');
    assert.equal(rec.deactivations, 1, 'the overlay was deactivated (not deleted)');
    assert.deepEqual(rec.notifications, [OVERLAY_FALLBACK_MESSAGE], 'the user was told');
    assert.ok(
      rec.logs.some((l) => l.includes('cu128') && l.includes('libcudart.so.13')),
      'the failure is logged with the accel and the real cause',
    );
  });

  it('startup failure WITHOUT an overlay → rethrows to the fatal path, no retry, no notify', async () => {
    // No overlay involved: this is a genuine packaging error, and the existing
    // fatal error screen must handle it exactly as before.
    const rec = recordingHooks([null]);
    await assert.rejects(launchWithOverlayFallback(null, rec.hooks), /exited with code 1/);
    assert.deepEqual(rec.starts, [null], 'exactly one attempt — no retry loop');
    assert.equal(rec.deactivations, 0, 'nothing to deactivate');
    assert.deepEqual(rec.notifications, [], 'no misleading "running on CPU" message');
  });

  it('overlay launch fails AND the CPU retry fails → rethrows the CPU error, exactly two attempts', async () => {
    // Both fail: the overlay fallback ran (deactivate + notify), and then the
    // bundled-env failure propagates to the caller's fatal path. The retry is a
    // plain call in the catch block, so a third attempt is impossible.
    const rec = recordingHooks(['cu128', null]);
    await assert.rejects(launchWithOverlayFallback('cu128', rec.hooks), /bundled env/);
    assert.deepEqual(rec.starts, ['cu128', null], 'exactly two attempts, never a loop');
    assert.equal(rec.deactivations, 1, 'the broken overlay was still deactivated');
    assert.deepEqual(rec.notifications, [OVERLAY_FALLBACK_MESSAGE]);
  });
});
