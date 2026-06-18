import { app } from 'electron';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, posix as pathPosix, resolve, win32 as pathWin32 } from 'node:path';

/** Compute accelerators a PixlStash runtime can target. */
export type Accel = 'cpu' | 'cu128' | 'rocm' | 'metal';

/**
 * The accepted {@link Accel} values, as a runtime array (the union type is erased
 * at compile time). The single source of truth for validating any externally
 * supplied accelerator string — keep it in step with the `Accel` union above.
 */
export const ACCEL_VALUES: readonly Accel[] = ['cpu', 'cu128', 'rocm', 'metal'];

/** True only when `value` is one of the known {@link Accel} members. */
export function isAccel(value: unknown): value is Accel {
  return typeof value === 'string' && (ACCEL_VALUES as readonly string[]).includes(value);
}

/**
 * Parse the developer/CI hardware-detection override. The override fakes which
 * GPU the machine appears to have so the backend-download/overlay flow can be
 * exercised on hardware that lacks the matching GPU. It is read from, in order
 * of precedence:
 *
 *   1. a `--force-backend=<accel>` argv flag (wins), and
 *   2. the `PIXLSTASH_FORCE_BACKEND=<accel>` env var (CI convenience).
 *
 * SECURITY: the value is validated against the {@link Accel} enum and anything
 * else is rejected (returns `null` after a warning). This flag must NEVER feed an
 * install index — the download still resolves only through the hardcoded
 * {@link TORCH_INDEX} map keyed by the validated `Accel`, so an arbitrary string
 * can never become an arbitrary-wheel-install vector. Returns the validated
 * `Accel`, or `null` when no (valid) override is present.
 */
export function parseForcedBackend(
  argv: readonly string[] = process.argv,
  env: NodeJS.ProcessEnv = process.env,
  warn: (message: string) => void = (m) => console.warn(m),
): Accel | null {
  // argv wins over the env var when both are set.
  const flag = argv.find((a) => a.startsWith('--force-backend='));
  const raw = flag !== undefined ? flag.slice('--force-backend='.length) : env.PIXLSTASH_FORCE_BACKEND;
  if (raw === undefined || raw === '') return null;
  if (!isAccel(raw)) {
    warn(
      `[force-backend] ignoring invalid backend override '${raw}'; ` +
        `expected one of ${ACCEL_VALUES.join(', ')}`,
    );
    return null;
  }
  return raw;
}

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
 * Compute the per-platform *default* parent directory for GPU overlays. Pure (all
 * inputs passed in) so the platform rule is unit-testable without electron.
 *
 * On Windows the packaged app installs to a user-writable, user-chosen location
 * (electron-builder NSIS with perMachine:false + allowToChangeInstallationDirectory),
 * so the heavy GPU wheels go *inside the install folder the user picked* rather
 * than always landing on the system drive under AppData. `resourcesPath` is
 * `<installDir>/resources`, so its parent is the install dir.
 *
 * Everywhere else the installed image is read-only (a Linux AppImage is a squashfs
 * mount, a notarized macOS .app must not be modified) or we're running unpacked in
 * dev — there is no writable "install folder" to use — so overlays stay under the
 * app-data dir, as before. Users who want them elsewhere can still pick a custom
 * location (see {@link backendsRoot}).
 */
export function computeDefaultBackendsRoot(
  platform: NodeJS.Platform,
  isPackaged: boolean,
  resourcesPath: string,
  userData: string,
): string {
  // Use the target platform's path semantics (so the win32 branch is correct —
  // and unit-testable — even when this runs on a POSIX host in CI).
  const p = platform === 'win32' ? pathWin32 : pathPosix;
  if (platform === 'win32' && isPackaged) {
    return p.join(p.dirname(resourcesPath), 'backends');
  }
  return p.join(userData, 'backends');
}

/** The per-platform default overlay location for this run (see {@link computeDefaultBackendsRoot}). */
export function defaultBackendsRoot(): string {
  return computeDefaultBackendsRoot(
    process.platform,
    app.isPackaged,
    process.resourcesPath,
    app.getPath('userData'),
  );
}

/**
 * Where a user-chosen overlay location is persisted. Kept under userData (which
 * always survives app updates/moves) even when the overlays themselves live on a
 * different drive, so the app can always find them again.
 */
export function backendsLocationPath(): string {
  return join(app.getPath('userData'), 'backends-location.json');
}

/**
 * Parent directory for GPU overlays: the user's chosen location if they set one,
 * otherwise the per-platform {@link defaultBackendsRoot}. The default is computed
 * (never stored), so when the user hasn't overridden it the overlays keep tracking
 * the real install dir across updates/moves.
 */
export function backendsRoot(): string {
  try {
    const saved = JSON.parse(readFileSync(backendsLocationPath(), 'utf8'));
    if (typeof saved?.dir === 'string' && saved.dir.trim()) return saved.dir;
  } catch {
    /* not set / unreadable → fall back to the computed default */
  }
  return defaultBackendsRoot();
}

/**
 * Decide what to persist for a chosen overlay location: `null` (meaning "use the
 * computed default") when the choice equals the default, else the resolved path.
 * Pure (platform passed in) so the custom-vs-default rule is unit-testable.
 *
 * Windows paths are case-insensitive, so a choice that differs from the default
 * only by casing (e.g. a different drive-letter case) must still clear the
 * override and keep tracking the install dir; POSIX filesystems are
 * case-sensitive, so there the comparison is exact.
 */
export function normalizeBackendsRoot(
  chosen: string,
  def: string,
  platform: NodeJS.Platform = process.platform,
): string | null {
  const resolved = resolve(chosen);
  const same =
    platform === 'win32'
      ? resolved.toLowerCase() === resolve(def).toLowerCase()
      : resolved === resolve(def);
  return same ? null : resolved;
}

/** Persist (`dir`) or clear (`null`/empty) the user's chosen overlay location. */
export function setBackendsRoot(dir: string | null): void {
  const path = backendsLocationPath();
  if (dir === null || dir.trim() === '') {
    rmSync(path, { force: true });
    return;
  }
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify({ dir }, null, 2));
}

/**
 * Writable overlay holding on-demand GPU wheels, one directory per accelerator,
 * injected via PYTHONPATH so its torch/onnxruntime shadow the bundled CPU build.
 * Lives under {@link backendsRoot} so the multi-GB download can follow the app's
 * install location (Windows) or a folder the user picked.
 */
export function overlayDir(accel: Accel): string {
  return join(backendsRoot(), accel);
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
