// Linux desktop environments (Cinnamon/GNOME/KDE) show a window's taskbar and
// alt-tab icon by matching its WM_CLASS to an INSTALLED .desktop file and using
// that entry's Icon — they ignore the window's _NET_WM_ICON. The packaged app
// ships such a file (electron-builder generates pixlstash.desktop with
// StartupWMClass=pixlstash), so the installed app is fine. But `npm run dev`
// installs nothing, so the dev window (WM_CLASS=pixlstash, from package.json
// desktopName) matches no entry and shows a blank/generic icon.
//
// This writes a minimal per-user .desktop that maps that WM_CLASS to the app
// icon, giving the dev window a proper icon. Idempotent and Linux-only; a no-op
// elsewhere. Remove ~/.local/share/applications/pixlstash.desktop to undo.
import { copyFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { homedir, platform } from 'node:os';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

if (platform() !== 'linux') {
  process.exit(0);
}

const here = dirname(fileURLToPath(import.meta.url));
const projectDir = join(here, '..');
const iconSrc = join(projectDir, 'assets', 'icon.png');
// The entry has to outlive the checkout that wrote it. Every `npm run dev`
// rewrites this file with the directory it ran from, and a session worktree is
// deleted once its PR merges — so an `Icon=` pointing into the checkout goes
// dangling and the alt-tab icon silently disappears. (It already did: an entry
// written before the repo moved to the container layout kept pointing at a path
// that no longer existed.) Copy the icon to a stable per-user location instead
// and reference that. Cinnamon/GNOME read absolute Icon paths directly, so no
// theme install is involved.
const iconPath = join(homedir(), '.local', 'share', 'icons', 'pixlstash.png');
const electronBin = join(projectDir, 'node_modules', '.bin', 'electron');

// Electron derives the X11 WM_CLASS / app_id from package.json `desktopName`
// ("pixlstash.desktop" -> "pixlstash"); the .desktop must match it on both the
// filename (app_id) and StartupWMClass for the window→app association.
const WM_CLASS = 'pixlstash';

const appsDir = join(homedir(), '.local', 'share', 'applications');
const desktopFile = join(appsDir, `${WM_CLASS}.desktop`);

if (!existsSync(iconSrc)) {
  console.warn(`dev-desktop: icon missing at ${iconSrc}; skipping`);
  process.exit(0);
}

const contents = `[Desktop Entry]
Type=Application
Name=PixlStash (dev)
Comment=Dev window-icon association for the PixlStash desktop app
Icon=${iconPath}
Exec="${electronBin}" "${projectDir}"
Terminal=false
Categories=Graphics;Photography;
StartupWMClass=${WM_CLASS}
`;

// Best effort, like the update-desktop-database nudge below: this is a cosmetic
// nicety, and `npm run dev` chains straight into launching the app. A read-only
// or locked-down home must cost you the icon, never the dev run.
try {
  mkdirSync(dirname(iconPath), { recursive: true });
  copyFileSync(iconSrc, iconPath);
  mkdirSync(appsDir, { recursive: true });
  writeFileSync(desktopFile, contents);
} catch (e) {
  console.warn('dev-desktop: could not install the desktop entry; skipping:', e);
  process.exit(0);
}
console.log(`dev-desktop: installed ${desktopFile}`);
console.log(`             StartupWMClass=${WM_CLASS}  Icon=${iconPath}`);

// Refresh the app database so the window tracker picks up the new entry. Best
// effort — the directory is monitored live on most DEs, so this just nudges it.
execFile('update-desktop-database', [appsDir], () => {});
