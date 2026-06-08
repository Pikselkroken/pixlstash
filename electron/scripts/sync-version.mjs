// Mirror the single source-of-truth version from ../../pyproject.toml into
// electron/package.json, so the desktop app, the backend wheel and the
// downloadable compute backends all advertise the same version. Run as part of
// `npm run build`. Mirrors what .github/workflows/windows-installer.yml already
// does for the Inno Setup build.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const pyprojectPath = join(here, '..', '..', 'pyproject.toml');
const packagePath = join(here, '..', 'package.json');

const pyproject = readFileSync(pyprojectPath, 'utf8');
// The version lives in the [project] table: `version = "1.6.0"`. Match the
// first top-level version assignment.
const match = pyproject.match(/^\s*version\s*=\s*"([^"]+)"/m);
if (!match) {
  console.error('sync-version: could not find version in pyproject.toml');
  process.exit(1);
}
const pep440 = match[1];

// PEP 440 (Python) versions like "1.6.0.dev1" or "1.6.0rc1" aren't valid semver,
// which electron-builder (>=26) and electron-updater require. Translate the base
// release plus any pre-release (aN/bN/rcN), dev (.devN) and post (.postN) segments
// into their semver spelling, e.g. 1.6.0.dev1 -> 1.6.0-dev.1, 1.6.0rc1 -> 1.6.0-rc.1.
function pep440ToSemver(v) {
  const base = v.match(/^(\d+)\.(\d+)(?:\.(\d+))?(.*)$/);
  if (!base) return v;
  const [, major, minor, patch = '0'] = base;
  let rest = base[4] || '';
  const pre = [];
  const phase = { a: 'alpha', alpha: 'alpha', b: 'beta', beta: 'beta', rc: 'rc', c: 'rc', preview: 'rc', pre: 'rc' };
  let g;
  if ((g = rest.match(/^[._-]?(alpha|a|beta|b|preview|pre|rc|c)[._-]?(\d+)/i))) {
    pre.push(phase[g[1].toLowerCase()], g[2]);
    rest = rest.slice(g[0].length);
  }
  if ((g = rest.match(/^[._-]?dev[._-]?(\d+)/i))) { pre.push('dev', g[1]); rest = rest.slice(g[0].length); }
  if ((g = rest.match(/^[._-]?post[._-]?(\d+)/i))) { pre.push('post', g[1]); rest = rest.slice(g[0].length); }
  return pre.length ? `${major}.${minor}.${patch}-${pre.join('.')}` : `${major}.${minor}.${patch}`;
}

const version = pep440ToSemver(pep440);

const pkg = JSON.parse(readFileSync(packagePath, 'utf8'));
if (pkg.version !== version) {
  pkg.version = version;
  writeFileSync(packagePath, JSON.stringify(pkg, null, 2) + '\n');
  console.log(`sync-version: package.json version -> ${version}${version !== pep440 ? ` (from ${pep440})` : ''}`);
} else {
  console.log(`sync-version: already at ${version}`);
}
