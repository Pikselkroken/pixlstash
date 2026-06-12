import { app } from 'electron';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/** Compute accelerators a PixlStash runtime can target. */
export type Accel = 'cpu' | 'cu128' | 'rocm' | 'metal';

/**
 * Versions + accelerator baked into the runtime embedded in the installer.
 * Written by scripts/build_desktop_runtime.py to resources/runtime.json and
 * read at boot. The bundled env always works offline (CPU on Windows/Linux,
 * Metal on macOS); GPU accelerators are added on demand as PYTHONPATH overlays.
 */
export interface RuntimeInfo {
  /** Accelerator the bundled env ships with: 'cpu' (win/linux) or 'metal' (mac). */
  accel: Accel;
  torch: string;
  torchvision: string;
  onnxruntime: string;
}

export const ACCEL_LABELS: Record<Accel, string> = {
  cpu: 'CPU',
  cu128: 'NVIDIA GPU (CUDA 12.8)',
  rocm: 'AMD GPU (ROCm, experimental)',
  metal: 'Apple Silicon (Metal)',
};

/**
 * torch install index per accelerator; `null` means default PyPI (macOS wheels
 * already include Metal/MPS). Mirrors TORCH_INDEX in build_desktop_runtime.py.
 */
export const TORCH_INDEX: Record<Accel, string | null> = {
  cpu: 'https://download.pytorch.org/whl/cpu',
  cu128: 'https://download.pytorch.org/whl/cu128',
  // ROCm is experimental and Linux-only. rocm7.1 is the index that publishes the
  // same torch version line as the bundled CPU build; older rocmX.Y indexes lag
  // several torch releases behind. Keep this in step with the bundled torch when
  // it moves (the install-time fallback in BackendManager handles minor drift).
  rocm: 'https://download.pytorch.org/whl/rocm7.1',
  metal: null,
};

/**
 * onnxruntime distribution per accelerator. Only `onnxruntime-gpu` (CUDA) needs
 * an overlay; the others reuse the CPU `onnxruntime` already in the bundled env.
 */
export const ONNX_PACKAGE: Record<Accel, string> = {
  cpu: 'onnxruntime',
  cu128: 'onnxruntime-gpu',
  rocm: 'onnxruntime', // ROCm onnxruntime is not on PyPI — bundled CPU ORT.
  metal: 'onnxruntime',
};

/**
 * The read-only Python runtime embedded in the installer. Packaged: under the
 * app's resources dir; dev: electron/resources/python, produced locally by
 * `python scripts/build_desktop_runtime.py`.
 */
export function bundledRuntimeDir(): string {
  if (app.isPackaged) return join(process.resourcesPath, 'python');
  // dev: electron/dist/config.js → electron/resources/python
  return join(__dirname, '..', 'resources', 'python');
}

/** The bundled interpreter inside {@link bundledRuntimeDir}. */
export function bundledInterpreter(): string {
  const dir = bundledRuntimeDir();
  if (process.platform === 'win32') return join(dir, 'python.exe');
  return join(dir, 'bin', 'python3');
}

/** runtime.json sits next to the bundled python/ directory. */
export function runtimeInfoPath(): string {
  return join(bundledRuntimeDir(), '..', 'runtime.json');
}

export function readRuntimeInfo(): RuntimeInfo | null {
  try {
    return JSON.parse(readFileSync(runtimeInfoPath(), 'utf8')) as RuntimeInfo;
  } catch {
    return null;
  }
}

/**
 * Writable overlay holding on-demand GPU wheels, one directory per accelerator,
 * injected via PYTHONPATH so its torch/onnxruntime shadow the bundled CPU build.
 */
export function overlayDir(accel: Accel): string {
  return join(app.getPath('userData'), 'backends', accel);
}

/** Marker written into an overlay once its install completed successfully. */
export function overlayMarkerPath(accel: Accel): string {
  return join(overlayDir(accel), 'OVERLAY.json');
}

/** JSON state recording the active GPU accelerator (absent => bundled env). */
export function activeAccelPath(): string {
  return join(app.getPath('userData'), 'active-accel.json');
}

/** Per-launch log file for the spawned Python server. */
export function serverLogPath(): string {
  return join(app.getPath('logs'), 'pixlstash-server.log');
}

/** Log file for GPU-overlay pip installs (so failures are diagnosable). */
export function installLogPath(): string {
  return join(app.getPath('logs'), 'pixlstash-install.log');
}

/**
 * The desktop app's own server config, kept under the app data dir and separate
 * from any standalone pip/Docker install's config — the two never overwrite each
 * other. Seeded once by the first-run wizard (which can import an existing
 * config's values), then owned by the app. Its existence marks setup as done.
 */
export function serverConfigPath(): string {
  return join(app.getPath('userData'), 'server-config.json');
}

/** Library location offered on first run when nothing is imported. */
export function defaultLibraryDir(): string {
  let base: string;
  try {
    base = app.getPath('pictures');
  } catch {
    base = app.getPath('home');
  }
  return join(base, 'PixlStash');
}

/** Optional pip index-url override for corporate mirrors / proxies. */
export function pipIndexUrl(): string | undefined {
  return process.env.PIXLSTASH_PIP_INDEX_URL || undefined;
}

/** True when running the dev loop against a local interpreter (see ServerProcess). */
export function isDevBackend(): boolean {
  return Boolean(process.env.PIXLSTASH_DESKTOP_DEV || process.env.PIXLSTASH_DEV_BACKEND);
}
