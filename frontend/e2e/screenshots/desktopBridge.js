// Make the SPA render as the DESKTOP app for screenshots. The Electron shell
// wraps this same SPA in a frameless window; the in-app title bar (TitleBar.vue,
// with the PixlStash wordmark, breadcrumb and min/max/close controls) and the
// `is-desktop` layout appear whenever window.pixlstashDesktop exists. Injecting
// a stub bridge before page scripts run gives us exactly that desktop chrome
// without launching Electron (which would need its own bundled backend, not the
// demo-data library we want to show).
//
// Mirrors the real preload surface (electron/src/preload.ts) closely enough for
// the library view: window controls, version/settings/streaming hooks, and the
// compute/server reads used if the settings dialog's Backend tab is shown.

function installDesktopBridge() {
  const noop = () => {}
  const sub = () => () => {}
  const val = (v) => () => Promise.resolve(v)
  window.pixlstashDesktop = {
    bootstrap: val({}),
    // Window controls (TitleBar.vue wires these to the custom min/max/close).
    windowMinimize: val(undefined),
    windowToggleMaximize: val(undefined),
    windowClose: val(undefined),
    // Streaming + tray hooks (App.vue / useVersionCheck register these).
    onPhase: sub,
    onProgress: sub,
    onOpenSettings: sub,
    // Desktop prefs + remote-access / compute reads (Backend settings tab).
    getDesktopPrefs: val({ hideToTrayOnClose: true }),
    setDesktopPrefs: val(undefined),
    getServerSettings: val({
      enabled: true,
      port: 9537,
      ssl: false,
      urls: ['http://192.168.1.24:9537'],
    }),
    setServerSettings: val(undefined),
    checkServerPort: val({ available: true }),
    listAccelerators: val({
      bundled: { accel: 'cpu', label: 'Built-in (CPU)', active: true },
      items: [
        {
          accel: 'cu128',
          label: 'NVIDIA GPU acceleration (CUDA 12.8)',
          installed: false,
          active: false,
          recommended: true,
        },
      ],
    }),
    installAccelerator: val(undefined),
    useAccelerator: val(undefined),
    removeAccelerator: val(undefined),
    openLibraryFolder: noop,
    showLogs: noop,
  }
}

/** Install the desktop stub bridge on a page BEFORE it navigates. */
export async function useDesktopBridge(page) {
  await page.addInitScript(installDesktopBridge)
}
