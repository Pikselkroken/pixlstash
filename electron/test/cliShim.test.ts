import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { chmodSync, existsSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  cliCommandHint,
  launcherPath,
  parseCliArgs,
  shimPath,
  shimScript,
  syncShim,
} from '../src/cliShim';

describe('parseCliArgs — deciding between a window and a CLI run', () => {
  it('a packaged launch with no arguments opens a window', () => {
    assert.equal(parseCliArgs(['/opt/PixlStash/pixlstash']), null);
  });

  it('everything after the marker belongs to the Python CLI', () => {
    const argv = ['/opt/PixlStash/pixlstash', 'cli', 'libraries', 'attach', '/photos'];
    assert.deepEqual(parseCliArgs(argv), ['libraries', 'attach', '/photos']);
  });

  it('a bare marker is a CLI run with no arguments, not a windowed launch', () => {
    // argparse prints usage and exits 2 — the right answer for `pixlstash`
    // with nothing after it, and NOT a silent extra window.
    assert.deepEqual(parseCliArgs(['/opt/PixlStash/pixlstash', 'cli']), []);
  });

  it('the dev launch (electron . cli …) parses like the packaged one', () => {
    assert.deepEqual(parseCliArgs(['/n_m/electron', '.', 'cli', 'plugins', 'list']), [
      'plugins',
      'list',
    ]);
  });

  it('an executable path that happens to be named cli is not the marker', () => {
    // Searched from index 1, so argv[0] can never trigger a CLI run.
    assert.equal(parseCliArgs(['/usr/local/bin/cli']), null);
  });
});

describe('launcherPath — the path a shell can still run tomorrow', () => {
  it('an AppImage points at the .AppImage file, not the throwaway mount', () => {
    const launcher = launcherPath(
      { APPIMAGE: '/home/me/Apps/PixlStash.AppImage' },
      '/tmp/.mount_PixlSt7Fq2xw/pixlstash',
    );
    assert.equal(launcher, '/home/me/Apps/PixlStash.AppImage');
    assert.ok(!launcher.includes('.mount_'), 'the mount path is gone once the app exits');
  });

  it('every other install has a durable execPath', () => {
    assert.equal(launcherPath({}, '/opt/PixlStash/pixlstash'), '/opt/PixlStash/pixlstash');
  });
});

describe('shimScript — the generated shell command', () => {
  it('execs the launcher with the marker so exit codes survive', () => {
    const script = shimScript('/home/me/Apps/PixlStash.AppImage');
    assert.match(script, /^#!\/bin\/sh\n/);
    assert.match(script, /^exec '\/home\/me\/Apps\/PixlStash\.AppImage' cli "\$@"$/m);
  });

  it('a launcher path with a quote or a space cannot break out of the quoting', () => {
    const script = shimScript("/home/me/My Apps/it's here/PixlStash.AppImage");
    assert.match(script, /exec '\/home\/me\/My Apps\/it'\\''s here\/PixlStash\.AppImage' cli/);
  });
});

describe('cliCommandHint — what Settings tells the user to type', () => {
  it('names the short command once the shim is installed', () => {
    assert.equal(cliCommandHint(true, '/home/me/Apps/PixlStash.AppImage'), 'pixlstash');
  });

  it('without the shim it still names a command that runs, not a bare script name', () => {
    // The console script inside the app image is on no PATH, so naming it would
    // print a command that fails. The launcher always works.
    const hint = cliCommandHint(false, '/home/me/Apps/PixlStash.AppImage');
    assert.equal(hint, "'/home/me/Apps/PixlStash.AppImage' cli");
  });
});

describe('syncShim — installing and removing the shell command', () => {
  const scratch = () => mkdtempSync(join(tmpdir(), 'pixlstash-shim-'));

  it('installs an executable script and repoints it when the app moves', () => {
    const path = join(scratch(), 'bin', 'pixlstash');

    assert.equal(syncShim(true, '/old/PixlStash.AppImage', path), true);
    assert.ok(readFileSync(path, 'utf8').includes('/old/PixlStash.AppImage'));
    assert.equal(statSync(path).mode & 0o777, 0o755);

    // Rewritten every launch, which is what repairs a moved or renamed AppImage.
    assert.equal(syncShim(true, '/new/PixlStash.AppImage', path), true);
    const script = readFileSync(path, 'utf8');
    assert.ok(script.includes('/new/PixlStash.AppImage'));
    assert.ok(!script.includes('/old/'));
  });

  it('turning it off removes our script', () => {
    const path = join(scratch(), 'bin', 'pixlstash');
    syncShim(true, '/opt/PixlStash/pixlstash', path);

    assert.equal(syncShim(false, '/opt/PixlStash/pixlstash', path), false);
    assert.equal(existsSync(path), false);
  });

  it("turning it off leaves a pixlstash the user wrote themselves alone", () => {
    const path = join(scratch(), 'unrelated-pixlstash');
    writeFileSync(path, '#!/bin/sh\necho my own script\n');

    assert.equal(syncShim(false, '/opt/PixlStash/pixlstash', path), false);
    assert.equal(readFileSync(path, 'utf8'), '#!/bin/sh\necho my own script\n');
  });

  it('an unwritable home reports "not installed" instead of blocking startup', () => {
    const readOnly = scratch();
    chmodSync(readOnly, 0o500);
    try {
      assert.equal(syncShim(true, '/opt/PixlStash/pixlstash', join(readOnly, 'bin', 'pixlstash')), false);
    } finally {
      chmodSync(readOnly, 0o700);
    }
  });
});

describe('shimPath', () => {
  it('is the XDG per-user bin directory', () => {
    assert.equal(shimPath('/home/me'), '/home/me/.local/bin/pixlstash');
  });
});
