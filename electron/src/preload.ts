import { contextBridge, ipcRenderer, webUtils } from 'electron';

/**
 * Minimal, locked-down bridge for the splash / backend-manager UI, plus the
 * few things the main PixlStash web app (loaded from the local server) can only
 * get from the shell: saving a picture to a chosen file, the clipboard, and the
 * path of a file the user dropped on the window.
 */
contextBridge.exposeInMainWorld('pixlstashDesktop', {
  bootstrap: () => ipcRenderer.invoke('app:bootstrap'),
  // First-run setup wizard.
  probeSetup: () => ipcRenderer.invoke('setup:probe'),
  pickLibraryFolder: (current: string) => ipcRenderer.invoke('setup:pickFolder', current),
  // What is in a folder, for the startup screen's verdict. Read-only.
  inspectSetupPath: (path: string) => ipcRenderer.invoke('setup:inspect', path),
  commitSetup: (choices: unknown) => ipcRenderer.invoke('setup:commit', choices),
  // Startup permission recovery: main holds the backend's repair report and
  // waits for exactly one answer before retrying or quitting.
  permissionRepairRequest: () => ipcRenderer.invoke('permissions:request'),
  resolvePermissionRepair: (accepted: boolean) =>
    ipcRenderer.invoke('permissions:resolve', accepted),
  listAccelerators: () => ipcRenderer.invoke('accel:list'),
  installAccelerator: (accel: string) => ipcRenderer.invoke('accel:install', accel),
  useAccelerator: (accel: string | null) => ipcRenderer.invoke('accel:use', accel),
  removeAccelerator: (accel: string) => ipcRenderer.invoke('accel:remove', accel),
  // Where the on-demand GPU overlay is stored (keep the big download off C:).
  getBackendLocation: () => ipcRenderer.invoke('backend:getLocation'),
  pickBackendLocation: (current: string) => ipcRenderer.invoke('backend:pickLocation', current),
  setBackendLocation: (dir: string) => ipcRenderer.invoke('backend:setLocation', dir),
  // Desktop conveniences re-homed from the (removed) native menu.
  // The privacy answer the startup screen collected, handed over once so the
  // app can apply it instead of asking again.
  takePendingTelemetry: () => ipcRenderer.invoke('startup:takePendingTelemetry'),
  // A folder read the startup screen finished during the runtime download, so
  // the wizard opens on its questions instead of on a progress bar.
  takePendingMapping: () => ipcRenderer.invoke('startup:takePendingMapping'),
  // Hand the window back to the startup framework for one question the app
  // cannot answer on its own (today: the upgrade's privacy question).
  askStartupQuestion: (step: string) => ipcRenderer.invoke('startup:askQuestion', step),
  openLibraryFolder: () => ipcRenderer.invoke('desktop:openLibraryFolder'),
  showLogs: () => ipcRenderer.invoke('desktop:showLogs'),
  // Narrow media operations for the web-app lightbox. The renderer supplies
  // authenticated bytes; only main chooses a destination or touches clipboard.
  beginMediaSaveAs: (suggestedName: string) =>
    ipcRenderer.invoke('media:beginSaveAs', suggestedName),
  completeMediaSaveAs: (saveId: string, data: ArrayBuffer) =>
    ipcRenderer.invoke('media:completeSaveAs', { saveId, data }),
  cancelMediaSaveAs: (saveId: string) => ipcRenderer.invoke('media:cancelSaveAs', saveId),
  copyPngToClipboard: (data: ArrayBuffer) => ipcRenderer.invoke('media:copyPng', data),
  // Where a dropped file actually lives. A `File` from a drop carries a name and
  // bytes but no path - a browser sandbox rule, and the reason dropping a model
  // file on the web app can only point at the Add file… menu. The shell can
  // answer, so on the desktop a dropped `.safetensors` goes to the same
  // POST /model-files the menu calls: the file is already on this machine, and
  // that endpoint copies it into the model store rather than uploading a
  // gigabyte to land it beside where it started. Nothing crosses to main and no
  // file is read here - `webUtils` resolves the path in the renderer, and the
  // caller can only ask about a file the user themselves dropped.
  getDroppedFilePath: (file: File) => webUtils.getPathForFile(file),
  // Desktop-shell preferences (hide-to-tray-on-close, ...).
  getDesktopPrefs: () => ipcRenderer.invoke('desktop:getPrefs'),
  setDesktopPrefs: (prefs: unknown) => ipcRenderer.invoke('desktop:setPrefs', prefs),
  // External server (remote access) settings.
  getServerSettings: () => ipcRenderer.invoke('server:getSettings'),
  setServerSettings: (settings: unknown) =>
    ipcRenderer.invoke('server:setSettings', settings),
  checkServerPort: (port: number) => ipcRenderer.invoke('server:checkPort', port),
  // Fired when the tray's Settings entry asks the renderer to open Settings.
  onOpenSettings: (cb: () => void) => {
    const listener = () => cb();
    ipcRenderer.on('app:open-settings', listener);
    return () => ipcRenderer.removeListener('app:open-settings', listener);
  },
  // Custom title-bar window controls (frameless window).
  windowMinimize: () => ipcRenderer.invoke('window:minimize'),
  windowToggleMaximize: () => ipcRenderer.invoke('window:toggleMaximize'),
  windowClose: () => ipcRenderer.invoke('window:close'),
  // Main → renderer streaming events.
  onPhase: (cb: (payload: unknown) => void) => {
    const listener = (_e: unknown, payload: unknown) => cb(payload);
    ipcRenderer.on('app:phase', listener);
    return () => ipcRenderer.removeListener('app:phase', listener);
  },
  onProgress: (cb: (payload: unknown) => void) => {
    const listener = (_e: unknown, payload: unknown) => cb(payload);
    ipcRenderer.on('install:progress', listener);
    return () => ipcRenderer.removeListener('install:progress', listener);
  },
});
