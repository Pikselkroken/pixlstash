import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { posix, resolve, sep, win32 } from 'node:path';
import { computeDefaultBackendsRoot, normalizeBackendsRoot } from '../src/config';

describe('computeDefaultBackendsRoot — where GPU overlays default to', () => {
  it('Windows packaged installs next to the app (inside the chosen install folder)', () => {
    // process.resourcesPath is <installDir>/resources, so the overlay sits in
    // <installDir>/backends — following the location the user picked in the
    // installer instead of always landing under AppData on the system drive.
    const root = computeDefaultBackendsRoot(
      'win32',
      true,
      'D:\\Apps\\PixlStash\\resources',
      'C:\\Users\\me\\AppData\\Roaming\\pixlstash-desktop',
    );
    assert.equal(root, win32.join('D:\\Apps\\PixlStash', 'backends'));
    // The system-drive AppData path must NOT be used as the default on Windows.
    assert.ok(!root.includes('AppData'), 'Windows default must escape AppData');
  });

  it('Windows in dev (unpackaged) falls back to userData — no real install dir', () => {
    const userData = 'C:\\Users\\me\\AppData\\Roaming\\pixlstash-desktop';
    const root = computeDefaultBackendsRoot('win32', false, 'C:\\whatever\\resources', userData);
    assert.equal(root, win32.join(userData, 'backends'));
  });

  it('Linux keeps overlays under userData (the AppImage image is read-only)', () => {
    const userData = '/home/me/.config/pixlstash-desktop';
    const root = computeDefaultBackendsRoot('linux', true, '/tmp/.mount_x/resources', userData);
    assert.equal(root, posix.join(userData, 'backends'));
  });

  it('macOS keeps overlays under userData (the notarized .app must not be modified)', () => {
    const userData = '/Users/me/Library/Application Support/pixlstash-desktop';
    const root = computeDefaultBackendsRoot(
      'darwin',
      true,
      '/Applications/PixlStash.app/Contents/Resources',
      userData,
    );
    assert.equal(root, posix.join(userData, 'backends'));
  });
});

describe('normalizeBackendsRoot — custom vs default persistence rule', () => {
  const def = resolve('/data/backends');

  it('returns null when the choice equals the default (so it keeps tracking the install dir)', () => {
    assert.equal(normalizeBackendsRoot('/data/backends', def), null);
  });

  it('treats a trailing-separator path as the same as the default', () => {
    assert.equal(normalizeBackendsRoot(`/data/backends${sep}`, def), null);
  });

  it('returns the resolved path when the choice differs from the default', () => {
    assert.equal(normalizeBackendsRoot('/mnt/big/backends', def), resolve('/mnt/big/backends'));
  });
});
