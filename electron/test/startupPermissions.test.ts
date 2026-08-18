import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { mkdtempSync, mkdirSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  mkdirPrivateIfMissing,
  PERMISSION_REPAIR_PREFIX,
  parsePermissionRepairRequest,
  permissionRepairDialogDetail,
} from '../src/backend/StartupPermissions';
import { startupFailureError } from '../src/backend/ServerProcess';
import { PermissionRepairRequiredError } from '../src/backend/StartupPermissions';

const issue = {
  area: 'Library',
  path: '/home/alex/Pictures/a very long library path',
  current_mode: '775',
  repaired_mode: '755',
};

describe('permission repair startup protocol', () => {
  it('parses the backend record out of ordinary diagnostic output', () => {
    const output = [
      'CUDA is unavailable; forcing CPU inference.',
      `${PERMISSION_REPAIR_PREFIX}${JSON.stringify({ version: 1, issues: [issue] })}`,
    ].join('\n');
    assert.deepEqual(parsePermissionRepairRequest(output), { version: 1, issues: [issue] });
    const error = startupFailureError(1, null, output);
    assert.ok(error instanceof PermissionRepairRequiredError);
    assert.deepEqual(error.request, { version: 1, issues: [issue] });
  });

  it('rejects malformed or empty records instead of authorising repair', () => {
    assert.equal(parsePermissionRepairRequest(`${PERMISSION_REPAIR_PREFIX}{oops`), null);
    assert.equal(
      parsePermissionRepairRequest(
        `${PERMISSION_REPAIR_PREFIX}${JSON.stringify({ version: 1, issues: [] })}`,
      ),
      null,
    );
    assert.equal(
      parsePermissionRepairRequest(
        `${PERMISSION_REPAIR_PREFIX}${JSON.stringify({
          version: 1,
          issues: [{ ...issue, repaired_mode: 'not-a-mode' }],
        })}`,
      ),
      null,
    );
  });

  it('builds a native-dialog explanation with the risk, path and exact change', () => {
    const detail = permissionRepairDialogDetail({ version: 1, issues: [issue] });
    assert.match(detail, /modify a database/);
    assert.match(detail, /a very long library path/);
    assert.match(detail, /775 → 755/);
    assert.match(detail, /Fix permissions now\?/);
  });

  it('creates a credential directory as 0700 under umask 0002', () => {
    const root = mkdtempSync(join(tmpdir(), 'pixlstash-permissions-'));
    const target = join(root, 'config');
    const old = process.umask(0o002);
    try {
      assert.equal(mkdirPrivateIfMissing(target), true);
    } finally {
      process.umask(old);
    }
    assert.equal(statSync(target).mode & 0o777, 0o700);
    rmSync(root, { recursive: true, force: true });
  });

  it('does not silently tighten an existing directory', () => {
    const root = mkdtempSync(join(tmpdir(), 'pixlstash-permissions-'));
    const target = join(root, 'existing');
    mkdirSync(target, { mode: 0o775 });
    assert.equal(mkdirPrivateIfMissing(target), false);
    assert.equal(statSync(target).mode & 0o777, 0o775);
    rmSync(root, { recursive: true, force: true });
  });
});
