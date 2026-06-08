import { contextBridge, ipcRenderer } from 'electron';

/**
 * Minimal, locked-down bridge for the splash / backend-manager UI. The main
 * PixlStash web app (loaded from the local server) does not use any of this.
 */
contextBridge.exposeInMainWorld('pixlstashDesktop', {
  bootstrap: () => ipcRenderer.invoke('app:bootstrap'),
  // First-run setup wizard.
  probeSetup: () => ipcRenderer.invoke('setup:probe'),
  pickLibraryFolder: (current: string) => ipcRenderer.invoke('setup:pickFolder', current),
  commitSetup: (choices: unknown) => ipcRenderer.invoke('setup:commit', choices),
  listAccelerators: () => ipcRenderer.invoke('accel:list'),
  installAccelerator: (accel: string) => ipcRenderer.invoke('accel:install', accel),
  useAccelerator: (accel: string | null) => ipcRenderer.invoke('accel:use', accel),
  removeAccelerator: (accel: string) => ipcRenderer.invoke('accel:remove', accel),
  // Desktop conveniences re-homed from the (removed) native menu.
  openLibraryFolder: () => ipcRenderer.invoke('desktop:openLibraryFolder'),
  showLogs: () => ipcRenderer.invoke('desktop:showLogs'),
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
