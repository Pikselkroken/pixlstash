import { spawn } from 'node:child_process';
import { createWriteStream, mkdirSync } from 'node:fs';
import { mkdir, rm, readFile, writeFile, access } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import {
  Accel,
  ACCEL_LABELS,
  ONNX_PACKAGE,
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
 * "Downloading torch (820.3 MB)" — just the package name (no version/platform
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
}

export interface OverlayMeta {
  accel: Accel;
  torch: string;
  installedAt: string;
}

/** GPU accelerators that can be layered on top of the bundled CPU/Metal env. */
export const OVERLAY_ACCELS: Accel[] = ['cu128', 'rocm'];

/**
 * Build the `pip install` argument list for a GPU overlay. Pure (no I/O) so the
 * install contract is unit-testable: which index-url is used, how torch/
 * torchvision are pinned, and the exact-match-vs-lagging-index fallback.
 *
 * The index ALWAYS comes from the hardcoded {@link TORCH_INDEX} map keyed by the
 * validated `Accel` — never from caller-supplied data. `availableTorch` is the
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
  // bundled CPU build — its torch version isn't published there yet — fall back
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
    args.push(`onnxruntime-gpu==${basePep440(info.onnxruntime)}`);
  }
  return { args, usedFallback };
}

/**
 * Manages the on-demand GPU wheel overlays. The bundled env (CPU on Windows/
 * Linux, Metal on macOS) always works; this installs torch/onnxruntime-gpu for a
 * discrete GPU into a `userData/backends/<accel>` directory that ServerProcess
 * injects via PYTHONPATH. Nothing is hosted by us — wheels come from PyPI and
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
  ): Promise<OverlayMeta> {
    if (!OVERLAY_ACCELS.includes(accel)) {
      throw new Error(`${accel} is provided by the bundled runtime, not an overlay`);
    }
    const dir = overlayDir(accel);
    await rm(dir, { recursive: true, force: true });
    await mkdir(dir, { recursive: true });

    onProgress({ phase: 'prepare', message: 'Resolving environment…', fraction: -1 });
    const constraints = join(dir, 'constraints.txt');
    await writeFile(constraints, await this.bundledConstraints());

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
        phase: 'prepare',
        message: `torch ${basePep440(info.torch)} isn't on the GPU index; using ${available[0]}`,
        fraction: -1,
      });
    }

    await this.runPip(args, onProgress);

    const meta: OverlayMeta = { accel, torch: info.torch, installedAt: new Date().toISOString() };
    await writeFile(overlayMarkerPath(accel), JSON.stringify(meta, null, 2));
    await this.setActiveAccel(accel);
    onProgress({ phase: 'done', message: `${ACCEL_LABELS[accel]} ready`, fraction: 1 });
    return meta;
  }

  /**
   * `pip freeze` of the bundled env, minus the torch/onnx packages (those are
   * overridden per accelerator). Used as install constraints so overlay deps
   * resolve to the exact versions already in the bundled env.
   */
  private async bundledConstraints(): Promise<string> {
    const frozen = await this.capture(bundledInterpreter(), ['-m', 'pip', 'freeze']);
    const drop = /^(torch|torchvision|onnxruntime|onnxruntime-gpu)(==|@|\s|$)/i;
    const kept = frozen
      .split(/\r?\n/)
      .map((l) => l.trim())
      // Constraints files reject direct references (``pkg @ url`` / local paths —
      // e.g. the pixlstash wheel and the spaCy model) and option/comment lines,
      // so keep only plain ``name==version`` pins.
      .filter(
        (l) =>
          l &&
          !l.startsWith('-') &&
          !l.startsWith('#') &&
          !l.includes(' @ ') &&
          !drop.test(l),
      );
    return kept.join('\n') + '\n';
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

  private capture(cmd: string, args: string[]): Promise<string> {
    return new Promise((resolve, reject) => {
      const child = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'] });
      let out = '';
      child.stdout.on('data', (d) => (out += d.toString()));
      child.on('error', reject);
      child.on('exit', (code) =>
        code === 0 ? resolve(out) : reject(new Error(`${cmd} pip freeze exited ${code}`)),
      );
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
            onProgress({
              phase: 'download',
              message: prettyDownload(t),
              fraction: Number.isFinite(frac) ? frac : -1,
            });
          } else if (/^Installing collected packages/i.test(t)) {
            onProgress({ phase: 'install', message: 'Installing…', fraction: -1 });
          } else if (/^Collecting\b/i.test(t)) {
            onProgress({ phase: 'prepare', message: t.slice(0, 120), fraction: -1 });
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
