import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  parseUnusableVaultReport,
  vaultRecoveryDialogDetail,
  VaultUnusableError,
  VAULT_UNUSABLE_PREFIX,
} from '../src/backend/VaultRecovery';
import { startupFailureError } from '../src/backend/ServerProcess';
import { PermissionRepairRequiredError, PERMISSION_REPAIR_PREFIX } from '../src/backend/StartupPermissions';

const report = {
  version: 1 as const,
  folder: '/home/me/images',
  vault_path: '/home/me/images/vault.db',
  reason: '/home/me/images/vault.db does not look like a PixlStash vault (missing alembic_version).',
};

describe('the unopenable-library startup protocol', () => {
  it('parses the backend record out of ordinary diagnostic output', () => {
    const output = [
      'CUDA is unavailable; forcing CPU inference.',
      'PixlStash could not open the library database in /home/me/images:',
      `${VAULT_UNUSABLE_PREFIX}${JSON.stringify(report)}`,
    ].join('\n');

    assert.deepEqual(parseUnusableVaultReport(output), report);
  });

  it('turns the record into a typed error instead of a raw exit message', () => {
    const output = `${VAULT_UNUSABLE_PREFIX}${JSON.stringify(report)}`;
    const error = startupFailureError(1, null, output);

    assert.ok(error instanceof VaultUnusableError, 'the shell can only offer what it can recognise');
    assert.deepEqual(error.report, report);
  });

  it('leaves a permission repair as a permission repair', () => {
    // Both records can be in one tail (a repaired launch that then found a bad
    // vault). The permission repair has to run first: without it the backend
    // never gets far enough to say anything about the database.
    const output = [
      `${PERMISSION_REPAIR_PREFIX}${JSON.stringify({
        version: 1,
        issues: [
          { area: 'Library', path: '/home/me/images', current_mode: '775', repaired_mode: '700' },
        ],
      })}`,
      `${VAULT_UNUSABLE_PREFIX}${JSON.stringify(report)}`,
    ].join('\n');

    assert.ok(startupFailureError(1, null, output) instanceof PermissionRepairRequiredError);
  });

  it('refuses malformed records rather than offering to touch a database', () => {
    assert.equal(parseUnusableVaultReport(`${VAULT_UNUSABLE_PREFIX}{oops`), null);
    assert.equal(
      parseUnusableVaultReport(`${VAULT_UNUSABLE_PREFIX}${JSON.stringify({ ...report, version: 2 })}`),
      null,
    );
    assert.equal(
      parseUnusableVaultReport(`${VAULT_UNUSABLE_PREFIX}${JSON.stringify({ ...report, vault_path: '' })}`),
      null,
    );
    assert.equal(parseUnusableVaultReport('nothing structured here at all'), null);
  });

  it('takes the last record, so a retry describes the retry', () => {
    const older = { ...report, vault_path: '/home/me/old/vault.db' };
    const output = [
      `${VAULT_UNUSABLE_PREFIX}${JSON.stringify(older)}`,
      `${VAULT_UNUSABLE_PREFIX}${JSON.stringify(report)}`,
    ].join('\n');

    assert.equal(parseUnusableVaultReport(output)?.vault_path, report.vault_path);
  });
});

describe('what the recovery dialog says', () => {
  it('names the file, the reason, and what starting over costs', () => {
    const detail = vaultRecoveryDialogDetail(report);

    assert.match(detail, /vault\.db/);
    assert.match(detail, /alembic_version/, 'the backend reason is shown, not paraphrased away');
    assert.match(detail, /pictures are untouched/i);
    assert.match(detail, /tags, scores, characters and history/i);
  });

  it('promises the old file survives, because that is the line that stops a manual delete', () => {
    assert.match(vaultRecoveryDialogDetail(report), /renamed, never deleted/i);
  });
});
