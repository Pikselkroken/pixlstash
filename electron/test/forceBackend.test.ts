import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { parseForcedBackend, isAccel, ACCEL_VALUES } from '../src/config';

/**
 * Capture the warnings parseForcedBackend emits so tests can assert the
 * invalid-value path logs rather than silently accepting bad input.
 */
function collectWarnings(): { warn: (m: string) => void; messages: string[] } {
  const messages: string[] = [];
  return { warn: (m: string) => messages.push(m), messages };
}

describe('isAccel', () => {
  it('accepts every Accel enum member', () => {
    for (const a of ACCEL_VALUES) assert.ok(isAccel(a), `${a} should be a valid Accel`);
  });

  it('rejects anything outside the enum', () => {
    for (const bad of ['', 'gpu', 'CPU', 'cuda', 'cu129', 'rocm7.1', 'metal ', '../etc']) {
      assert.equal(isAccel(bad), false, `${JSON.stringify(bad)} must not be a valid Accel`);
    }
    assert.equal(isAccel(undefined), false);
    assert.equal(isAccel(123), false);
  });
});

describe('parseForcedBackend', () => {
  it('returns null when neither argv flag nor env var is set', () => {
    const { warn, messages } = collectWarnings();
    assert.equal(parseForcedBackend(['electron', 'main.js'], {}, warn), null);
    assert.equal(messages.length, 0);
  });

  it('accepts a valid --force-backend=<accel> argv flag', () => {
    for (const a of ACCEL_VALUES) {
      assert.equal(parseForcedBackend([`--force-backend=${a}`], {}, () => {}), a);
    }
  });

  it('accepts a valid PIXLSTASH_FORCE_BACKEND env var', () => {
    assert.equal(parseForcedBackend([], { PIXLSTASH_FORCE_BACKEND: 'rocm' }, () => {}), 'rocm');
  });

  it('lets argv win over the env var when both are set', () => {
    const accel = parseForcedBackend(
      ['--force-backend=cu128'],
      { PIXLSTASH_FORCE_BACKEND: 'rocm' },
      () => {},
    );
    assert.equal(accel, 'cu128');
  });

  it('ignores an invalid argv value and warns', () => {
    const { warn, messages } = collectWarnings();
    assert.equal(parseForcedBackend(['--force-backend=evil-url'], {}, warn), null);
    assert.equal(messages.length, 1);
    assert.match(messages[0], /ignoring invalid backend override 'evil-url'/);
  });

  it('ignores an invalid env value and warns', () => {
    const { warn, messages } = collectWarnings();
    assert.equal(parseForcedBackend([], { PIXLSTASH_FORCE_BACKEND: 'cuda' }, warn), null);
    assert.equal(messages.length, 1);
    assert.match(messages[0], /ignoring invalid backend override 'cuda'/);
  });

  it('does not treat an empty --force-backend= as a value', () => {
    const { warn, messages } = collectWarnings();
    assert.equal(parseForcedBackend(['--force-backend='], {}, warn), null);
    assert.equal(messages.length, 0);
  });

  it('never returns an index URL or arbitrary string (security: enum-only)', () => {
    // The classic abuse: try to smuggle a wheel index through the flag.
    const attempts = [
      '--force-backend=https://evil.example/whl',
      '--force-backend=cu128 --index-url=https://evil',
      '--force-backend=../../../../etc/passwd',
    ];
    for (const a of attempts) {
      const result = parseForcedBackend([a], {}, () => {});
      assert.ok(result === null || ACCEL_VALUES.includes(result));
    }
  });
});
