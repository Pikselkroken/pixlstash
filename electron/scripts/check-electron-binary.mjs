// Preflight for `npm run dev` / `npm run start`.
//
// Electron's npm package records the path to its extracted binary in
// node_modules/electron/path.txt. When that file is missing, `electron .`
// dies with a cryptic `ENOENT ... path.txt` that gives the user no idea what
// to do. The usual cause here: electron's postinstall downloads the binary
// zip fine, but its bundled extract-zip stalls partway under newer Node
// versions, so the binary is never extracted and path.txt is never written.
//
// This script turns that cryptic crash into an actionable warning, and points
// at the already-downloaded zip so recovery is a single command.
import { existsSync, readdirSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const electronDir = join(here, '..', 'node_modules', 'electron');
const pathFile = join(electronDir, 'path.txt');

if (existsSync(pathFile)) {
  process.exit(0); // binary is in place — nothing to warn about
}

// Try to locate the already-downloaded zip in the @electron/get cache so we can
// hand the user an exact recovery command instead of a vague "reinstall".
function findCachedZip() {
  const cacheRoot = process.env.electron_config_cache || join(homedir(), '.cache', 'electron');
  if (!existsSync(cacheRoot)) return null;
  try {
    for (const entry of readdirSync(cacheRoot)) {
      const dir = join(cacheRoot, entry);
      if (!statSync(dir).isDirectory()) continue;
      const zip = readdirSync(dir).find((f) => /^electron-.*\.zip$/.test(f));
      if (zip) return join(dir, zip);
    }
  } catch {
    return null;
  }
  return null;
}

const distDir = join(electronDir, 'dist');
const cachedZip = findCachedZip();

const lines = [
  '',
  'check-electron: Electron binary is not installed (node_modules/electron/path.txt is missing).',
  'check-electron: This usually means electron\'s postinstall extraction stalled (a known',
  'check-electron: extract-zip issue on newer Node versions). The binary was likely downloaded',
  'check-electron: but never unpacked, so `electron .` would crash with `ENOENT ... path.txt`.',
  '',
  'check-electron: To fix it, do ONE of the following:',
];

if (cachedZip) {
  lines.push(
    'check-electron:   A) Extract the already-downloaded zip (fastest, no re-download):',
    `check-electron:        rm -rf "${distDir}"`,
    `check-electron:        unzip -q "${cachedZip}" -d "${distDir}"`,
    `check-electron:        printf 'electron' > "${pathFile}"`,
    'check-electron:        chmod +x "' + join(distDir, 'electron') + '"',
  );
} else {
  lines.push(
    'check-electron:   A) Re-run the electron install to download and extract the binary:',
    'check-electron:        npm rebuild electron',
  );
}

lines.push(
  'check-electron:   B) Reinstall under a Node LTS (20/22), where extract-zip does not stall:',
  'check-electron:        rm -rf node_modules/electron && npm install',
  '',
);

console.error(lines.join('\n'));
process.exit(1);
