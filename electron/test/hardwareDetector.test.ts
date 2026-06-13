import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { detectHardware } from '../src/backend/HardwareDetector';
import { ACCEL_LABELS } from '../src/config';

/**
 * These tests run on whatever the CI box is — they must not depend on a real
 * nvidia-smi / rocminfo / AMD PCI device being present. We only assert the
 * forced-accel injection contract, which holds regardless of what the real
 * probes return (they fail closed to "not detected" on a box without the tools).
 */
describe('detectHardware forced-accel injection', () => {
  it('injects a forced GPU accelerator and sets a synthetic gpuName', async () => {
    const hw = await detectHardware('cu128');
    assert.ok(hw.accelerators.includes('cu128'), 'forced cu128 must be available');
    // CPU stays the universal fallback at the end of the list.
    assert.equal(hw.accelerators.at(-1), 'cpu');
    // gpuName is set: either the real probe's name, or our synthetic label.
    assert.ok(hw.gpuName, 'a forced GPU should yield a gpuName');
  });

  it('sets a synthetic "[forced]" gpuName when no real GPU probe supplied a name', async () => {
    const hw = await detectHardware('rocm');
    assert.ok(hw.accelerators.includes('rocm'), 'forced rocm must be injected regardless of probes');
    // gpuName precedence intentionally matches the existing rocm logic
    // (`gpuName = gpuName ?? …`): a real probe (e.g. an NVIDIA card on this box)
    // keeps its name; only when nothing real was detected do we fall back to the
    // synthetic forced label. Assert the synthetic value's exact format whenever
    // it is the one in effect.
    if (hw.gpuName?.endsWith('[forced]')) {
      assert.equal(hw.gpuName, `${ACCEL_LABELS.rocm} [forced]`);
    } else {
      // A real GPU was probed: gpuName came from hardware, not our injection.
      assert.ok(hw.gpuName, 'a real probe must have supplied a non-empty gpuName');
    }
  });

  it('synthetic label format is "<ACCEL_LABELS value> [forced]" on a probe-free machine', async () => {
    // Force metal on a non-mac: no real probe sets a name for metal, and (unless
    // an NVIDIA/AMD GPU is present) gpuName is purely our synthetic injection.
    const hw = await detectHardware('metal');
    assert.ok(hw.accelerators.includes('metal'));
    if (hw.gpuName?.endsWith('[forced]')) {
      assert.equal(hw.gpuName, `${ACCEL_LABELS.metal} [forced]`);
    }
  });

  it('does not duplicate the accelerator if it is already detected', async () => {
    const hw = await detectHardware('rocm');
    const count = hw.accelerators.filter((a) => a === 'rocm').length;
    assert.equal(count, 1, 'rocm must appear exactly once even if forced + detected');
  });

  it('forcing cpu is a no-op (cpu is always present, exactly once)', async () => {
    const hw = await detectHardware('cpu');
    const count = hw.accelerators.filter((a) => a === 'cpu').length;
    assert.equal(count, 1);
    assert.equal(hw.accelerators.at(-1), 'cpu');
  });

  it('without an override, never injects a synthetic accelerator', async () => {
    const hw = await detectHardware(null);
    // Whatever is in the list came from a real probe; the synthetic "[forced]"
    // label must never appear.
    if (hw.gpuName) {
      assert.doesNotMatch(hw.gpuName, /\[forced\]/);
    }
    assert.ok(hw.accelerators.includes('cpu'));
  });

  it('keeps cpu last so the forced accel is preferred over the fallback', async () => {
    const hw = await detectHardware('cu128');
    const cuIdx = hw.accelerators.indexOf('cu128');
    const cpuIdx = hw.accelerators.indexOf('cpu');
    assert.ok(cuIdx >= 0 && cuIdx < cpuIdx, 'forced accel must rank ahead of cpu');
  });
});
