/** Structured startup protocol for POSIX permission recovery. */

import { chmodSync, existsSync, mkdirSync } from 'node:fs';

export const PERMISSION_REPAIR_PREFIX = 'PIXLSTASH_PERMISSION_REPAIR=';

export interface PermissionRepairIssue {
  area: string;
  path: string;
  current_mode: string;
  repaired_mode: string;
}

export interface PermissionRepairRequest {
  version: 1;
  issues: PermissionRepairIssue[];
}

/** A backend startup failure the desktop shell can safely offer to repair. */
export class PermissionRepairRequiredError extends Error {
  constructor(public readonly request: PermissionRepairRequest) {
    super('PixlStash needs safer file permissions before it can start.');
    this.name = 'PermissionRepairRequiredError';
  }
}

function isIssue(value: unknown): value is PermissionRepairIssue {
  if (!value || typeof value !== 'object') return false;
  const issue = value as Record<string, unknown>;
  return (
    typeof issue.area === 'string' &&
    typeof issue.path === 'string' &&
    issue.path.length > 0 &&
    issue.path.length <= 4096 &&
    typeof issue.current_mode === 'string' &&
    /^[0-7]{3,4}$/.test(issue.current_mode) &&
    typeof issue.repaired_mode === 'string' &&
    /^[0-7]{3,4}$/.test(issue.repaired_mode)
  );
}

/** Parse the last valid repair record from the backend's bounded output tail. */
export function parsePermissionRepairRequest(output: string): PermissionRepairRequest | null {
  const records = output
    .split(/\r?\n/)
    .filter((line) => line.startsWith(PERMISSION_REPAIR_PREFIX));
  for (const line of records.reverse()) {
    try {
      const value = JSON.parse(line.slice(PERMISSION_REPAIR_PREFIX.length)) as Record<
        string,
        unknown
      >;
      if (
        value.version === 1 &&
        Array.isArray(value.issues) &&
        value.issues.length > 0 &&
        value.issues.length <= 64 &&
        value.issues.every(isIssue)
      ) {
        return value as unknown as PermissionRepairRequest;
      }
    } catch {
      // Malformed diagnostic output never authorises a repair retry.
    }
  }
  return null;
}

/** Native-dialog detail: name the risk, every target, and the exact change. */
export function permissionRepairDialogDetail(request: PermissionRepairRequest): string {
  const paths = request.issues.map(
    (issue) =>
      `${issue.area}:\n${issue.path}\nPermissions ${issue.current_mode} → ${issue.repaired_mode}`,
  );
  return (
    'Other users on this computer can read private credentials or modify a database. ' +
    'PixlStash will not open these files until their permissions are safer.\n\n' +
    paths.join('\n\n') +
    '\n\nFix permissions now?'
  );
}

export function isPermissionRepairRequired(
  error: unknown,
): error is PermissionRepairRequiredError {
  return error instanceof PermissionRepairRequiredError;
}

/** Create an app-owned credential directory privately; never alter an existing one. */
export function mkdirPrivateIfMissing(path: string): boolean {
  const existed = existsSync(path);
  mkdirSync(path, { recursive: true, mode: 0o700 });
  if (!existed && process.platform !== 'win32') {
    // mkdir's requested mode is still filtered by the process umask.
    chmodSync(path, 0o700);
  }
  return !existed;
}
