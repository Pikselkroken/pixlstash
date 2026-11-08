/**
 * Structured startup protocol for an active library that will not open.
 *
 * The backend refuses to open a library whose folder or `vault.db` is gone,
 * unreadable, or another library (`prevalidate_library_fingerprint`), and it is
 * right to: opening it would create an empty database where the real one used
 * to be. But the only place to change the active library was Settings, which
 * needs the server this refusal stops, so the app dead-ended on a process-exit
 * message. The backend now prints a one-line record instead, and the shell
 * turns it into a recovery screen with a way out.
 */
import { execFile } from 'node:child_process';
import { posix, win32 } from 'node:path';
import { promisify } from 'node:util';

export const LIBRARY_UNAVAILABLE_PREFIX = 'PIXLSTASH_LIBRARY_UNAVAILABLE=';

export type LibraryUnavailableKind = 'folder_missing' | 'vault_missing' | 'unreadable' | 'foreign';

const KINDS: readonly LibraryUnavailableKind[] = [
  'folder_missing',
  'vault_missing',
  'unreadable',
  'foreign',
];

/** One registered library, as the backend names it. */
export interface LibraryRef {
  id: number;
  uuid: string;
  name: string;
  path: string;
}

/** What the backend found, and which other libraries do still open. */
export interface LibraryUnavailableReport {
  version: 1;
  kind: LibraryUnavailableKind;
  library: LibraryRef;
  vault_path: string;
  /** The backend's own one-line reason, shown verbatim as the detail. */
  reason: string;
  /** Attached libraries that passed the same read-only check. */
  alternatives: LibraryRef[];
}

/** A backend startup failure the recovery screen can offer a way out of. */
export class LibraryUnavailableError extends Error {
  constructor(public readonly report: LibraryUnavailableReport) {
    // The backend's reason travels in the message too, for the launch paths
    // (first-run setup, a runtime switch) that show it rather than the screen.
    super(`PixlStash could not open the library "${report.library.name}": ${report.reason}`);
    this.name = 'LibraryUnavailableError';
  }
}

export function isLibraryUnavailable(error: unknown): error is LibraryUnavailableError {
  return error instanceof LibraryUnavailableError;
}

const MAX_TEXT = 4096;

function isText(value: unknown, allowEmpty = false): value is string {
  return typeof value === 'string' && value.length <= MAX_TEXT && (allowEmpty || value.length > 0);
}

function isLibraryRef(value: unknown): value is LibraryRef {
  if (!value || typeof value !== 'object') return false;
  const ref = value as Record<string, unknown>;
  return (
    Number.isInteger(ref.id) &&
    isText(ref.uuid) &&
    isText(ref.name) &&
    isText(ref.path)
  );
}

function isReport(value: unknown): value is LibraryUnavailableReport {
  if (!value || typeof value !== 'object') return false;
  const report = value as Record<string, unknown>;
  return (
    report.version === 1 &&
    KINDS.includes(report.kind as LibraryUnavailableKind) &&
    isLibraryRef(report.library) &&
    isText(report.vault_path) &&
    isText(report.reason, true) &&
    Array.isArray(report.alternatives) &&
    report.alternatives.length <= 64 &&
    report.alternatives.every(isLibraryRef)
  );
}

/** Parse the last valid record from the backend's bounded output tail. */
export function parseLibraryUnavailableReport(output: string): LibraryUnavailableReport | null {
  const records = output
    .split(/\r?\n/)
    .filter((line) => line.startsWith(LIBRARY_UNAVAILABLE_PREFIX));
  for (const line of records.reverse()) {
    try {
      const value = JSON.parse(line.slice(LIBRARY_UNAVAILABLE_PREFIX.length)) as unknown;
      if (isReport(value)) return value;
    } catch {
      // A malformed record is not a recovery offer; the generic error stands.
    }
  }
  return null;
}

/**
 * True when *path* is *folder* or somewhere beneath it.
 *
 * A new library must never be started there: an empty `vault.db` where the
 * missing library belongs would hide the real one once its drive comes back,
 * which is exactly what the backend's fingerprint check exists to prevent. The
 * backend refuses it too; this is so the screen can say so before a relaunch.
 */
export function isSameOrInside(path: string, folder: string, platform = process.platform): boolean {
  // The platform's own path rules, not the host's: a test can then exercise
  // all three, and the comparison never depends on where it happens to run.
  const paths = platform === 'win32' ? win32 : posix;
  const fold = (p: string) => (platform === 'win32' || platform === 'darwin' ? p.toLowerCase() : p);
  const rel = paths.relative(fold(paths.resolve(folder)), fold(paths.resolve(path)));
  return rel === '' || (!rel.startsWith('..') && !paths.isAbsolute(rel));
}

/** `server-config.json` with `image_root` pointed at *imageRoot*, everything else kept. */
export function withImageRoot(configText: string, imageRoot: string): string {
  const parsed = JSON.parse(configText) as unknown;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('The server configuration is not a JSON object.');
  }
  return JSON.stringify({ ...(parsed as Record<string, unknown>), image_root: imageRoot }, null, 2);
}

type ExecResult = { stdout: string; stderr: string };
export type ExecRunner = (
  file: string,
  args: string[],
  options: { timeout: number },
) => Promise<ExecResult>;

const execFileP = promisify(execFile);

/** The CLI's own refusal, without its `error:` prefix, for the screen. */
function cliFailure(error: unknown): Error {
  const processError = error as Error & { stderr?: string };
  const detail = (processError.stderr?.trim() || processError.message || String(error))
    .split(/\r?\n/)
    .map((line) => line.replace(/^error:\s*/, ''))
    .join('\n')
    .trim();
  return new Error(detail);
}

async function runLibraryVerb(
  interpreter: string,
  hubPath: string,
  verb: string[],
  run: ExecRunner,
): Promise<void> {
  // The packaged app puts no `pixlstash-cli` on PATH; `-m pixlstash.cli` is
  // the same entry point inside the bundled runtime (see LegacyIdentityPreparation).
  const args = ['-m', 'pixlstash.cli', '--hub', hubPath, 'libraries', ...verb];
  try {
    await run(interpreter, args, { timeout: 30_000 });
  } catch (error) {
    throw cliFailure(error);
  }
}

/**
 * "I found it": point the registration at where the library lives now.
 *
 * `--same-library` makes the CLI refuse a folder holding any other library, so
 * picking the wrong folder cannot hand this library's share links to it.
 */
export function relocateLibrary(
  interpreter: string,
  hubPath: string,
  library: LibraryRef,
  folder: string,
  run: ExecRunner = execFileP,
): Promise<void> {
  return runLibraryVerb(
    interpreter,
    hubPath,
    ['relocate', library.uuid, folder, '--same-library'],
    run,
  );
}

/** Make another attached library the one that opens. */
export function activateLibrary(
  interpreter: string,
  hubPath: string,
  library: LibraryRef,
  run: ExecRunner = execFileP,
): Promise<void> {
  return runLibraryVerb(interpreter, hubPath, ['activate', library.uuid], run);
}
