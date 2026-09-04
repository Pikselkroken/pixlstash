/** Structured startup protocol for a library database that will not open. */

export const VAULT_UNUSABLE_PREFIX = 'PIXLSTASH_VAULT_UNUSABLE=';

/** What the backend found, and where. */
export interface UnusableVaultReport {
  version: 1;
  /** The library folder the app is configured to open. */
  folder: string;
  /** The `vault.db` inside it that could not be opened. */
  vault_path: string;
  /** The backend's own one-line reason, shown verbatim. */
  reason: string;
}

/**
 * A backend startup failure the desktop shell can offer a way out of.
 *
 * Distinct from a bare startup crash because there is exactly one thing a
 * person can do about it, and the app knows what it is: start over with an
 * empty database, keeping the old file. Without this the backend exited 1 and
 * the shell showed a Python traceback over the setup window, which is how a
 * December-2025 library folder read as "the app is broken" - and how the file
 * that would have opened after an upgrade got deleted by hand.
 */
export class VaultUnusableError extends Error {
  constructor(public readonly report: UnusableVaultReport) {
    super('PixlStash could not open the library database.');
    this.name = 'VaultUnusableError';
  }
}

function isReport(value: unknown): value is UnusableVaultReport {
  if (!value || typeof value !== 'object') return false;
  const report = value as Record<string, unknown>;
  return (
    report.version === 1 &&
    typeof report.folder === 'string' &&
    report.folder.length > 0 &&
    report.folder.length <= 4096 &&
    typeof report.vault_path === 'string' &&
    report.vault_path.length > 0 &&
    report.vault_path.length <= 4096 &&
    typeof report.reason === 'string' &&
    report.reason.length <= 4096
  );
}

/** Parse the last valid record from the backend's bounded output tail. */
export function parseUnusableVaultReport(output: string): UnusableVaultReport | null {
  const records = output.split(/\r?\n/).filter((line) => line.startsWith(VAULT_UNUSABLE_PREFIX));
  for (const line of records.reverse()) {
    try {
      const value = JSON.parse(line.slice(VAULT_UNUSABLE_PREFIX.length)) as unknown;
      if (isReport(value)) return value;
    } catch {
      // Malformed diagnostic output never authorises touching a database.
    }
  }
  return null;
}

/**
 * Native-dialog detail: what failed, what the offer costs, and that the old
 * file survives. The last line is the one that matters - somebody who believes
 * the app is about to delete their library will go and delete it themselves.
 */
export function vaultRecoveryDialogDetail(report: UnusableVaultReport): string {
  return (
    `${report.vault_path}\n${report.reason}\n\n` +
    'PixlStash can start over with a new, empty library database in that folder. ' +
    'Your pictures are untouched and will be offered for import again, but the tags, ' +
    'scores, characters and history recorded in the old database are not carried over.\n\n' +
    'The old file is renamed, never deleted, so it can still be recovered.'
  );
}

export function isVaultUnusable(error: unknown): error is VaultUnusableError {
  return error instanceof VaultUnusableError;
}
