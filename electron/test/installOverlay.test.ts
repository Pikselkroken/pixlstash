import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { buildOverlayPipArgs } from '../src/backend/BackendManager';
import { ORT_GPU_PIN, RuntimeInfo, TORCH_INDEX } from '../src/config';

/** A bundled runtime whose torch matches what the rocm7.1 index publishes. */
const ROCM_RUNTIME: RuntimeInfo = {
  accel: 'cpu',
  torch: '2.10.0+cpu',
  torchvision: '0.25.0+cpu',
  onnxruntime: '1.20.0',
};

/** Find the value immediately following a flag in an args array. */
function flagValue(args: string[], flag: string): string | undefined {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1] : undefined;
}

describe('buildOverlayPipArgs — rocm overlay', () => {
  it('uses the hardcoded rocm7.1 index-url and exact-pins torch 2.10.0', () => {
    // torch 2.10.0 exists on the rocm7.1 index (cp310-cp314 manylinux_2_28_x86_64),
    // so the exact-match branch fires, not the lagging-index fallback.
    const { args, usedFallback } = buildOverlayPipArgs(
      'rocm',
      ROCM_RUNTIME,
      '/tmp/overlay/constraints.txt',
      '/tmp/overlay',
      ['2.10.0', '2.9.1', '2.9.0'],
      undefined,
    );

    assert.equal(usedFallback, false, 'exact match must not trigger the lagging-index fallback');
    assert.equal(
      flagValue(args, '--index-url'),
      'https://download.pytorch.org/whl/rocm7.1',
      'index-url must be the hardcoded TORCH_INDEX.rocm entry',
    );
    // Exact public-version pin, local "+cpu" tag stripped.
    assert.ok(args.includes('torch==2.10.0'), 'torch must be exact-pinned to 2.10.0');
    assert.ok(args.includes('torchvision==0.25.0'), 'torchvision exact-pinned to bundled version');
    // The index-url is exactly the one from config — never caller-derived.
    assert.equal(flagValue(args, '--index-url'), TORCH_INDEX.rocm);
  });

  it('rocm is CPU-ORT, so no onnxruntime-gpu pin is added', () => {
    const { args } = buildOverlayPipArgs('rocm', ROCM_RUNTIME, 'c.txt', 'd', ['2.10.0'], undefined);
    assert.ok(!args.some((a) => a.startsWith('onnxruntime')), 'rocm reuses bundled CPU ORT');
  });

  it('falls back to the index newest only when the exact torch is absent', () => {
    const { args, usedFallback } = buildOverlayPipArgs(
      'rocm',
      ROCM_RUNTIME,
      'c.txt',
      'd',
      ['2.9.1', '2.9.0'], // 2.10.0 not published on the index
      undefined,
    );
    assert.equal(usedFallback, true);
    assert.ok(args.includes('torch==2.9.1'), 'falls back to the index newest');
    assert.ok(args.includes('torchvision'), 'lets pip pick the matching torchvision');
    assert.ok(!args.includes('torchvision==0.25.0'), 'no exact torchvision pin in fallback');
  });

  it('cu128 overlay pins onnxruntime-gpu to its CUDA generation, NOT the bundle version', () => {
    const { args } = buildOverlayPipArgs(
      'cu128',
      { accel: 'cpu', torch: '2.10.0+cpu', torchvision: '0.25.0+cpu', onnxruntime: '1.20.0' },
      'c.txt',
      'd',
      ['2.10.0'],
      undefined,
    );
    assert.equal(flagValue(args, '--index-url'), TORCH_INDEX.cu128);
    // Regression for the 2026-07-20 incident: inheriting the bundle's ORT version
    // ignores the CUDA-flavor axis — PyPI's onnxruntime-gpu 1.27.0 is a CUDA 13
    // build (libcudart.so.13), which ImportErrors on the cu12 overlay stack and
    // kills the whole backend at startup. The cu128 overlay must pin the last
    // CUDA-12 PyPI build from ORT_GPU_PIN instead.
    assert.equal(ORT_GPU_PIN.cu128, '1.26.0', 'cu128 pin is the last CUDA-12 PyPI build');
    assert.ok(args.includes(`onnxruntime-gpu==${ORT_GPU_PIN.cu128}`), 'uses the per-accel CUDA-generation pin');
    assert.ok(
      !args.includes('onnxruntime-gpu==1.20.0'),
      "the bundle's onnxruntime version must NOT leak into the gpu package pin",
    );
  });

  it('a corporate pip mirror becomes --extra-index-url, never the primary GPU index', () => {
    const { args } = buildOverlayPipArgs(
      'rocm',
      ROCM_RUNTIME,
      'c.txt',
      'd',
      ['2.10.0'],
      'https://mirror.corp/simple',
    );
    // GPU index stays primary; the mirror is only an extra fallback index.
    assert.equal(flagValue(args, '--index-url'), TORCH_INDEX.rocm);
    assert.equal(flagValue(args, '--extra-index-url'), 'https://mirror.corp/simple');
  });
});

describe('filterConstraintsFreeze', () => {
  // Regression for the 2026-07-20 live failure: the bundled env froze
  // setuptools==83.0.0 while the CUDA torch wheels declare `setuptools<82`,
  // making every overlay install ResolutionImpossible. Build tooling must
  // never appear in the constraints.
  it('drops build tooling (setuptools/pip/wheel) so torch metadata cannot conflict', async () => {
    const { filterConstraintsFreeze } = await import('../src/backend/BackendManager');
    const frozen = [
      'numpy==2.4.4',
      'setuptools==83.0.0',
      'pip==25.1',
      'wheel==0.45.0',
      'pillow==12.3.0',
    ].join('\n');
    assert.equal(filterConstraintsFreeze(frozen), 'numpy==2.4.4\npillow==12.3.0\n');
  });

  it('drops overlay-owned dists, direct references, and option/comment lines', async () => {
    const { filterConstraintsFreeze } = await import('../src/backend/BackendManager');
    const frozen = [
      'torch==2.13.0+cpu',
      'torchvision==0.28.0+cpu',
      'onnxruntime==1.27.0',
      'pixlstash @ file:///build/pixlstash-1.7.0-py3-none-any.whl',
      '-e /some/editable',
      '# a comment',
      'jinja2==3.1.6',
    ].join('\n');
    assert.equal(filterConstraintsFreeze(frozen), 'jinja2==3.1.6\n');
  });

  it('does not over-drop packages that merely start with a dropped name', async () => {
    const { filterConstraintsFreeze } = await import('../src/backend/BackendManager');
    // e.g. "pipdeptree" / "wheel-filename" must survive the pip/wheel drop.
    const frozen = ['pipdeptree==2.23.0', 'wheel-filename==1.4.0', 'setuptools-scm==8.1.0'].join('\n');
    assert.equal(
      filterConstraintsFreeze(frozen),
      'pipdeptree==2.23.0\nwheel-filename==1.4.0\nsetuptools-scm==8.1.0\n',
    );
  });
});
