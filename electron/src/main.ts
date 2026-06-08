import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  nativeImage,
  shell,
  session,
  dialog,
} from 'electron';
import { execFile } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
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
let quitting = false;

// Cached during boot so the renderer/backend manager can reuse them.
let hardware: Hardware | null = null;
let runtime: RuntimeInfo | null = null;

function sendPhase(payload: Record<string, unknown>): void {
  mainWindow?.webContents.send('app:phase', payload);
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
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
  // Open external links in the user's browser, not inside the app window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://127.0.0.1') || url.startsWith('http://localhost')) {
      return { action: 'allow' };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });
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

/** Reveal the current backend log (handy when attaching it to a bug report). */
function showServerLogs(): void {
  const log = serverLogPath();
  if (existsSync(log)) shell.showItemInFolder(log);
  else void shell.openPath(dirname(log));
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
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    buildMenu();
    registerIpc();
    createMainWindow();
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
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
}
