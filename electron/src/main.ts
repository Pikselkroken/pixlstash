import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  nativeImage,
  shell,
  session,
  dialog,
  Tray,
} from 'electron';
import { execFile } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { networkInterfaces } from 'node:os';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { detectHardware, gpuUpgrades, Hardware } from './backend/HardwareDetector';
import { BackendManager, OVERLAY_ACCELS } from './backend/BackendManager';
import { ServerProcess } from './backend/ServerProcess';
import {
  Accel,
  ACCEL_LABELS,
  RuntimeInfo,
  bundledInterpreter,
  defaultLibraryDir,
  isDevBackend,
  overlayDir,
  readRuntimeInfo,
  serverConfigPath,
  serverLogPath,
} from './config';

const execFileP = promisify(execFile);

/** Window/taskbar icon, bundled with the renderer assets so it resolves in both
 * dev and packaged runs. We load the canonical square 1024² icon (copy-assets
 * places it next to the renderer) rather than the non-square brand Logo.png:
 * Linux alt-tab/taskbar switchers expect a square icon and ignore odd ratios.
 * Loaded as a NativeImage and applied via both the constructor option AND an
 * explicit setIcon() — the constructor `icon` alone is unreliable on Linux. */
const APP_ICON_PATH = join(__dirname, 'renderer', 'icon.png');

/** The packaged renderer directory — the ONLY `file://` location we ever load
 * (splash index.html, first-run setup.html, their bundled assets). Used by the
 * navigation guard to pin allowed local navigation to our own files instead of
 * trusting any `file://` URL. Resolved (symlinks/`..` collapsed) and suffixed
 * with the path separator so a sibling dir sharing the prefix can't slip through. */
const RENDERER_DIR = resolve(__dirname, 'renderer') + sep;

function loadAppIcon(): Electron.NativeImage {
  const img = nativeImage.createFromPath(APP_ICON_PATH);
  if (img.isEmpty()) {
    console.warn(`[icon] could not load window icon from ${APP_ICON_PATH}`);
    return img;
  }
  // Downscale the 1024² source to a modest square so _NET_WM_ICON stays small
  // and the alt-tab/taskbar thumbnail renders crisply.
  return img.resize({ width: 256, height: 256, quality: 'best' });
}

const APP_ICON = loadAppIcon();

// Keep the desktop app's data dir distinct from a standalone pip/Docker server's
// (~/.config/pixlstash). Electron's default name ('PixlStash') differs from it
// only by case — fine on Linux, but a COLLISION on case-insensitive filesystems
// (Windows, default macOS). Pin it to 'pixlstash-desktop'. Must run before
// anything reads userData (config paths, the single-instance lock).
app.setPath('userData', join(app.getPath('appData'), 'pixlstash-desktop'));

const manager = new BackendManager();
let serverProcess: ServerProcess | null = null;
let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
// The URL currently loaded in the window, so we can jump straight back to the
// running backend when reopening from the tray (no full re-boot needed).
let currentUrl: string | null = null;
let quitting = false;
// Desktop-shell preference: when true, closing the window hides it to the tray
// (keeping the backend / remote server alive) instead of quitting. Loaded from
// disk at startup and toggled from Settings → Backend.
let hideToTrayOnClose = true;

// Cached during boot so the renderer/backend manager can reuse them.
let hardware: Hardware | null = null;
let runtime: RuntimeInfo | null = null;

function sendPhase(payload: Record<string, unknown>): void {
  mainWindow?.webContents.send('app:phase', payload);
}

/**
 * Decide whether the window may navigate (top-level) to `target`. The privileged
 * `pixlstashDesktop` preload bridge stays injected across same-window navigation,
 * so any off-origin page that loaded here could call high-impact IPC
 * (setServerSettings, commitSetup, installAccelerator, …). We therefore allow
 * ONLY the content we load ourselves and block everything else (deny-by-default):
 *
 *  - `file://` — ONLY files inside our own packaged renderer directory
 *    (renderer/index.html splash, renderer/setup.html, their assets). A blanket
 *    `file:` allow would let a navigated page load any local HTML under the
 *    privileged preload, so we resolve the target path and require it to live
 *    under RENDERER_DIR.
 *  - the live loopback backend origin — http://127.0.0.1:<ephemeral port>. The
 *    port is chosen fresh per backend launch, so the allowed origin is derived
 *    from the URL we actually loaded (`currentUrl`), never hardcoded. Before the
 *    backend is up `currentUrl` is null; we then permit only the loopback host
 *    (127.0.0.1 / localhost over http) so an in-flight load isn't broken, while
 *    still excluding every non-loopback origin.
 */
function isAllowedNavigation(target: string): boolean {
  let url: URL;
  try {
    url = new URL(target);
  } catch (e) {
    console.warn(`[nav] blocking navigation to unparseable URL ${target}:`, e);
    return false;
  }
  // Local bundled pages (splash / setup wizard): allow ONLY our own renderer
  // files, never an arbitrary file:// path (which would still carry the preload).
  if (url.protocol === 'file:') {
    try {
      const path = resolve(fileURLToPath(url));
      return path === RENDERER_DIR.slice(0, -1) || path.startsWith(RENDERER_DIR);
    } catch (e) {
      console.warn(`[nav] blocking unresolvable file:// URL ${target}:`, e);
      return false;
    }
  }
  // The running backend, pinned to the exact loopback origin we loaded.
  if (currentUrl) {
    try {
      if (url.origin === new URL(currentUrl).origin) return true;
    } catch (e) {
      console.warn(`[nav] could not parse current backend URL ${currentUrl}:`, e);
    }
  }
  // Fallback before the backend URL is known: only the loopback host over http.
  if (url.protocol === 'http:' && (url.hostname === '127.0.0.1' || url.hostname === 'localhost')) {
    return true;
  }
  return false;
}

/**
 * Hand a URL to the OS browser/handler, but ONLY for schemes we trust. Passing
 * attacker-influenced URLs to `shell.openExternal` is a known local code-exec /
 * privilege-escalation vector: `file:`, `smb:`, and custom-handler schemes
 * (`vscode:`, `ms-msdt:`, …) can launch local programs or mount remote shares.
 * Outbound links from app content should only ever be plain web/email links, so
 * we allow `https:` and `mailto:` and block (and log) everything else —
 * deny-by-default. Plain `http:` is intentionally excluded for outbound opens:
 * the only legitimate http target here is the loopback backend, which is handled
 * in-app (setWindowOpenHandler 'allow' / isAllowedNavigation), never opened
 * externally. Used by BOTH setWindowOpenHandler and the navigation guard so the
 * scheme policy lives in one place.
 */
function openExternalSafely(url: string): void {
  let protocol: string;
  try {
    ({ protocol } = new URL(url));
  } catch (e) {
    console.warn(`[external] refusing to open unparseable URL ${url}:`, e);
    return;
  }
  if (protocol === 'https:' || protocol === 'mailto:') {
    void shell.openExternal(url);
    return;
  }
  console.warn(`[external] blocked openExternal for disallowed scheme '${protocol}': ${url}`);
}

/** The accelerator the bundled (installer-shipped) runtime provides. */
function bundledAccel(): Accel {
  return runtime?.accel ?? 'cpu';
}

function createMainWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#1b1f24',
    icon: APP_ICON,
    show: true,
    // Frameless so the app draws its own title bar that blends with the toolbar.
    // macOS keeps native traffic lights (positioned over the custom bar); other
    // platforms get fully custom controls drawn by the renderer.
    ...(process.platform === 'darwin'
      ? { titleBarStyle: 'hidden' as const, trafficLightPosition: { x: 12, y: 11 } }
      : { frame: false }),
    webPreferences: {
      preload: join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  // The constructor `icon` option is unreliable on Linux (and a no-op on macOS,
  // which uses the bundled .icns); set it explicitly for Windows/Linux so the
  // alt-tab/taskbar icon actually appears.
  if (process.platform !== 'darwin' && !APP_ICON.isEmpty()) {
    mainWindow.setIcon(APP_ICON);
  }
  mainWindow.loadFile(join(__dirname, 'renderer', 'index.html'));
  // With a tray available, closing the window hides it instead of quitting so
  // the backend (and any remote server) keeps running. A real quit goes through
  // `quitting` (tray Quit / app before-quit), which lets the close proceed.
  mainWindow.on('close', (e) => {
    if (!quitting && tray && hideToTrayOnClose) {
      e.preventDefault();
      mainWindow?.hide();
    }
  });
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
  // Open external links in the user's browser, not inside the app window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://127.0.0.1') || url.startsWith('http://localhost')) {
      return { action: 'allow' };
    }
    openExternalSafely(url);
    return { action: 'deny' };
  });
  // Lock down TOP-LEVEL navigation so the privileged preload bridge can never
  // end up under an untrusted origin. setWindowOpenHandler above only covers
  // window.open / new windows; in-window navigation (link clicks, meta-refresh,
  // window.location, HTTP redirects) is governed here. Anything that isn't our
  // own local content or the live loopback backend is cancelled and, if it's a
  // real external link, handed to the OS browser instead.
  const guardNavigation = (event: Electron.Event, url: string): void => {
    if (isAllowedNavigation(url)) return;
    event.preventDefault();
    console.warn(`[nav] blocked in-window navigation to off-origin URL: ${url}`);
    // Hand a real external link to the OS browser, but only through the scheme
    // allowlist (https/mailto) — never a raw file:/smb:/custom-handler URL.
    openExternalSafely(url);
  };
  mainWindow.webContents.on('will-navigate', guardNavigation);
  mainWindow.webContents.on('will-redirect', guardNavigation);
}

/** Bring the main window to the front, recreating it if it was destroyed. */
function showWindow(): void {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
    return;
  }
  // The window was closed while the app stayed alive in the tray; recreate it
  // and jump straight back to the running backend if we have its URL, otherwise
  // run the normal boot flow. (createMainWindow reassigns the module-level
  // mainWindow, which TS can't see through the null-narrowing above.)
  createMainWindow();
  const win = mainWindow as BrowserWindow | null;
  if (currentUrl) {
    void win?.loadURL(currentUrl);
  } else {
    win?.webContents.once('did-finish-load', () => void boot());
  }
}

/**
 * Create the system-tray icon so the app can keep running — and keep serving the
 * optional remote server — after the window is closed. Returns false when the
 * platform has no usable tray (macOS uses the dock; a GNOME session without
 * AppIndicator support can't show one), in which case the caller keeps the
 * default quit-on-close behavior so the app is never left unreachable.
 */
function createTray(): boolean {
  if (process.platform === 'darwin') return false; // macOS keeps the dock icon
  try {
    tray = new Tray(APP_ICON.isEmpty() ? nativeImage.createEmpty() : APP_ICON);
  } catch (e) {
    console.warn('[tray] could not create a tray icon; keeping quit-on-close:', e);
    tray = null;
    return false;
  }
  tray.setToolTip('PixlStash');
  // On Linux the context menu is the primary interaction (left-click may not
  // emit 'click'), so it must always be set; on Windows click also reopens.
  tray.setContextMenu(buildTrayMenu());
  tray.on('click', () => showWindow());
  return true;
}

/** Build the tray context menu, reflecting the current remote-server state. */
function buildTrayMenu(): Menu {
  const serverEnabled = readServerSettings().enabled;
  return Menu.buildFromTemplate([
    { label: 'Show window', click: () => showWindow() },
    { label: 'Settings…', click: () => openSettings() },
    { type: 'separator' },
    {
      label: 'Enable server',
      type: 'checkbox',
      checked: serverEnabled,
      // Flipping this restarts the backend so the external listener is bound (or
      // dropped); the window reloads onto the new loopback URL, same as Apply.
      click: () => void toggleServerEnabled(!serverEnabled),
    },
    { type: 'separator' },
    {
      label: 'Quit PixlStash',
      click: () => {
        quitting = true;
        app.quit();
      },
    },
  ]);
}

/** Refresh the tray menu so its checkbox states match the current config. */
function refreshTrayMenu(): void {
  if (tray) tray.setContextMenu(buildTrayMenu());
}

/** Toggle the external (remote) server on/off, preserving the port and SSL. */
async function toggleServerEnabled(enabled: boolean): Promise<void> {
  const current = readServerSettings();
  try {
    await writeServerSettings({ enabled, port: current.port, ssl: current.ssl });
  } catch (e) {
    console.warn('[tray] failed to toggle the remote server:', e);
    refreshTrayMenu();
  }
}

/** Reveal the active library (image_root) in the OS file manager. */
function openLibraryFolder(): void {
  const cfg = existsSync(serverConfigPath()) ? readJsonFile(serverConfigPath()) : null;
  const imageRoot = typeof cfg?.image_root === 'string' ? cfg.image_root : null;
  if (!imageRoot || !existsSync(imageRoot)) {
    void dialog.showMessageBox({
      type: 'info',
      message: 'No library folder yet',
      detail: 'Finish first-run setup to choose your library folder.',
    });
    return;
  }
  void shell.openPath(imageRoot);
}

/** The desktop shell's own preferences file (separate from the backend config). */
function desktopPrefsPath(): string {
  return join(app.getPath('userData'), 'desktop-prefs.json');
}

/** Load shell preferences from disk into memory (defaults kept when absent). */
function loadDesktopPrefs(): void {
  const prefs = existsSync(desktopPrefsPath()) ? readJsonFile(desktopPrefsPath()) : null;
  if (prefs && typeof prefs.hideToTrayOnClose === 'boolean') {
    hideToTrayOnClose = prefs.hideToTrayOnClose;
  }
}

/** Persist the current shell preferences to disk. */
function saveDesktopPrefs(): void {
  try {
    mkdirSync(dirname(desktopPrefsPath()), { recursive: true });
    writeFileSync(desktopPrefsPath(), JSON.stringify({ hideToTrayOnClose }, null, 2));
  } catch (e) {
    console.warn('[desktop-prefs] could not persist preferences:', e);
  }
}

/** Bring the window forward and ask the renderer to open the Settings dialog. */
function openSettings(): void {
  showWindow();
  const wc = mainWindow?.webContents;
  if (!wc) return;
  // If the window was just recreated it is still loading; wait for the renderer
  // (which registers the listener) before sending, otherwise fire immediately.
  if (wc.isLoading()) {
    wc.once('did-finish-load', () => wc.send('app:open-settings'));
  } else {
    wc.send('app:open-settings');
  }
}

/** Reveal the current backend log (handy when attaching it to a bug report). */
function showServerLogs(): void {
  const log = serverLogPath();
  if (existsSync(log)) shell.showItemInFolder(log);
  else void shell.openPath(dirname(log));
}

/** Port offered for the external listener when the config has none yet. */
const DEFAULT_EXTERNAL_PORT = 9537;

interface ServerSettings {
  enabled: boolean;
  port: number;
  ssl: boolean;
}

/** This machine's non-loopback IPv4 addresses, for showing reachable URLs. */
function lanAddresses(): string[] {
  const out: string[] = [];
  for (const addrs of Object.values(networkInterfaces())) {
    for (const addr of addrs ?? []) {
      if (addr.family === 'IPv4' && !addr.internal) out.push(addr.address);
    }
  }
  return out;
}

/** Read the backend's external-listener settings (defaults when absent). */
function readServerSettings(): ServerSettings & { urls: string[] } {
  const configPath = serverConfigPath();
  const cfg = existsSync(configPath) ? readJsonFile(configPath) : null;
  const enabled = Boolean(cfg?.external_server_enabled);
  const port =
    typeof cfg?.port === 'number' && cfg.port > 0 ? (cfg.port as number) : DEFAULT_EXTERNAL_PORT;
  const ssl = Boolean(cfg?.require_ssl);
  const scheme = ssl ? 'https' : 'http';
  const urls = enabled ? lanAddresses().map((ip) => `${scheme}://${ip}:${port}`) : [];
  return { enabled, port, ssl, urls };
}

/**
 * Persist the external-listener settings into the desktop's own server-config
 * and relaunch the backend so the change takes effect. The loopback the window
 * uses is unaffected — run() always serves it on a fresh ephemeral HTTP port —
 * so the window simply reloads onto the new loopback URL, exactly like switching
 * the compute runtime.
 */
async function writeServerSettings(settings: ServerSettings): Promise<void> {
  const configPath = serverConfigPath();
  const cfg = (existsSync(configPath) ? readJsonFile(configPath) : null) ?? {};
  cfg.external_server_enabled = settings.enabled;
  if (Number.isInteger(settings.port) && settings.port > 0 && settings.port <= 65535) {
    cfg.port = settings.port;
  }
  cfg.require_ssl = settings.ssl;
  // Bind all interfaces when remote access is on so other devices can reach it.
  if (settings.enabled) cfg.host = '0.0.0.0';
  mkdirSync(dirname(configPath), { recursive: true });
  writeFileSync(configPath, JSON.stringify(cfg, null, 2));
  // Keep the tray's "Enable server" checkbox in sync with the new config.
  refreshTrayMenu();
  await startAndLoad(await activeOverlayAccel());
}

function buildMenu(): void {
  // The web app's own toolbar + Settings dialog are the entire UI, so there's no
  // in-window menu bar — it was just chrome (compute backends, library folder
  // and logs now live in the desktop section of the web app's Settings dialog).
  // macOS still needs a menu for Cmd+Q, clipboard shortcuts and About, and it
  // lives in the global bar (no window-space cost), so keep a standard minimal
  // one there; on Windows/Linux drop the menu entirely. Dev runs keep the
  // reload/DevTools accelerators via a hidden role-only menu item.
  if (process.platform === 'darwin') {
    Menu.setApplicationMenu(
      Menu.buildFromTemplate([{ role: 'appMenu' }, { role: 'editMenu' }, { role: 'windowMenu' }]),
    );
    app.setAboutPanelOptions({
      applicationName: 'PixlStash',
      applicationVersion: app.getVersion(),
      website: 'https://pixlstash.dev',
    });
  } else {
    Menu.setApplicationMenu(null);
  }
}

/** Resolve the PYTHONPATH overlay for an accelerator, or null for the bundled env. */
function overlayFor(accel: Accel | null): string | null {
  return accel && accel !== bundledAccel() ? overlayDir(accel) : null;
}

/** The pixlstash inference device for an accelerator (GPU overlays → cuda). */
function deviceFor(accel: Accel | null): string | undefined {
  if (isDevBackend()) return undefined; // dev uses the developer's own env/config
  const a = accel ?? bundledAccel();
  if (a === 'cu128' || a === 'rocm') return 'cuda';
  if (a === 'metal') return 'auto';
  return 'cpu';
}

/** Spawn the backend (bundled env + optional GPU overlay), inject the loopback session, load the UI. */
async function startAndLoad(accel: Accel | null): Promise<void> {
  sendPhase({ phase: 'starting' });
  serverProcess?.stop();
  serverProcess = new ServerProcess((code) => {
    if (!quitting) {
      dialog.showErrorBox(
        'PixlStash backend stopped',
        `The PixlStash server exited unexpectedly (code ${code}). Check the log for details.`,
      );
    }
  });
  const running = await serverProcess.start(overlayFor(accel), deviceFor(accel));

  // Inject the pre-authenticated loopback session cookie so the window opens
  // straight into the library with no login prompt (backend seeds the matching
  // session from PIXLSTASH_DESKTOP_SESSION).
  await session.defaultSession.cookies.set({
    url: running.url,
    name: 'session_id',
    value: running.sessionToken,
    httpOnly: true,
    sameSite: 'lax',
  });

  sendPhase({ phase: 'ready', url: running.url });
  currentUrl = running.url;
  await mainWindow?.loadURL(running.url);
}

/** Which accelerator overlay (if any) should we launch with right now? */
async function activeOverlayAccel(): Promise<Accel | null> {
  const active = await manager.getActiveAccel();
  if (active && (await manager.isInstalled(active))) return active;
  if (active) await manager.setActiveAccel(null); // stale (overlay removed/app moved)
  return null;
}

/** Launch: dev passthrough, or the bundled env (+ active GPU overlay), straight into the library. */
async function boot(): Promise<void> {
  try {
    sendPhase({ phase: 'detect' });
    hardware = await detectHardware();

    if (isDevBackend()) {
      await startAndLoad(null);
      return;
    }

    runtime = readRuntimeInfo();
    if (!runtime) {
      throw new Error('Bundled runtime is missing or unreadable (packaging error).');
    }

    if (!existsSync(serverConfigPath())) {
      // First run: collect the library folder + compute choice before starting.
      // The setup window commits the config (via setup:commit) and then boots.
      await mainWindow?.loadFile(join(__dirname, 'renderer', 'setup.html'));
      return;
    }

    await startAndLoad(await activeOverlayAccel());
  } catch (e) {
    sendPhase({ phase: 'error', message: (e as Error).message });
  }
}

/**
 * Locate the standalone (pip/Docker) server config so first-run setup can offer
 * to import its values. Resolved via the backend's own platformdirs so it
 * matches wherever a local install actually keeps it; best-effort (returns null
 * if the bundled interpreter, platformdirs, or the file are unavailable).
 */
async function standaloneConfigPath(): Promise<string | null> {
  try {
    const { stdout } = await execFileP(
      bundledInterpreter(),
      [
        '-c',
        "from platformdirs import user_config_dir; import os; " +
          "print(os.path.join(user_config_dir('pixlstash'), 'server-config.json'))",
      ],
      { timeout: 8000 },
    );
    const path = stdout.trim();
    return path && existsSync(path) ? path : null;
  } catch {
    return null;
  }
}

function readJsonFile(path: string): Record<string, unknown> | null {
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** The discrete-GPU overlay (if any) we'd offer to install on this machine. */
function gpuUpgrade(): Accel | undefined {
  return hardware ? gpuUpgrades(hardware, bundledAccel())[0] : undefined;
}

/** Describe the bundled accelerator + each installable/installed GPU overlay. */
async function acceleratorState() {
  const active = await manager.getActiveAccel();
  const upgrades = hardware ? gpuUpgrades(hardware, bundledAccel()) : [];
  const installed = await manager.listInstalled();
  const candidates = new Set<Accel>([...upgrades, ...installed]);
  const items = [];
  for (const accel of OVERLAY_ACCELS) {
    if (!candidates.has(accel)) continue;
    items.push({
      accel,
      label: ACCEL_LABELS[accel],
      installed: installed.includes(accel),
      active: active === accel,
      recommended: upgrades[0] === accel,
    });
  }
  return {
    bundled: { accel: bundledAccel(), label: ACCEL_LABELS[bundledAccel()], active: active === null },
    items,
  };
}

function registerIpc(): void {
  ipcMain.handle('app:bootstrap', async () => ({
    version: app.getVersion(),
    hardware,
    runtime,
    bundledAccel: bundledAccel(),
    activeAccel: await manager.getActiveAccel(),
  }));

  // ---- First-run setup wizard ----

  ipcMain.handle('setup:probe', async () => {
    const stdPath = await standaloneConfigPath();
    const imported = stdPath ? readJsonFile(stdPath) : null;
    const importedImageRoot =
      typeof imported?.image_root === 'string' ? (imported.image_root as string) : null;
    const gpu = gpuUpgrade();
    return {
      importedFrom: imported ? stdPath : null,
      defaults: {
        imageRoot: importedImageRoot || defaultLibraryDir(),
        useGpu: Boolean(gpu),
      },
      gpu: gpu
        ? { available: true, accel: gpu, label: ACCEL_LABELS[gpu], name: hardware?.gpuName ?? null }
        : { available: false },
    };
  });

  ipcMain.handle('setup:pickFolder', async (_e, current?: string) => {
    const res = await dialog.showOpenDialog({
      title: 'Choose your PixlStash library folder',
      defaultPath: current || defaultLibraryDir(),
      properties: ['openDirectory', 'createDirectory'],
    });
    return res.canceled || !res.filePaths[0] ? null : res.filePaths[0];
  });

  ipcMain.handle('setup:commit', async (_e, choices: { imageRoot: string; useGpu: boolean }) => {
    if (!runtime) throw new Error('No bundled runtime available');
    const imageRoot = (choices?.imageRoot || '').trim();
    if (!imageRoot) throw new Error('Please choose a library folder.');

    // Write the desktop's own config. Loopback HTTP; the active runtime drives
    // the device (default_device left as auto). The backend fills the rest of
    // the defaults on first read.
    mkdirSync(dirname(serverConfigPath()), { recursive: true });
    writeFileSync(
      serverConfigPath(),
      JSON.stringify(
        { host: '127.0.0.1', require_ssl: false, image_root: imageRoot, default_device: 'auto' },
        null,
        2,
      ),
    );

    // The GPU choice maps onto the existing on-demand wheel-overlay system.
    const gpu = gpuUpgrade();
    if (choices?.useGpu && gpu) {
      await manager.installOverlay(gpu, runtime, (p) =>
        mainWindow?.webContents.send('install:progress', p),
      );
      await manager.setActiveAccel(gpu);
    } else {
      await manager.setActiveAccel(null);
    }

    await startAndLoad(await activeOverlayAccel());
  });

  ipcMain.handle('desktop:openLibraryFolder', () => openLibraryFolder());
  ipcMain.handle('desktop:showLogs', () => showServerLogs());

  // Desktop-shell preferences (e.g. hide-to-tray-on-close).
  ipcMain.handle('desktop:getPrefs', () => ({ hideToTrayOnClose }));
  ipcMain.handle('desktop:setPrefs', (_e, prefs: { hideToTrayOnClose?: boolean }) => {
    if (typeof prefs?.hideToTrayOnClose === 'boolean') {
      hideToTrayOnClose = prefs.hideToTrayOnClose;
      saveDesktopPrefs();
    }
    return { hideToTrayOnClose };
  });

  // External server (remote access) settings. The loopback the window uses is
  // never affected by these — only the optional second listener.
  ipcMain.handle('server:getSettings', () => readServerSettings());
  ipcMain.handle('server:setSettings', async (_e, settings: ServerSettings) => {
    await writeServerSettings(settings);
  });

  // Custom title-bar window controls (the window is frameless).
  ipcMain.handle('window:minimize', () => mainWindow?.minimize());
  ipcMain.handle('window:toggleMaximize', () => {
    if (!mainWindow) return;
    if (mainWindow.isMaximized()) mainWindow.unmaximize();
    else mainWindow.maximize();
  });
  ipcMain.handle('window:close', () => mainWindow?.close());

  ipcMain.handle('accel:list', async () => acceleratorState());

  ipcMain.handle('accel:install', async (_e, accel: Accel) => {
    if (!runtime) throw new Error('No bundled runtime available');
    await manager.installOverlay(accel, runtime, (p) =>
      mainWindow?.webContents.send('install:progress', p),
    );
    await startAndLoad(accel);
    return acceleratorState();
  });

  ipcMain.handle('accel:use', async (_e, accel: Accel | null) => {
    await manager.setActiveAccel(accel);
    await startAndLoad(accel);
    return acceleratorState();
  });

  ipcMain.handle('accel:remove', async (_e, accel: Accel) => {
    await manager.remove(accel);
    await startAndLoad(await activeOverlayAccel());
    return acceleratorState();
  });
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    // Relaunching while we're already running just reopens the window (it may be
    // hidden in the tray).
    showWindow();
  });

  app.whenReady().then(() => {
    loadDesktopPrefs();
    buildMenu();
    registerIpc();
    createMainWindow();
    createTray();
    // Kick off boot once the splash has loaded so phase events aren't missed.
    mainWindow?.webContents.once('did-finish-load', () => {
      void boot();
    });

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    });
  });

  app.on('before-quit', () => {
    quitting = true;
    serverProcess?.stop();
    tray?.destroy();
    tray = null;
  });

  app.on('window-all-closed', () => {
    // With hide-to-tray active the window only hides (never destroyed), so this
    // normally won't fire. It does fire when the tray is unavailable OR the user
    // turned hide-to-tray off — in both cases closing the window should quit
    // (macOS keeps its usual dock behavior).
    if (process.platform !== 'darwin' && !(tray && hideToTrayOnClose)) app.quit();
  });
}
