import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { describe, it } from 'node:test';
import { offerPath, requireOfferedPath, requireServerSettings } from '../src/config';

/**
 * `backend:setLocation` and `setup:commit` take a folder from the renderer and
 * hand it to code that creates, writes and recursively deletes:
 * `changeBackendsLocation` -> `moveDir` does
 * `rm(to, { recursive: true, force: true })` on `<caller-chosen root>/<accel>`,
 * and `setup:commit` writes the server config's `image_root` and installs a
 * 2.5 GB runtime under `installLocation`.
 *
 * Both are *meant* to take a user-chosen path, so an allowlist of directories
 * would be the wrong control and would break the feature. What is checked is
 * provenance: only the main process can open a native dialog or compute a
 * default, so a path it never handed out did not come from one (CWE-20, the
 * sibling of the item 13 accelerator fix in #1190).
 */
describe('requireOfferedPath', () => {
  it('accepts a path the dialog just handed out', () => {
    const chosen = resolve(sep, 'home', 'me', 'Pictures');
    offerPath(chosen, 'library');
    assert.equal(requireOfferedPath(chosen, 'library', 'Library folder'), chosen);
  });

  it('refuses a path offered for the OTHER question', () => {
    // One pool would let the library folder be replayed as the GPU install
    // location, which makes setBackendsRoot point at the user's pictures and
    // accel:install / backend:setLocation then recursively delete
    // `<library>/<accel>` inside it.
    const library = resolve(sep, 'home', 'me', 'Pictures');
    const backends = resolve(sep, 'home', 'me', '.cache', 'backends');
    offerPath(library, 'library');
    offerPath(backends, 'backends');
    assert.throws(
      () => requireOfferedPath(library, 'backends', 'GPU install location'),
      /was not offered by PixlStash for this choice/,
    );
    assert.throws(
      () => requireOfferedPath(backends, 'library', 'Library folder'),
      /was not offered by PixlStash for this choice/,
    );
    // Each is still accepted for its own question.
    assert.equal(requireOfferedPath(library, 'library', 'Library folder'), library);
    assert.equal(requireOfferedPath(backends, 'backends', 'GPU install location'), backends);
  });

  it('accepts the same folder spelled differently', () => {
    // The renderer trims the readonly field, and a path may arrive with a
    // trailing separator; both must still resolve to the offered folder.
    const chosen = resolve(sep, 'home', 'me', 'Library Two');
    offerPath(chosen, 'library');
    assert.equal(requireOfferedPath(`  ${chosen}${sep}  `, 'library', 'Library folder'), chosen);
    assert.equal(requireOfferedPath(join(chosen, 'x', '..'), 'library', 'Library folder'), chosen);
  });

  it('refuses a folder nobody offered', () => {
    for (const bad of [
      resolve(sep, 'etc'),
      resolve(sep, 'home', 'me'),
      resolve(sep, 'home', 'me', 'Pictures', 'nested'),
      '../..',
      'C:\\Windows',
    ]) {
      assert.throws(
        () => requireOfferedPath(bad, 'backends', 'GPU install location'),
        /was not offered by PixlStash/,
        `${bad} must not be accepted as a destination`,
      );
    }
  });

  it('refuses anything that is not a non-empty string', () => {
    for (const bad of [
      null,
      undefined,
      '',
      '   ',
      123,
      // Structured clone carries a BigInt over IPC and JSON.stringify throws on
      // one; describing the value must not become the failure.
      1n,
      // A caller-supplied toString must never be called to describe the value.
      { toString: () => resolve(sep, 'home', 'me', 'Pictures') },
      [resolve(sep, 'home', 'me', 'Pictures')],
    ]) {
      assert.throws(
        () => requireOfferedPath(bad, 'library', 'Library folder'),
        /must be a folder path|was not offered by PixlStash/,
        `${typeof bad} must not be accepted as a destination`,
      );
    }
  });

  it('offerPath returns its argument and ignores nothing-values', () => {
    // It sits inline in the handlers' return expressions, so it must be
    // transparent, and a `null` default (no detected legacy library) must not
    // put an empty string on the offered set.
    const chosen = resolve(sep, 'home', 'you', 'Pictures');
    assert.equal(offerPath(chosen, 'library'), chosen);
    assert.equal(offerPath(null, 'library'), null);
    assert.equal(offerPath(undefined, 'library'), undefined);
    assert.throws(
      () => requireOfferedPath('', 'library', 'Library folder'),
      /must be a folder path/,
    );
  });
});

/**
 * `server:setSettings` writes `enabled` and `ssl` into the backend's config.
 * `enabled` binds a second listener on 0.0.0.0 and `ssl` decides whether it
 * demands TLS; both were written unchecked, so a non-boolean could read false
 * to the backend and true to the shell's own `Boolean(...)` - a plaintext LAN
 * listener the toggle reports as encrypted.
 */
describe('requireServerSettings', () => {
  it('passes a well-formed settings object through', () => {
    const settings = { enabled: true, port: 9537, ssl: true };
    assert.deepEqual(requireServerSettings(settings), settings);
    assert.deepEqual(requireServerSettings({ enabled: false, port: 1, ssl: false }), {
      enabled: false,
      port: 1,
      ssl: false,
    });
  });

  it('refuses a non-boolean enabled or ssl', () => {
    for (const bad of [
      { enabled: 1, port: 9537, ssl: true },
      { enabled: true, port: 9537, ssl: 0 },
      { enabled: true, port: 9537, ssl: [] },
      { enabled: true, port: 9537, ssl: 'false' },
      { enabled: 'yes', port: 9537, ssl: false },
      { enabled: true, port: 9537 },
      { port: 9537, ssl: true },
      {},
      null,
      undefined,
      'enabled',
    ]) {
      assert.throws(
        () => requireServerSettings(bad),
        /must be true or false/,
        `${JSON.stringify(bad) ?? String(bad)} must not be accepted`,
      );
    }
  });

  it('leaves a half-typed port alone rather than throwing', () => {
    // The port field emits Number('') === 0 and Number('x') === NaN while it is
    // being typed, and writeServerSettings already ignores anything outside
    // 1-65535 and keeps the stored port. Refusing here would turn typing into
    // an error dialog - over-blocking is its own regression.
    assert.equal(requireServerSettings({ enabled: true, port: 0, ssl: false }).port, 0);
    assert.ok(Number.isNaN(requireServerSettings({ enabled: true, port: NaN, ssl: false }).port));
  });
});

/**
 * Source-text assertions, because `main.ts` registers its handlers as a side
 * effect of an Electron app boot and exposes no module boundary to import - the
 * same reason `accelIpcValidation.test.ts` reads it as text.
 *
 * What is pinned is the shape. Declaring the parameter `dir: string` again is
 * exactly the regression, and it type-checks.
 */
describe('the path-taking IPC handlers validate before they use the value', () => {
  // __dirname is dist-test/test at run time; the sources are two levels up.
  const main = readFileSync(join(__dirname, '..', '..', 'src', 'main.ts'), 'utf8');

  it('backend:setLocation takes an unknown and narrows it', () => {
    const start = main.indexOf("ipcMain.handle('backend:setLocation'");
    assert.ok(start >= 0, 'backend:setLocation handler not found');
    const body = main.slice(start, main.indexOf('ipcMain.handle(', start + 1));
    assert.match(body, /\(_e,\s*raw:\s*unknown\)/);
    assert.match(body, /requireOfferedPath\(raw, 'backends',/);
    assert.doesNotMatch(
      body,
      /changeBackendsLocation\(\s*raw\s*\)/,
      'the raw renderer value must never reach changeBackendsLocation',
    );
  });

  it('setup:commit narrows both of its paths before runFirstRunSetup', () => {
    const start = main.indexOf("ipcMain.handle(\n    'setup:commit'");
    assert.ok(start >= 0, 'setup:commit handler not found');
    const body = main.slice(start, main.indexOf("ipcMain.handle('startup:", start));
    assert.match(body, /imageRoot:\s*unknown/, 'imageRoot must not be typed as a string');
    assert.match(body, /installLocation\?:\s*unknown/);
    assert.match(body, /requireOfferedPath\(choices\?\.imageRoot, 'library',/);
    assert.match(body, /requireOfferedPath\(choices\.installLocation, 'backends',/);
    assert.doesNotMatch(
      body,
      /runFirstRunSetup\(\s*choices\s*,/,
      'the unvalidated choices object must never be the one that runs the setup',
    );
  });

  it('every path a handler hands the renderer is recorded with offerPath', () => {
    // The other half of the guard: a dialog result or default that is not
    // offered is refused when it comes back, which breaks the feature rather
    // than opening a hole - but it breaks it silently, so pin it here.
    for (const source of [
      "ipcMain.handle('setup:pickFolder'",
      "ipcMain.handle('backend:pickLocation'",
      "ipcMain.handle('backend:getLocation'",
    ]) {
      const start = main.indexOf(source);
      assert.ok(start >= 0, `${source} not found`);
      const body = main.slice(start, main.indexOf('ipcMain.handle(', start + 1));
      assert.match(body, /offerPath\(/, `${source} must record what it hands out`);
    }
    // The wizard's prefilled defaults, which setup:commit must also accept.
    const probe = main.slice(
      main.indexOf("ipcMain.handle('setup:probe'"),
      main.indexOf("ipcMain.handle('setup:inspect'"),
    );
    for (const field of [
      'existingRoot: offerPath(',
      "newRoot: offerPath(defaultLibraryDir(), 'library')",
      "installLocation: offerPath(backendsRoot(), 'backends')",
    ]) {
      assert.ok(probe.includes(field), `setup:probe must offer ${field}`);
    }
  });

  it('server:setSettings takes an unknown and narrows it', () => {
    const start = main.indexOf("ipcMain.handle('server:setSettings'");
    assert.ok(start >= 0, 'server:setSettings handler not found');
    const body = main.slice(start, main.indexOf('ipcMain.handle(', start + 1));
    assert.match(body, /\(_e,\s*raw:\s*unknown\)/);
    assert.match(body, /writeServerSettings\(requireServerSettings\(raw\)\)/);
  });
});
