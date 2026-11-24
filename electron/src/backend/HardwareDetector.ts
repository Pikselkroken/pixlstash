import { execFile } from 'node:child_process';
import { readdir, readFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import { Accel, ACCEL_LABELS } from '../config';

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

/**
 * The oldest driver branch that runs CUDA 13 (580, both Linux and Windows).
 * Below it the cu130 torch wheels cannot initialise CUDA at all, so the overlay
 * would be a multi-GB download that runs on the CPU.
 */
export const MIN_CUDA13_DRIVER = 580;

/**
 * True unless `driverVersion` ("615.71.09", "581.57") is a branch older than
 * {@link MIN_CUDA13_DRIVER}. An unreadable version answers true: the install
 * still works and the backend falls back to CPU on its own, so a parse miss
 * must not hide the GPU.
 */
export function driverRunsCuda13(driverVersion: string | undefined): boolean {
  const major = Number.parseInt(driverVersion ?? '', 10);
  return Number.isNaN(major) || major >= MIN_CUDA13_DRIVER;
}

async function nvidiaGpu(): Promise<{ name: string; driver?: string } | null> {
  try {
    const { stdout } = await execFileP(
      'nvidia-smi',
      ['--query-gpu=name,driver_version', '--format=csv,noheader'],
      { timeout: 4000 },
    );
    const line = stdout.split('\n').find((l) => l.trim().length > 0);
    if (!line) return { name: 'NVIDIA GPU' };
    // "NVIDIA GeForce RTX 5090, 615.71.09": the first GPU's model and driver.
    const comma = line.lastIndexOf(',');
    if (comma < 0) return { name: line.trim() || 'NVIDIA GPU' };
    return { name: line.slice(0, comma).trim() || 'NVIDIA GPU', driver: line.slice(comma + 1).trim() };
  } catch {
    return null;
  }
}

/** PCI vendor ID for AMD/ATI, as written in each /sys/bus/pci/devices entry. */
const AMD_PCI_VENDOR = '0x1002';

/**
 * Cheap, Linux-only sysfs probe for an AMD GPU when `rocminfo` isn't installed.
 * Reads the PCI vendor ID of each device and looks for AMD (0x1002) on a display/
 * VGA-class device. This is a few small synchronous-ish file reads under
 * /sys/bus/pci/devices, no process spawn, so it's safe to run on the boot path.
 */
async function hasAmdPciDevice(): Promise<boolean> {
  try {
    const devices = await readdir('/sys/bus/pci/devices');
    for (const dev of devices) {
      const base = `/sys/bus/pci/devices/${dev}`;
      let vendor: string;
      try {
        vendor = (await readFile(`${base}/vendor`, 'utf8')).trim().toLowerCase();
      } catch {
        continue; // device disappeared / unreadable; skip it
      }
      if (vendor !== AMD_PCI_VENDOR) continue;
      // PCI class 0x03xxxx == display controller (VGA/3D). Restrict to GPUs so a
      // non-GPU AMD device (chipset, audio) doesn't get mistaken for a GPU.
      try {
        const cls = (await readFile(`${base}/class`, 'utf8')).trim().toLowerCase();
        if (cls.startsWith('0x03')) return true;
      } catch {
        // No class file but vendor is AMD - treat as a candidate GPU.
        return true;
      }
    }
  } catch (e) {
    // /sys not present (non-Linux, container) - not an error worth surfacing.
    console.warn('[hardware] could not scan /sys/bus/pci/devices for an AMD GPU:', e);
  }
  return false;
}

async function hasAmdRocmGpu(): Promise<string | null> {
  // ROCm is Linux-only for our bundles. Probe rocminfo first; if it isn't
  // installed, fall back to the /sys PCI vendor ID (0x1002 = AMD).
  if (currentOs() !== 'linux') return null;
  try {
    const { stdout } = await execFileP('rocminfo', [], { timeout: 4000 });
    if (/gfx\d+/i.test(stdout)) return 'AMD GPU (ROCm)';
  } catch {
    /* rocminfo not installed - fall through to the sysfs vendor probe */
  }
  if (await hasAmdPciDevice()) return 'AMD GPU (ROCm)';
  return null;
}

/**
 * Detect the OS/arch and which compute accelerators this machine can use, in
 * priority order. The LM Studio model: we recommend the first accelerator for
 * which a catalog bundle exists, but always offer CPU as a universal fallback.
 *
 * `forcedAccel` (from the `--force-backend=` flag / `PIXLSTASH_FORCE_BACKEND`
 * env var, parsed and validated by {@link parseForcedBackend}) fakes the GPU
 * probe so the download/overlay flow can be tested without the matching GPU. It
 * is injected into `accelerators` ahead of CPU (deduped, real probes still run
 * and keep their priority), and sets a synthetic `gpuName`. It never influences
 * the install index - the overlay still resolves through the hardcoded
 * TORCH_INDEX map keyed by this validated `Accel`.
 */
export async function detectHardware(forcedAccel: Accel | null = null): Promise<Hardware> {
  const os = currentOs();
  const arch = currentArch();
  const accelerators: Accel[] = [];
  let gpuName: string | undefined;

  // Apple Silicon → Metal (torch MPS) ships in the standard macOS wheel.
  if (os === 'mac' && arch === 'arm64') {
    accelerators.push('metal');
  }

  const nvidia = await nvidiaGpu();
  if (nvidia && os !== 'mac') {
    if (driverRunsCuda13(nvidia.driver)) {
      accelerators.push('cu130');
      gpuName = nvidia.name;
    } else {
      // Not offered: torch cu130 would install and then never see the GPU.
      // An existing CUDA 12.8 overlay keeps working (see RETIRED_ACCELS).
      console.warn(
        `[hardware] ${nvidia.name} has driver ${nvidia.driver}; GPU acceleration needs ` +
          `driver ${MIN_CUDA13_DRIVER} or newer (CUDA 13), so it is not offered`,
      );
    }
  }

  const rocm = await hasAmdRocmGpu();
  if (rocm) {
    accelerators.push('rocm');
    gpuName = gpuName ?? rocm;
  }

  // Inject the forced accelerator (if any) as available, keeping the existing
  // priority ordering: real probes above keep their slot; the forced one is
  // added only if not already detected, just ahead of the CPU fallback. 'cpu'
  // is always present below, so forcing 'cpu' is a no-op on the list.
  if (forcedAccel && forcedAccel !== 'cpu' && !accelerators.includes(forcedAccel)) {
    accelerators.push(forcedAccel);
    gpuName = gpuName ?? `${ACCEL_LABELS[forcedAccel]} [forced]`;
  }

  accelerators.push('cpu');
  return { os, arch, accelerators, gpuName };
}

/**
 * GPU accelerators worth offering on top of the bundled env. The installer
 * already ships a working `bundledAccel` (cpu on Windows/Linux, metal on macOS);
 * this returns the detected discrete-GPU accelerators (cu130/rocm) that need an
 * on-demand wheel overlay. Empty when the bundled env already covers the GPU
 * (e.g. Metal on Apple Silicon).
 */
export function gpuUpgrades(hw: Hardware, bundledAccel: Accel): Accel[] {
  return hw.accelerators.filter((a) => a !== 'cpu' && a !== bundledAccel);
}
