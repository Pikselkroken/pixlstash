import { spawn } from 'node:child_process';
import { createWriteStream, mkdirSync } from 'node:fs';
import { mkdir, readdir, rm, rmdir, readFile, writeFile, access } from 'node:fs/promises';
import { dirname, join, resolve, sep } from 'node:path';
import {
  Accel,
  ACCEL_LABELS,
  ONNX_PACKAGE,
  ORT_GPU_PIN,
  RuntimeInfo,
  TORCH_INDEX,
  activeAccelPath,
  bundledInterpreter,
  installLogPath,
  overlayDir,
  overlayMarkerPath,
  pipIndexUrl,
} from '../config';

/** Drop a PEP 440 local version label: "2.12.0+cpu" → "2.12.0". */
function basePep440(version: string): string {
  return version.split('+')[0];
}

/**
 * Condense a verbose pip "Downloading <wheel> (820.3 MB)" line into
 * "Downloading torch (820.3 MB)" - just the package name (no version/platform
 * tags or %-encoding) and the size, for a readable, short progress caption.
 */
function prettyDownload(line: string): string {
  const m = line.match(/^Downloading\s+(\S+)/i);
  if (!m) return line.slice(0, 120);
  const file = m[1].replace(/%2B/gi, '+').split('/').pop() ?? m[1];
  const pkg = file.replace(/-\d.*$/, '') || file;
  const size = line.match(/\(([\d.]+\s*[KMG]B)\)/i);
  return `Downloading ${pkg}${size ? ` (${size[1]})` : ''}`;
}

export interface InstallProgress {
  phase: 'prepare' | 'download' | 'install' | 'done';
  message: string;
  /** 0..1 within the active download, or -1 when unknown. */
  fraction: number;
  /**
   * Bytes of wheels pip has finished fetching, and the bytes it has announced
   * so far. pip names each wheel's size as it starts it ("Downloading torch
   * (820.3 MB)") and, over a pipe, says nothing more until the next one - so
   * "how far through the download are we" is answerable, where "how far through
   * this file" is not. The total grows as new wheels are announced, which is
   * why `bytesDone` is what advances and the pair is reported rather than a
   * single fraction that could walk backwards.
   */
  bytesDone: number;
  bytesTotal: number;
}

/** Bytes named in a pip "Downloading … (820.3 MB)" line, or 0. */
export function announcedBytes(line: string): number {
  const match = line.match(/\(([\d.]+)\s*([KMG]?B)\)/i);
  if (!match) return 0;
  const scale: Record<string, number> = { B: 1, KB: 1024, MB: 1024 ** 2, GB: 1024 ** 3 };
  return Number(match[1]) * (scale[match[2].toUpperCase()] ?? 1);
}

export interface OverlayMeta {
  accel: Accel;
  torch: string;
  installedAt: string;
  /**
   * The app version whose bundled env this overlay was pruned against. The
   * bundled site-packages only ever changes with an app update, so a mismatch
   * means the prune below has not been re-run against the current bundle - see
   * {@link BackendManager.repairIfStale}. Absent on overlays written before
   * pruning existed, which is exactly the state that needs repairing.
   */
  appVersion?: string;
}

/** One distribution found in an overlay, read from its `<name>-<version>.dist-info`. */
export interface DistInfo {
  /** Distribution name as the directory spells it, e.g. "typing_extensions". */
  name: string;
  version: string;
  /** The `.dist-info` directory name, relative to the overlay root. */
  dirName: string;
}

/** GPU accelerators that can be layered on top of the bundled CPU/Metal env. */
export const OVERLAY_ACCELS: Accel[] = ['cu128', 'rocm'];

/**
 * Build the `pip install` argument list for a GPU overlay. Pure (no I/O) so the
 * install contract is unit-testable: which index-url is used, how torch/
 * torchvision are pinned, and the exact-match-vs-lagging-index fallback.
 *
 * The index ALWAYS comes from the hardcoded {@link TORCH_INDEX} map keyed by the
 * validated `Accel` - never from caller-supplied data. `availableTorch` is the
 * public torch versions the index publishes (newest first), used only to decide
 * between an exact pin and the lagging-index fallback; it can never introduce a
 * new index.
 *
 * @param accel       the (validated) overlay accelerator
 * @param info        the bundled runtime versions to stay ABI-compatible with
 * @param constraints path to the pip constraints file
 * @param dir         the overlay --target directory
 * @param availableTorch public torch versions on the index (newest first); empty
 *                        when the index couldn't be queried
 * @param userIndexUrl  optional corporate-mirror pip index (PIXLSTASH_PIP_INDEX_URL)
 */
export function buildOverlayPipArgs(
  accel: Accel,
  info: RuntimeInfo,
  constraints: string,
  dir: string,
  availableTorch: readonly string[],
  userIndexUrl: string | undefined,
): { args: string[]; usedFallback: boolean } {
  const args = ['-m', 'pip', 'install', '--no-cache-dir', '--target', dir, '-c', constraints];
  const index = TORCH_INDEX[accel];
  if (index) {
    args.push('--index-url', index, '--extra-index-url', userIndexUrl ?? 'https://pypi.org/simple');
  } else if (userIndexUrl) {
    args.push('--index-url', userIndexUrl);
  }
  // Pin torch to the bundled *public* version, dropping the +cpu/+cu128 local
  // tag (the GPU index serves its own local build). If the GPU index lags the
  // bundled CPU build - its torch version isn't published there yet - fall back
  // to the index's newest and let pip choose the matching torchvision. The
  // overlay fully shadows the bundled torch, so an exact version match isn't
  // required, only a self-consistent torch+torchvision pair.
  const torchWant = basePep440(info.torch);
  let usedFallback = false;
  if (availableTorch.length && !availableTorch.includes(torchWant)) {
    usedFallback = true;
    args.push(`torch==${availableTorch[0]}`, 'torchvision');
  } else {
    args.push(`torch==${torchWant}`, `torchvision==${basePep440(info.torchvision)}`);
  }
  if (ONNX_PACKAGE[accel] === 'onnxruntime-gpu') {
    // NOT the bundle's ORT version: onnxruntime-gpu has a CUDA-flavor axis the
    // version number doesn't express - PyPI serves one flavor per release, and
    // 1.27.0 moved to CUDA 13 (links libcudart.so.13) while the cu128 overlay's
    // nvidia stack only ships libcudart.so.12, so `import onnxruntime` ImportErrors
    // and the whole backend dies at startup (insightface imports it in the
    // pixlstash import chain; live incident 2026-07-20). Pin per accel to the last
    // build of that accel's CUDA generation instead - see ORT_GPU_PIN in config.ts.
    const ortPin = ORT_GPU_PIN[accel];
    if (!ortPin) {
      // A new GPU accel was mapped to onnxruntime-gpu without recording which
      // CUDA generation it needs. Falling back to the bundle version is exactly
      // the incident above, so fail loudly at install time instead.
      throw new Error(`No onnxruntime-gpu pin recorded for ${accel} - add it to ORT_GPU_PIN`);
    }
    args.push(`onnxruntime-gpu==${ortPin}`);
  }
  return { args, usedFallback };
}

/**
 * Reduce a `pip freeze` of the bundled env to the pins usable as overlay
 * install constraints. Dropped, two kinds:
 *  - torch/torchvision/onnxruntime: owned by the overlay itself, so the bundle's
 *    CPU pins must not constrain them.
 *  - Build tooling (setuptools/pip/wheel): constraining these serves no runtime
 *    alignment purpose, and the CUDA torch wheels declare `setuptools<82` - with
 *    the bundled env frozen at setuptools>=82 the constraint made every overlay
 *    install ResolutionImpossible (seen live 2026-07-20: bundled 83.0.0 vs
 *    torch 2.11.0+cu128). pip is free to put a satisfying setuptools in the
 *    overlay instead, which is the long-standing installed state anyway.
 * Constraints files also reject direct references (`pkg @ url` / local paths -
 * e.g. the pixlstash wheel) and option/comment lines, so only plain
 * `name==version` pins survive.
 */
export function filterConstraintsFreeze(frozen: string): string {
  const drop = /^(torch|torchvision|onnxruntime|onnxruntime-gpu|setuptools|pip|wheel)(==|@|\s|$)/i;
  const kept = frozen
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(
      (l) =>
        l && !l.startsWith('-') && !l.startsWith('#') && !l.includes(' @ ') && !drop.test(l),
    );
  return kept.join('\n') + '\n';
}

/**
 * The bundled env's installed versions, keyed by normalized distribution name,
 * read from the same `pip freeze` that produces the install constraints. Used to
 * decide which of the overlay's packages are duplicates of the bundle's - see
 * {@link planOverlayPrune}. Direct references (`pkg @ url`) carry no comparable
 * version and are skipped.
 */
export function parseFreezeVersions(frozen: string): Map<string, string> {
  const versions = new Map<string, string>();
  for (const raw of frozen.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('-') || line.startsWith('#')) continue;
    const match = line.match(/^([A-Za-z0-9._-]+)==(.+)$/);
    if (match) versions.set(normalizeDistName(match[1]), match[2].trim());
  }
  return versions;
}

/** PEP 503 name normalization: case-folded, runs of -_. collapsed to "-". */
export function normalizeDistName(name: string): string {
  return name.toLowerCase().replace(/[-_.]+/g, '-');
}

/**
 * The distributions an overlay exists to provide. Everything else pip puts in
 * there is a dependency the bundled env already ships, and is deleted - see
 * {@link planOverlayPrune} for why that matters.
 *
 * Prefix-matched rather than exact, because the CUDA stack is one wheel per
 * library (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, ...) and its membership
 * changes with every torch release.
 */
export const OVERLAY_OWNED_PREFIXES = [
  'torch',
  'torchvision',
  'onnxruntime-gpu',
  'nvidia-',
  'triton',
  'pytorch-triton',
];

/** True when `name` is a distribution the overlay is *for*, not a duplicate. */
export function overlayOwns(name: string): boolean {
  const key = normalizeDistName(name);
  return OVERLAY_OWNED_PREFIXES.some((p) => key === p || key.startsWith(p));
}

/**
 * Decide which of an overlay's distributions must be deleted.
 *
 * `pip install --target` writes the *whole* dependency closure into the overlay,
 * not just the GPU wheels: torch alone drags in typing_extensions, numpy, sympy,
 * networkx, jinja2, filelock, fsspec and more. ServerProcess then prepends the
 * overlay to PYTHONPATH, so every one of those copies shadows the bundled env's
 * own - for no benefit, since the constraints file pinned them to the bundle's
 * versions in the first place.
 *
 * The moment the two disagree, the bundle's own code imports the overlay's copy
 * and dies before the server starts. Seen live on Windows 2026-09-03: an overlay
 * carrying typing_extensions 4.15 over a bundle holding 4.16 made the bundle's
 * anyio raise `ImportError: cannot import name 'sentinel'`, killing the backend
 * in the fastapi import chain. `sentinel` exists only in 4.16+.
 *
 * So: keep what the overlay owns (torch and the GPU stack), keep anything the
 * bundle does not have at all, delete the rest. That is the state the overlay
 * would have if pip could install *only* the wheels we asked it for.
 *
 * Pure (no I/O) so the rule is unit-testable without a filesystem.
 */
export function planOverlayPrune(
  overlayDists: readonly DistInfo[],
  bundled: ReadonlyMap<string, string>,
): { remove: DistInfo[]; keep: DistInfo[] } {
  const remove: DistInfo[] = [];
  const keep: DistInfo[] = [];
  for (const dist of overlayDists) {
    const key = normalizeDistName(dist.name);
    if (overlayOwns(key) || !bundled.has(key)) keep.push(dist);
    else remove.push(dist);
  }
  return { remove, keep };
}

/** Parse a "typing_extensions-4.15.0.dist-info" directory name, or null. */
export function parseDistInfoDir(dirName: string): DistInfo | null {
  if (!dirName.endsWith('.dist-info')) return null;
  const stem = dirName.slice(0, -'.dist-info'.length);
  const dash = stem.lastIndexOf('-');
  if (dash < 1) return null;
  return { name: stem.slice(0, dash), version: stem.slice(dash + 1), dirName };
}

/**
 * The files a distribution's RECORD lists, resolved against `dir` and filtered
 * to those that genuinely live inside it.
 *
 * RECORD paths are relative to the install root but may point outside it
 * (`../../Scripts/foo.exe` for console entry points). An overlay is a plain
 * `--target` directory with no siblings we own, so anything escaping it is not
 * ours to delete - and following one would let a crafted RECORD reach the rest
 * of the install. Pure so that containment is testable directly.
 */
export function recordedFilesWithin(dir: string, record: string): string[] {
  const root = resolve(dir);
  const prefix = root.endsWith(sep) ? root : root + sep;
  const files: string[] = [];
  for (const line of record.split(/\r?\n/)) {
    const rel = line.split(',')[0]?.trim();
    if (!rel) continue;
    const full = resolve(dir, rel);
    if (full.startsWith(prefix)) files.push(full);
  }
  return files;
}

/** The message shown when a broken GPU overlay is bypassed at launch. */
export const OVERLAY_FALLBACK_MESSAGE =
  'GPU acceleration failed to load - running on CPU. Reinstall it from Settings.';

/** Injected effects for {@link launchWithOverlayFallback}, so it is unit-testable. */
export interface OverlayFallbackHooks {
  /** Spawn the backend (+ optional overlay) and load the UI; throws on startup failure. */
  start: (accel: Accel | null) => Promise<void>;
  /** Deactivate the active overlay (setActiveAccel(null)). Must NOT delete the dir. */
  deactivateOverlay: () => Promise<void>;
  /** Surface the fallback message to the user (dialog). */
  notify: (message: string) => void;
  /** Diagnostic logging; defaults to console.error. */
  log?: (message: string) => void;
  /** False when the failure is unrelated to the accelerator. */
  shouldFallback?: (error: unknown) => boolean;
}

/**
 * Launch the backend, falling back to the bundled CPU/Metal env when startup
 * fails WITH a GPU overlay active. A broken overlay must never prevent launch:
 * e.g. an overlay whose onnxruntime-gpu is the wrong CUDA generation makes
 * `import onnxruntime` throw at module load, which kills the whole backend
 * during startup (insightface imports it in pixlstash's import chain - live
 * incident 2026-07-20). In that case we deactivate the overlay (the dir is kept
 * on disk for reinstall/inspection - only the active-accel state is cleared),
 * tell the user, and retry once without it.
 *
 * Cannot loop by construction: the retry is the literal `hooks.start(null)` call
 * in the catch block - there is no recursion and no second catch, so if the
 * bundled-env launch also fails, that error propagates to the caller's existing
 * fatal path (a genuine packaging error, not an overlay problem). A failure with
 * `accel === null` rethrows immediately: no overlay was involved, so there is
 * nothing to fall back from.
 */
export async function launchWithOverlayFallback(
  accel: Accel | null,
  hooks: OverlayFallbackHooks,
): Promise<void> {
  try {
    await hooks.start(accel);
  } catch (e) {
    if (accel === null) throw e; // bundled env failed - nothing to fall back to
    if (hooks.shouldFallback?.(e) === false) throw e;
    const log = hooks.log ?? ((m: string) => console.error(m));
    log(
      `[overlay-fallback] backend startup failed with the ${accel} overlay active; ` +
        `deactivating it (dir kept for reinstall/inspection) and retrying on the ` +
        `bundled env: ${(e as Error).message}`,
    );
    await hooks.deactivateOverlay();
    hooks.notify(OVERLAY_FALLBACK_MESSAGE);
    // Exactly one retry, overlay-free. NOT wrapped in another try - a failure
    // here is a genuine packaging error and must reach the caller's fatal path.
    await hooks.start(null);
  }
}

/**
 * Manages the on-demand GPU wheel overlays. The bundled env (CPU on Windows/
 * Linux, Metal on macOS) always works; this installs torch/onnxruntime-gpu for a
 * discrete GPU into a `userData/backends/<accel>` directory that ServerProcess
 * injects via PYTHONPATH. Nothing is hosted by us - wheels come from PyPI and
 * PyTorch's index.
 */
export class BackendManager {
  async isInstalled(accel: Accel): Promise<boolean> {
    try {
      await access(overlayMarkerPath(accel));
      return true;
    } catch {
      return false;
    }
  }

  async listInstalled(): Promise<Accel[]> {
    const out: Accel[] = [];
    for (const accel of OVERLAY_ACCELS) {
      if (await this.isInstalled(accel)) out.push(accel);
    }
    return out;
  }

  async getActiveAccel(): Promise<Accel | null> {
    try {
      const state = JSON.parse(await readFile(activeAccelPath(), 'utf8'));
      return typeof state.accel === 'string' ? (state.accel as Accel) : null;
    } catch {
      return null;
    }
  }

  async setActiveAccel(accel: Accel | null): Promise<void> {
    if (accel === null) {
      await rm(activeAccelPath(), { force: true });
      return;
    }
    await writeFile(activeAccelPath(), JSON.stringify({ accel }, null, 2));
  }

  async remove(accel: Accel): Promise<void> {
    await rm(overlayDir(accel), { recursive: true, force: true });
    if ((await this.getActiveAccel()) === accel) await this.setActiveAccel(null);
  }

  /**
   * Install the GPU wheels for `accel` into a PYTHONPATH overlay, pinned to the
   * bundled torch/onnxruntime versions so they stay ABI-compatible with the
   * bundled env. torch/torchvision come from the accelerator index; their shared
   * pure-Python deps are pinned to the bundled versions via a constraints file,
   * so only the genuinely-new GPU/CUDA wheels are downloaded. Streams pip output
   * to onProgress; marks + activates the overlay on success. Throws on failure.
   */
  async installOverlay(
    accel: Accel,
    info: RuntimeInfo,
    onProgress: (p: InstallProgress) => void,
    appVersion: string,
  ): Promise<OverlayMeta> {
    if (!OVERLAY_ACCELS.includes(accel)) {
      throw new Error(`${accel} is provided by the bundled runtime, not an overlay`);
    }
    const dir = overlayDir(accel);
    await rm(dir, { recursive: true, force: true });
    await mkdir(dir, { recursive: true });

    onProgress({
      phase: 'prepare',
      message: 'Resolving environment…',
      fraction: -1,
      bytesDone: 0,
      bytesTotal: 0,
    });
    const constraints = join(dir, 'constraints.txt');
    const bundled = await this.bundledFreeze();
    await writeFile(constraints, bundled.constraints);

    const index = TORCH_INDEX[accel];
    const available = index ? await this.indexVersions('torch', index) : [];
    const { args, usedFallback } = buildOverlayPipArgs(
      accel,
      info,
      constraints,
      dir,
      available,
      pipIndexUrl(),
    );
    if (usedFallback) {
      onProgress({
        bytesDone: 0,
        bytesTotal: 0,
        phase: 'prepare',
        message: `torch ${basePep440(info.torch)} isn't on the GPU index; using ${available[0]}`,
        fraction: -1,
      });
    }

    await this.runPip(args, onProgress);

    onProgress({
      phase: 'install',
      message: 'Removing duplicate packages…',
      fraction: -1,
      bytesDone: 0,
      bytesTotal: 0,
    });
    await this.pruneOverlay(dir, bundled.versions);

    const meta: OverlayMeta = {
      accel,
      torch: info.torch,
      installedAt: new Date().toISOString(),
      appVersion,
    };
    await writeFile(overlayMarkerPath(accel), JSON.stringify(meta, null, 2));
    await this.setActiveAccel(accel);
    onProgress({
      phase: 'done',
      message: `${ACCEL_LABELS[accel]} ready`,
      fraction: 1,
      bytesDone: 0,
      bytesTotal: 0,
    });
    return meta;
  }

  /**
   * Delete every package in the overlay that duplicates one the bundled env
   * already provides, keeping the GPU wheels the overlay exists for and anything
   * the bundle lacks. See {@link planOverlayPrune} for why the duplicates are
   * actively harmful rather than merely wasteful.
   *
   * Driven by each distribution's own RECORD, so a package's files go with its
   * metadata and nothing is left half-deleted. Returns the names removed.
   */
  async pruneOverlay(dir: string, bundled: ReadonlyMap<string, string>): Promise<string[]> {
    let entries: string[];
    try {
      entries = await readdir(dir);
    } catch (e) {
      // No overlay directory means there is nothing to prune - that is the
      // not-installed case repairIfStale asks about, not a failure.
      if ((e as NodeJS.ErrnoException).code === 'ENOENT') return [];
      // Anything else (unreadable, not a directory, permissions) means we cannot
      // tell whether duplicates are present. Reporting success here would let
      // installOverlay mark and activate an unpruned overlay, which is exactly
      // the shadowing this method exists to prevent - so it fails the install.
      // repairIfStale catches it separately so a launch is never blocked.
      throw new Error(`Cannot prune the overlay at ${dir}: ${(e as Error).message}`);
    }
    const dists = entries
      .map(parseDistInfoDir)
      .filter((d): d is DistInfo => d !== null);
    const { remove } = planOverlayPrune(dists, bundled);

    const removed: string[] = [];
    // Directories the deletions may have emptied, deepest first. They cannot be
    // left behind: an empty directory on sys.path is an implicit namespace
    // package (PEP 420), so an emptied `numpy/` still shadows the bundle's real
    // numpy - the very failure this prune exists to remove, in a quieter form.
    const emptied = new Set<string>();
    for (const dist of remove) {
      const distInfo = join(dir, dist.dirName);
      try {
        const record = await readFile(join(distInfo, 'RECORD'), 'utf8');
        for (const file of recordedFilesWithin(dir, record)) {
          await rm(file, { force: true });
          for (let d = dirname(file); d.startsWith(dir) && d !== dir; d = dirname(d)) {
            emptied.add(d);
          }
        }
      } catch (e) {
        // No RECORD (or an unreadable one) means we cannot know which files are
        // this distribution's. Leaving the dist-info in place keeps the overlay
        // describing itself honestly, so a later prune can retry.
        console.warn(
          `[overlay] skipping ${dist.name} ${dist.version}: RECORD unreadable (${(e as Error).message})`,
        );
        continue;
      }
      await rm(distInfo, { recursive: true, force: true });
      removed.push(`${dist.name} ${dist.version}`);
    }
    // Deepest first, so a directory is retried only after its children are gone.
    // rmdir refuses a non-empty one, which is exactly the wanted behaviour: a
    // directory still holding a kept package's files must survive.
    for (const d of [...emptied].sort((a, b) => b.length - a.length)) {
      try {
        await rmdir(d);
      } catch {
        // Non-empty (or already gone): another distribution still lives here.
      }
    }
    if (removed.length) {
      console.log(`[overlay] pruned ${removed.length} bundled duplicates: ${removed.join(', ')}`);
    }
    return removed;
  }

  /**
   * Re-prune an overlay that was built against a different app version.
   *
   * The bundled site-packages is replaced wholesale by an app update, so an
   * overlay's duplicates stop matching what the bundle now holds - which is the
   * shadowing failure in {@link planOverlayPrune}, arriving without anyone
   * touching the overlay. Pruning against the *current* bundle repairs it in
   * place, with no re-download: the duplicates are what break, and the bundle
   * already has correct copies of every one of them.
   *
   * An overlay whose marker predates pruning has no `appVersion` at all, which
   * reads as stale and gets the same repair - that is how already-installed
   * overlays are fixed.
   *
   * Never throws: a failed repair must not stop the app from launching, since a
   * broken overlay still falls back to the bundled env.
   */
  async repairIfStale(accel: Accel, appVersion: string): Promise<boolean> {
    let meta: OverlayMeta;
    try {
      meta = JSON.parse(await readFile(overlayMarkerPath(accel), 'utf8')) as OverlayMeta;
    } catch {
      return false; // not installed
    }
    if (meta.appVersion === appVersion) return false;
    try {
      const { versions } = await this.bundledFreeze();
      const removed = await this.pruneOverlay(overlayDir(accel), versions);
      await writeFile(
        overlayMarkerPath(accel),
        JSON.stringify({ ...meta, appVersion }, null, 2),
      );
      console.log(
        `[overlay] ${accel} rebuilt for app ${appVersion} (was ${meta.appVersion ?? 'unpruned'}), ` +
          `${removed.length} duplicates removed`,
      );
      return true;
    } catch (e) {
      console.warn(`[overlay] could not re-prune ${accel}: ${(e as Error).message}`);
      return false;
    }
  }

  /**
   * `pip freeze` of the bundled env, as both the install constraints (minus the
   * torch/onnx packages, which are overridden per accelerator) and the version
   * map the post-install prune compares against. One capture serves both, so the
   * pins and the prune can never disagree about what the bundle holds.
   */
  private async bundledFreeze(): Promise<{ constraints: string; versions: Map<string, string> }> {
    const frozen = await this.capture(bundledInterpreter(), ['-m', 'pip', 'freeze']);
    const versions = parseFreezeVersions(frozen);
    if (versions.size === 0) {
      // Every bundled env has dependencies; an empty freeze means we did not read
      // one. Installing on it would pin nothing and prune nothing, which is the
      // silent-wrong-versions failure this whole path exists to prevent.
      throw new Error('Could not read the bundled environment (pip freeze returned nothing)');
    }
    return { constraints: filterConstraintsFreeze(frozen), versions };
  }

  /** Public (local-tag-stripped) versions of `pkg` on `index`, newest first. */
  private async indexVersions(pkg: string, index: string): Promise<string[]> {
    try {
      const out = await this.capture(bundledInterpreter(), [
        '-m',
        'pip',
        'index',
        'versions',
        pkg,
        '--index-url',
        index,
      ]);
      const line = out.split(/\r?\n/).find((l) => /available versions:/i.test(l));
      if (!line) return [];
      return line
        .replace(/.*available versions:\s*/i, '')
        .split(',')
        .map((v) => basePep440(v.trim()))
        .filter(Boolean);
    } catch {
      return [];
    }
  }

  /**
   * Run `cmd` and return its complete stdout.
   *
   * Resolves on 'close', NOT on 'exit'. 'exit' fires when the process ends,
   * which can precede its stdout pipe being drained - so resolving there can
   * return a *truncated* capture with no error of any kind. The one caller that
   * matters is `pip freeze`, whose output feeds the install constraints file,
   * and whose output is alphabetically sorted: a truncated capture silently
   * drops the tail of the alphabet, leaving exactly those packages unpinned
   * while every earlier one looks correctly constrained. 'close' fires only
   * once all stdio streams are closed, so the capture is whole.
   */
  private capture(cmd: string, args: string[]): Promise<string> {
    return new Promise((resolve, reject) => {
      const child = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'] });
      let out = '';
      let err = '';
      child.stdout.on('data', (d) => (out += d.toString()));
      child.stderr.on('data', (d) => (err += d.toString()));
      child.on('error', reject);
      child.on('close', (code, signal) => {
        if (code === 0) {
          resolve(out);
          return;
        }
        // A signalled child reports code null, so name the signal instead of
        // rejecting with "exited null".
        const how = code === null ? `was killed by ${signal}` : `exited ${code}`;
        const detail = err.trim() ? `: ${err.trim().slice(-500)}` : '';
        reject(new Error(`${cmd} ${args.join(' ')} ${how}${detail}`));
      });
    });
  }

  private runPip(args: string[], onProgress: (p: InstallProgress) => void): Promise<void> {
    return new Promise((resolve, reject) => {
      mkdirSync(dirname(installLogPath()), { recursive: true });
      const log = createWriteStream(installLogPath(), { flags: 'a' });
      log.write(`\n=== ${new Date().toISOString()} pip ${args.join(' ')} ===\n`);
      // Keep the tail of pip's own output so a failure reports the real cause
      // (e.g. an unresolvable version) instead of a bare exit code.
      let tail = '';
      // What pip has named so far, and what it has finished. A wheel counts as
      // done when the next one is announced or the install phase starts, which
      // is the only completion signal a piped pip gives.
      let bytesTotal = 0;
      let bytesDone = 0;
      let inFlight = 0;
      const child = spawn(bundledInterpreter(), args, { stdio: ['ignore', 'pipe', 'pipe'] });
      const onChunk = (raw: string) => {
        log.write(raw);
        tail = (tail + raw).slice(-2000);
        for (const line of raw.split(/\r?\n/)) {
          const t = line.trim();
          if (!t) continue;
          const dl = t.match(/(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)\s*([KMG]B)/i);
          if (/^Downloading\b/i.test(t) || dl) {
            const frac = dl ? Number(dl[1]) / Number(dl[2]) : -1;
            if (/^Downloading\b/i.test(t)) {
              bytesDone += inFlight;
              inFlight = announcedBytes(t);
              bytesTotal += inFlight;
            }
            onProgress({
              phase: 'download',
              message: prettyDownload(t),
              fraction: Number.isFinite(frac) ? frac : -1,
              bytesDone,
              bytesTotal,
            });
          } else if (/^Installing collected packages/i.test(t)) {
            bytesDone += inFlight;
            inFlight = 0;
            onProgress({
              phase: 'install',
              message: 'Installing…',
              fraction: -1,
              bytesDone,
              bytesTotal,
            });
          } else if (/^Collecting\b/i.test(t)) {
            onProgress({
              phase: 'prepare',
              message: t.slice(0, 120),
              fraction: -1,
              bytesDone,
              bytesTotal,
            });
          }
        }
      };
      child.stdout.on('data', (d) => onChunk(d.toString()));
      child.stderr.on('data', (d) => onChunk(d.toString()));
      child.on('error', (e) => {
        log.end();
        reject(e);
      });
      child.on('exit', (code) => {
        log.end();
        if (code === 0) {
          resolve();
          return;
        }
        const detail = tail.trim() ? `\n\n${tail.trim()}` : '';
        reject(
          new Error(`pip install failed (exit ${code}).${detail}\n\nFull log: ${installLogPath()}`),
        );
      });
    });
  }
}
