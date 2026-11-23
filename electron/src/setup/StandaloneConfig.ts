import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import { promisify } from 'node:util';

const execFileP = promisify(execFile);

export type ExecRunner = (
  file: string,
  args: string[],
  options: { timeout: number },
) => Promise<{ stdout: string; stderr: string }>;

/**
 * Prints where a standalone (pip/Docker) install keeps its server config.
 *
 * stdout is switched to UTF-8 first because Node decodes it as UTF-8, while a
 * piped Python stdout on Windows uses the ANSI code page: a non-ASCII profile
 * folder (a Cyrillic user name on cp1251) came back garbled and the import
 * offer silently disappeared (#2802).
 */
export const STANDALONE_CONFIG_SCRIPT =
  "import os, sys; from platformdirs import user_config_dir; " +
  "sys.stdout.reconfigure(encoding='utf-8'); " +
  "print(os.path.join(user_config_dir('pixlstash'), 'server-config.json'))";

/**
 * Locate the standalone server config so first-run setup can offer to import
 * its values. Resolved via the backend's own platformdirs so it matches
 * wherever a local install actually keeps it; best-effort (returns null if the
 * bundled interpreter, platformdirs, or the file are unavailable).
 */
export async function standaloneConfigPath(
  interpreter: string,
  run: ExecRunner = execFileP,
): Promise<string | null> {
  try {
    const { stdout } = await run(interpreter, ['-c', STANDALONE_CONFIG_SCRIPT], {
      timeout: 8000,
    });
    const path = stdout.trim();
    return path && existsSync(path) ? path : null;
  } catch (e) {
    console.warn('setup: could not locate a standalone server config:', e);
    return null;
  }
}
