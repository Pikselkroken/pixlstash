import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { Accel } from '../config';

const execFileP = promisify(execFile);

export interface Hardware {
  os: 'win' | 'mac' | 'linux';
  arch: 'x64' | 'arm64';
  /** Accelerators available on this machine, best-first. Always ends with 'cpu'. */
  accelerators: Accel[];
  /** GPU name when detected, for display. */
  gpuName?: string;
}

function currentOs(): Hardware['os'] {
  if (process.platform === 'win32') return 'win';
  if (process.platform === 'darwin') return 'mac';
  return 'linux';
}

function currentArch(): Hardware['arch'] {
  return process.arch === 'arm64' ? 'arm64' : 'x64';
}

async function hasNvidiaGpu(): Promise<string | null> {
  try {
    const { stdout } = await execFileP('nvidia-smi', ['-L'], { timeout: 4000 });
    const line = stdout.split('\n').find((l) => l.trim().length > 0);
    if (!line) return 'NVIDIA GPU';
    // `nvidia-smi -L` → "GPU 0: NVIDIA GeForce RTX 5090 (UUID: GPU-…)". Keep just
    // the model name; the device index and UUID mean nothing to the user.
    const name = line
      .replace(/^GPU\s+\d+:\s*/, '')
      .replace(/\s*\(UUID:[^)]*\)\s*$/, '')
      .trim();
    return name || 'NVIDIA GPU';
  } catch {
    return null;
  }
}

async function hasAmdRocmGpu(): Promise<string | null> {
  // ROCm is Linux-only for our bundles. Probe rocminfo; fall back to /sys vendor.
  if (currentOs() !== 'linux') return null;
  try {
    const { stdout } = await execFileP('rocminfo', [], { timeout: 4000 });
    if (/gfx\d+/i.test(stdout)) return 'AMD GPU (ROCm)';
  } catch {
    /* rocminfo not installed — fall through */
  }
  return null;
}

/**
 * Detect the OS/arch and which compute accelerators this machine can use, in
 * priority order. The LM Studio model: we recommend the first accelerator for
 * which a catalog bundle exists, but always offer CPU as a universal fallback.
 */
export async function detectHardware(): Promise<Hardware> {
  const os = currentOs();
  const arch = currentArch();
  const accelerators: Accel[] = [];
  let gpuName: string | undefined;

  // Apple Silicon → Metal (torch MPS) ships in the standard macOS wheel.
  if (os === 'mac' && arch === 'arm64') {
    accelerators.push('metal');
  }

  const nvidia = await hasNvidiaGpu();
  if (nvidia && os !== 'mac') {
    accelerators.push('cu128');
    gpuName = nvidia;
  }

  const rocm = await hasAmdRocmGpu();
  if (rocm) {
    accelerators.push('rocm');
    gpuName = gpuName ?? rocm;
  }

  accelerators.push('cpu');
  return { os, arch, accelerators, gpuName };
}

/**
 * GPU accelerators worth offering on top of the bundled env. The installer
 * already ships a working `bundledAccel` (cpu on Windows/Linux, metal on macOS);
 * this returns the detected discrete-GPU accelerators (cu128/rocm) that need an
 * on-demand wheel overlay. Empty when the bundled env already covers the GPU
 * (e.g. Metal on Apple Silicon).
 */
export function gpuUpgrades(hw: Hardware, bundledAccel: Accel): Accel[] {
  return hw.accelerators.filter((a) => a !== 'cpu' && a !== bundledAccel);
}
