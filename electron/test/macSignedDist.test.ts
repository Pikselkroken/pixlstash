import assert from 'node:assert/strict';
import childProcess = require('node:child_process');
import { describe, it } from 'node:test';
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

// scripts/mac-signed-dist.sh: the signed macOS release build that recovers from
// a staple or DMG-detach fault after notarisation (#2634). The real tools only
// exist on a macOS runner with Apple credentials, so these tests put stub
// npm / npx / xcrun / hdiutil first on PATH and assert on what got called.
// Compiled to dist-test/test/, so the script is two levels up from __dirname.
const SCRIPT = resolve(__dirname, '..', '..', 'scripts', 'mac-signed-dist.sh');
const APP = 'release/mac-arm64/PixlStash.app';

interface Scenario {
  /** Exit code of the first `npm run dist`. */
  distExit: number;
  /** Whether that build leaves a packaged .app behind. */
  leavesApp?: boolean;
  /** How many `xcrun stapler staple` calls fail before one succeeds. */
  stapleFailures?: number;
  validateExit?: number;
  /** How many `npx electron-builder --prepackaged` calls fail before one succeeds. */
  rebuildFailures?: number;
  codesignExit?: number;
  spctlExit?: number;
  /** Arguments passed through to the script (and on to electron-builder). */
  args?: string[];
}

interface Outcome {
  status: number | null;
  calls: string[];
  output: string;
  dir: string;
}

// A stub that logs its argv and fails the first $<counter> calls.
function stub(name: string, body: string): string {
  return `#!/usr/bin/env bash
echo "${name} $*" >> "$STUB_LOG"
${body}
`;
}

function failFirst(counterVar: string, limitVar: string): string {
  return `n=$(cat "$STUB_DIR/${counterVar}" 2>/dev/null || echo 0)
echo $((n + 1)) > "$STUB_DIR/${counterVar}"
[ "$n" -ge "\${${limitVar}:-0}" ] || exit 1
exit 0`;
}

function run(scenario: Scenario): Outcome {
  const dir = mkdtempSync(join(tmpdir(), 'mac-signed-dist-'));
  const bin = join(dir, 'bin');
  const work = join(dir, 'electron');
  mkdirSync(bin);
  mkdirSync(join(work, 'release'), { recursive: true });
  writeFileSync(join(work, 'package.json'), JSON.stringify({ build: { productName: 'PixlStash' } }));
  const stubs: Record<string, string> = {
    npm: stub(
      'npm',
      `if [ "\${STUB_LEAVES_APP:-0}" = 1 ]; then mkdir -p "${APP}"; touch release/partial.dmg; fi
exit "\${STUB_DIST_EXIT:-0}"`,
    ),
    // A rebuild that finds a stale DMG from an earlier attempt fails loudly
    // (it could be uploaded); each attempt then leaves a partial DMG behind,
    // as a failed dmgbuild would.
    npx: stub(
      'npx',
      `if [ -e release/partial.dmg ]; then echo "STALE-DMG" >> "$STUB_LOG"; exit 1; fi
touch release/partial.dmg
${failFirst('npx.count', 'STUB_REBUILD_FAILURES')}`,
    ),
    xcrun: stub(
      'xcrun',
      `if [ "$2" = validate ]; then exit "\${STUB_VALIDATE_EXIT:-0}"; fi
${failFirst('staple.count', 'STUB_STAPLE_FAILURES')}`,
    ),
    codesign: stub('codesign', 'exit "${STUB_CODESIGN_EXIT:-0}"'),
    spctl: stub('spctl', 'exit "${STUB_SPCTL_EXIT:-0}"'),
    // `hdiutil info` reports a stuck dmgbuild scratch image (temp dir) and a
    // simulator runtime image that must be left alone.
    hdiutil: stub(
      'hdiutil',
      `if [ "$1" = info ]; then cat <<'INFO'
framework       : 671
================================================
image-path      : /Library/Developer/CoreSimulator/Images/runtime.dmg
shadow-path     : <none>
/dev/disk3	GUID_partition_scheme	
/dev/disk3s1	Apple_APFS	
================================================
image-path      : /private/var/folders/xy/T/tmpabc123/temp.dmg
shadow-path     : <none>
/dev/disk5	GUID_partition_scheme	
/dev/disk5s1	Apple_HFS	/Volumes/PixlStash 2.0.0
INFO
fi
exit 0`,
    ),
  };
  for (const [name, body] of Object.entries(stubs)) {
    writeFileSync(join(bin, name), body);
    chmodSync(join(bin, name), 0o755);
  }
  const log = join(dir, 'calls.log');
  writeFileSync(log, '');
  const result = childProcess.spawnSync('bash', [SCRIPT, ...(scenario.args ?? [])], {
    cwd: work,
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${bin}:${process.env.PATH}`,
      STUB_LOG: log,
      STUB_DIR: dir,
      STUB_DIST_EXIT: String(scenario.distExit),
      STUB_LEAVES_APP: scenario.leavesApp ? '1' : '0',
      STUB_STAPLE_FAILURES: String(scenario.stapleFailures ?? 0),
      STUB_VALIDATE_EXIT: String(scenario.validateExit ?? 0),
      STUB_REBUILD_FAILURES: String(scenario.rebuildFailures ?? 0),
      STUB_CODESIGN_EXIT: String(scenario.codesignExit ?? 0),
      STUB_SPCTL_EXIT: String(scenario.spctlExit ?? 0),
      MAC_DIST_RETRY_BASE_DELAY: '0',
    },
  });
  return {
    status: result.status,
    calls: readFileSync(log, 'utf8').split('\n').filter(Boolean),
    output: `${result.stdout}${result.stderr}`,
    dir: work,
  };
}

const count = (calls: string[], prefix: string) => calls.filter((c) => c.startsWith(prefix)).length;

describe('mac-signed-dist.sh — surviving faults after notarisation', { skip: process.platform === 'win32' }, () => {
  it('a clean build is just the build: nothing stapled or rebuilt', () => {
    const out = run({ distExit: 0, leavesApp: true });
    assert.equal(out.status, 0, out.output);
    assert.deepEqual(out.calls, ['npm run dist -- --publish never']);
  });

  it('a build that failed before packaging the app fails, without retrying anything', () => {
    const out = run({ distExit: 1, leavesApp: false });
    assert.equal(out.status, 1, out.output);
    assert.match(out.output, /No single packaged PixlStash\.app/);
    assert.equal(count(out.calls, 'xcrun'), 0);
    assert.equal(count(out.calls, 'npx'), 0);
  });

  it('a dropped staple is retried, then only the zip/DMG are rebuilt from the notarised app', () => {
    const out = run({ distExit: 1, leavesApp: true, stapleFailures: 2 });
    assert.equal(out.status, 0, out.output);
    assert.equal(count(out.calls, `xcrun stapler staple -v ${APP}`), 3);
    assert.equal(count(out.calls, `xcrun stapler validate ${APP}`), 1);
    assert.deepEqual(
      out.calls.filter((c) => c.startsWith('npx')),
      [`npx --no-install electron-builder --mac --arm64 --publish never --prepackaged ${APP}`],
    );
    assert.equal(count(out.calls, 'npm'), 1, 'the signing+notarising build must not run again');
    assert.match(out.output, /macOS build recovered/);
  });

  it('extra arguments reach both the first build and the rebuild', () => {
    const out = run({ distExit: 1, leavesApp: true, args: ['--config.foo=bar'] });
    assert.equal(out.status, 0, out.output);
    assert.deepEqual(out.calls.filter((c) => c.startsWith('npm')), ['npm run dist -- --publish never --config.foo=bar']);
    assert.ok(out.calls.some((c) => c.startsWith('npx') && c.endsWith(`--prepackaged ${APP} --config.foo=bar`)));
  });

  it('an app whose signature or Gatekeeper check fails is not rebuilt, even when stapled', () => {
    for (const scenario of [{ codesignExit: 1 }, { spctlExit: 3 }]) {
      const out = run({ distExit: 1, leavesApp: true, ...scenario });
      assert.equal(out.status, 1, out.output);
      assert.equal(count(out.calls, 'npx'), 0);
    }
  });

  it('an app that cannot be stapled is never turned into a zip/DMG', () => {
    // A staple only succeeds for a notarised app: if it never does, the app
    // may not be notarised, and shipping a DMG of it would be the bug.
    const out = run({ distExit: 1, leavesApp: true, stapleFailures: 99 });
    assert.equal(out.status, 1, out.output);
    assert.equal(count(out.calls, 'xcrun stapler staple'), 5);
    assert.equal(count(out.calls, 'npx'), 0);
  });

  it('a stapled app whose ticket does not validate is not rebuilt', () => {
    const out = run({ distExit: 1, leavesApp: true, validateExit: 1 });
    assert.equal(out.status, 1, out.output);
    assert.equal(count(out.calls, 'npx'), 0);
  });

  it('a DMG detach fault in the rebuild is retried', () => {
    const out = run({ distExit: 1, leavesApp: true, rebuildFailures: 1 });
    assert.equal(out.status, 0, out.output);
    assert.equal(count(out.calls, 'npx'), 2);
    // Before every attempt: the stuck scratch image is force-detached (and the
    // simulator image is not), and the partial DMG of the last attempt removed.
    const detaches = out.calls.filter((c) => c.startsWith('hdiutil detach'));
    assert.deepEqual(detaches, ['hdiutil detach -force /dev/disk5', 'hdiutil detach -force /dev/disk5']);
    const order = out.calls.filter((c) => c.startsWith('hdiutil detach') || c.startsWith('npx'));
    assert.deepEqual(order.map((c) => c.split(' ')[0]), ['hdiutil', 'npx', 'hdiutil', 'npx']);
    // The npx stub logs STALE-DMG (and fails) if a leftover DMG was not removed.
    assert.equal(count(out.calls, 'STALE-DMG'), 0);
  });

  it('a rebuild that keeps failing gives up after three attempts', () => {
    const out = run({ distExit: 1, leavesApp: true, rebuildFailures: 99 });
    assert.equal(out.status, 1, out.output);
    assert.equal(count(out.calls, 'npx'), 3);
  });
});
