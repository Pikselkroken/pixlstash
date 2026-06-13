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
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { homedir, platform } from 'node:os';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

if (platform() !== 'linux') {
  process.exit(0);
}

const here = dirname(fileURLToPath(import.meta.url));
const projectDir = join(here, '..');
// Absolute path to the canonical square icon (stable repo location, always
// present). Cinnamon/GNOME read absolute Icon paths directly — no theme install.
const iconPath = join(projectDir, 'assets', 'icon.png');
const electronBin = join(projectDir, 'node_modules', '.bin', 'electron');

// Electron derives the X11 WM_CLASS / app_id from package.json `desktopName`
// ("pixlstash.desktop" -> "pixlstash"); the .desktop must match it on both the
// filename (app_id) and StartupWMClass for the window→app association.
const WM_CLASS = 'pixlstash';

const appsDir = join(homedir(), '.local', 'share', 'applications');
const desktopFile = join(appsDir, `${WM_CLASS}.desktop`);

if (!existsSync(iconPath)) {
  console.warn(`dev-desktop: icon missing at ${iconPath}; skipping`);
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

mkdirSync(appsDir, { recursive: true });
writeFileSync(desktopFile, contents);
console.log(`dev-desktop: installed ${desktopFile}`);
console.log(`             StartupWMClass=${WM_CLASS}  Icon=${iconPath}`);

// Refresh the app database so the window tracker picks up the new entry. Best
// effort — the directory is monitored live on most DEs, so this just nudges it.
execFile('update-desktop-database', [appsDir], () => {});
