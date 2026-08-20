import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { chmodSync, existsSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  cliCommandHint,
  launcherPath,
  parseCliArgs,
  pathWith,
  pathWithout,
  shimBlocked,
  shimInstalled,
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

  it("turning it ON never overwrites a pixlstash the user wrote themselves", () => {
    // This writes outside the app's own storage, so an occupied path is a
    // refusal, not an overwrite. Reported as "not installed" so the Settings
    // switch cannot sit on over a command that is not ours.
    const path = join(scratch(), 'pixlstash');
    const theirs = '#!/bin/sh\necho my own script\n';
    writeFileSync(path, theirs);

    assert.equal(syncShim(true, '/opt/PixlStash/pixlstash', path), false);
    assert.equal(readFileSync(path, 'utf8'), theirs);
    assert.equal(shimBlocked(path), true);
  });

  it('a path holding our own shim is not "blocked" — that is just a rewrite', () => {
    const path = join(scratch(), 'pixlstash');
    syncShim(true, '/old/PixlStash.AppImage', path);
    assert.equal(shimBlocked(path), false);
    assert.equal(syncShim(true, '/new/PixlStash.AppImage', path), true);
  });

  it('an empty path is not blocked', () => {
    assert.equal(shimBlocked(join(scratch(), 'nothing-here')), false);
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

describe('shimInstalled — reading the state without changing it', () => {
  const scratch = () => mkdtempSync(join(tmpdir(), 'pixlstash-shim-'));

  it('is false with nothing there, and installs nothing by asking', () => {
    const path = join(scratch(), 'bin', 'pixlstash');
    assert.equal(shimInstalled(path), false);
    assert.equal(existsSync(path), false, 'a CLI run must not install a shim');
  });

  it('is true only for a file carrying our marker', () => {
    const dir = scratch();
    const ours = join(dir, 'ours');
    syncShim(true, '/opt/PixlStash/pixlstash', ours);
    assert.equal(shimInstalled(ours), true);

    const theirs = join(dir, 'theirs');
    writeFileSync(theirs, '#!/bin/sh\nexec /somewhere/else "$@"\n');
    assert.equal(shimInstalled(theirs), false);
  });
});

describe('shimPath', () => {
  it('is the XDG per-user bin directory', () => {
    assert.equal(shimPath('/home/me', 'linux'), '/home/me/.local/bin/pixlstash');
  });

  it('is a bin directory of our own on Windows, which has none to borrow', () => {
    const saved = process.env.LOCALAPPDATA;
    process.env.LOCALAPPDATA = join('C:', 'Users', 'me', 'AppData', 'Local');
    try {
      assert.equal(
        shimPath('C:\\Users\\me', 'win32'),
        join('C:', 'Users', 'me', 'AppData', 'Local', 'PixlStash', 'bin', 'pixlstash.cmd'),
      );
    } finally {
      if (saved === undefined) delete process.env.LOCALAPPDATA;
      else process.env.LOCALAPPDATA = saved;
    }
  });
});

describe('shimScript — the Windows .cmd', () => {
  const script = () =>
    shimScript('C:\\Program Files\\PixlStash\\resources\\python\\python.exe', 'C:\\Users\\me\\hub.db');

  it('calls the bundled interpreter directly, not the GUI launcher', () => {
    // PixlStash.exe is linked for the GUI subsystem, so no shell waits for it
    // (#1058). python.exe is a console binary, and this is the same argv runCli
    // spawns.
    assert.match(
      script(),
      /^"C:\\Program Files\\PixlStash\\resources\\python\\python\.exe" -m pixlstash\.cli --hub "C:\\Users\\me\\hub\.db" %\*$/m,
    );
  });

  it('forwards every argument and returns the CLI\'s own exit status', () => {
    // 1 refusal / 2 usage / 3 hub unavailable have to survive the wrapper.
    // Anchored on the command line itself: a bare /%\*/ would also be satisfied
    // by the marker comment.
    assert.match(script(), /^"[^"]+python\.exe" .* %\*$/m);
    assert.match(script(), /^exit \/b %ERRORLEVEL%$/m);
  });

  it('names itself, without leaking that into the calling shell', () => {
    // A .cmd runs in the caller's own cmd instance, so an unscoped `set` would
    // stick. Without the variable the CLI's usage lines would print the long
    // interpreter command instead of the word the user just typed.
    const s = script();
    assert.match(s, /^setlocal$/m);
    assert.match(s, /^set "PIXLSTASH_CLI_COMMAND=pixlstash"$/m);
    assert.ok(s.indexOf('setlocal') < s.indexOf('set "PIXLSTASH'));
  });

  it('is marked as ours and uses CRLF, which cmd needs', () => {
    assert.match(script(), /^REM Generated by PixlStash/m);
    assert.ok(script().includes('\r\n'));
    assert.ok(!/[^\r]\n/.test(script()), 'a bare LF is read inconsistently by cmd');
  });

  it('a percent in a path cannot start a batch expansion', () => {
    const s = shimScript('C:\\100%\\python.exe', 'C:\\hub.db');
    assert.match(s, /"C:\\100%%\\python\.exe"/);
  });
});

// The `.cmd` branch is selected by the hub argument rather than by the host
// platform, which is what lets these run on the CI Linux box at all; what they
// prove is the *content* the Windows path would write, not Windows behaviour.
describe('syncShim — the Windows .cmd on disk', () => {
  it('writes the batch form and still refuses a file that is not ours', () => {
    const dir = mkdtempSync(join(tmpdir(), 'pixlstash-shim-'));
    const path = join(dir, 'bin', 'pixlstash.cmd');

    assert.equal(syncShim(true, '/py/python.exe', path, '/hub.db'), true);
    assert.match(readFileSync(path, 'utf8'), /^@echo off/);
    assert.equal(shimInstalled(path), true);

    const theirs = join(dir, 'theirs.cmd');
    writeFileSync(theirs, '@echo off\r\necho mine\r\n');
    assert.equal(syncShim(true, '/py/python.exe', theirs, '/hub.db'), false);
    assert.equal(shimBlocked(theirs), true);
  });
});

describe('pathWith / pathWithout — editing the user PATH', () => {
  const dir = 'C:\\Users\\me\\AppData\\Local\\PixlStash\\bin';

  it('appends once, and reports nothing to do the second time', () => {
    const first = pathWith('C:\\Windows;C:\\Windows\\System32', dir);
    assert.equal(first, `C:\\Windows;C:\\Windows\\System32;${dir}`);
    // Toggling twice must leave exactly one entry, and every later launch must
    // skip the registry write entirely.
    assert.equal(pathWith(first as string, dir), null);
  });

  it('recognises an entry Windows would call the same directory', () => {
    assert.equal(pathWith(`c:\\users\\ME\\appdata\\local\\pixlstash\\bin\\`, dir), null);
  });

  it('an empty or absent PATH becomes just our directory', () => {
    assert.equal(pathWith('', dir), dir);
  });

  it('normalises a trailing separator instead of appending after it', () => {
    // `C:\\Windows;;<dir>` would put an empty element — which Windows reads as
    // "the current directory" — between them, so the trailing run goes.
    assert.equal(pathWith('C:\\Windows;', dir), `C:\\Windows;${dir}`);
    assert.equal(pathWith('C:\\Windows;;;', dir), `C:\\Windows;${dir}`);
  });

  it('keeps an interior empty element, which is not ours to remove', () => {
    assert.equal(pathWithout(`C:\\A;;C:\\B;${dir}`, dir), 'C:\\A;;C:\\B');
  });

  it('removes our element wherever it sits, not only at the end', () => {
    assert.equal(pathWithout(`${dir};C:\\Windows`, dir), 'C:\\Windows');
    assert.equal(pathWithout(`C:\\A;${dir};C:\\B`, dir), 'C:\\A;C:\\B');
    // Enabling twice can never produce this, but a hand-edited PATH can.
    assert.equal(pathWithout(`${dir};C:\\A;${dir}`, dir), 'C:\\A');
  });

  it('keeps %VARS% and every other element byte for byte', () => {
    const value = `%USERPROFILE%\\bin;C:\\Windows;${dir}`;
    assert.equal(pathWithout(value, dir), '%USERPROFILE%\\bin;C:\\Windows');
  });

  it('removes only what we added, and reports when we added nothing', () => {
    assert.equal(pathWithout('C:\\Windows;C:\\Other\\bin', dir), null);
  });

  it('leaves an empty value when we were the only element', () => {
    // The caller deletes the registry value rather than storing "".
    assert.equal(pathWithout(dir, dir), '');
  });
});
