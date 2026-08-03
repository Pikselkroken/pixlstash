import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileP = promisify(execFile);

type ExecResult = { stdout: string; stderr: string };
export type ExecRunner = (
  file: string,
  args: string[],
  options: { timeout: number },
) => Promise<ExecResult>;

/**
 * Prepare the backend's one-shot, read-only-validated legacy identity import.
 *
 * Packaged desktop builds do not install a `pixlstash-cli` executable on the
 * host PATH. `python -m pixlstash.cli` is the console script's canonical module
 * entry point inside the bundled runtime and accepts the identical arguments.
 */
export async function prepareLegacyIdentity(
  interpreter: string,
  hubPath: string,
  libraryFolder: string,
  run: ExecRunner = execFileP,
): Promise<string> {
  const args = [
    '-m',
    'pixlstash.cli',
    '--hub',
    hubPath,
    'libraries',
    'prepare-legacy-identity',
    libraryFolder,
  ];

  try {
    const { stdout } = await run(interpreter, args, { timeout: 30_000 });
    const result = stdout.trim();
    if (!result) {
      throw new Error('the identity preparer returned no library identity');
    }
    return result;
  } catch (error) {
    const processError = error as Error & { stderr?: string };
    const detail = processError.stderr?.trim() || processError.message;
    throw new Error(
      `Could not prepare the existing library identity for import: ${detail}`,
    );
  }
}
