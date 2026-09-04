import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { mkdtemp, mkdir, writeFile, readdir, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import {
  BackendManager,
  DistInfo,
  normalizeDistName,
  overlayOwns,
  parseDistInfoDir,
  parseFreezeVersions,
  planOverlayPrune,
  recordedFilesWithin,
} from '../src/backend/BackendManager';

/** Shorthand for a distribution as it appears in an overlay. */
function dist(name: string, version: string): DistInfo {
  return { name, version, dirName: `${name}-${version}.dist-info` };
}

/** True when `path` exists. */
async function exists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

describe('planOverlayPrune', () => {
  it('removes the duplicate that caused the 2026-09-03 Windows failure', () => {
    // The overlay carried typing_extensions 4.15.0 over a bundle holding 4.16.0.
    // PYTHONPATH puts the overlay first, so the bundle's own anyio imported 4.15
    // and raised ImportError: cannot import name 'sentinel' - which exists only
    // in 4.16+ - killing the backend during the fastapi import chain.
    const overlay = [dist('typing_extensions', '4.15.0'), dist('torch', '2.13.0+cu128')];
    const bundled = new Map([
      ['typing-extensions', '4.16.0'],
      ['torch', '2.13.0+cpu'],
    ]);

    const { remove, keep } = planOverlayPrune(overlay, bundled);

    assert.deepEqual(
      remove.map((d) => d.name),
      ['typing_extensions'],
      'the shadowing duplicate must go',
    );
    assert.deepEqual(
      keep.map((d) => d.name),
      ['torch'],
      'torch is what the overlay exists for, even though the bundle also has one',
    );
  });

  it('keeps every GPU wheel and drops every shared dependency', () => {
    // The real closure of `pip install --target torch torchvision onnxruntime-gpu`:
    // three wheels asked for, a dozen dependencies that duplicate the bundle.
    const overlay = [
      dist('torch', '2.13.0'),
      dist('torchvision', '0.27.1'),
      dist('onnxruntime_gpu', '1.26.0'),
      dist('nvidia_cublas_cu12', '12.8.0'),
      dist('nvidia_cudnn_cu12', '9.7.0'),
      dist('triton', '3.5.0'),
      dist('numpy', '2.5.2'),
      dist('sympy', '1.14.0'),
      dist('jinja2', '3.1.6'),
      dist('filelock', '3.32.5'),
      dist('fsspec', '2026.7.0'),
      dist('networkx', '3.6.1'),
      dist('typing_extensions', '4.16.0'),
    ];
    const bundled = new Map(
      [
        'torch',
        'torchvision',
        'numpy',
        'sympy',
        'jinja2',
        'filelock',
        'fsspec',
        'networkx',
        'typing-extensions',
      ].map((n) => [n, '1.0.0'] as [string, string]),
    );

    const { remove, keep } = planOverlayPrune(overlay, bundled);

    assert.deepEqual(
      keep.map((d) => d.name).sort(),
      [
        'nvidia_cublas_cu12',
        'nvidia_cudnn_cu12',
        'onnxruntime_gpu',
        'torch',
        'torchvision',
        'triton',
      ],
      'the GPU stack stays whole',
    );
    assert.equal(remove.length, 7, 'every shared dependency is removed');
  });

  it('keeps a package the bundle does not have at all', () => {
    // Nothing shadows it, and deleting it would break the overlay's torch.
    const { keep, remove } = planOverlayPrune([dist('mpmath', '1.3.0')], new Map());
    assert.deepEqual(
      keep.map((d) => d.name),
      ['mpmath'],
    );
    assert.equal(remove.length, 0);
  });

  it('matches names across pip\'s spelling variants', () => {
    // The overlay's dist-info says "typing_extensions"; pip freeze says
    // "typing-extensions". PEP 503 normalization is what makes them one package -
    // without it the duplicate is never spotted and the overlay stays broken.
    const { remove } = planOverlayPrune(
      [dist('Typing_Extensions', '4.15.0')],
      new Map([['typing-extensions', '4.16.0']]),
    );
    assert.deepEqual(
      remove.map((d) => d.name),
      ['Typing_Extensions'],
    );
  });
});

describe('overlayOwns', () => {
  it('claims the GPU stack, including per-library CUDA wheels', () => {
    for (const name of [
      'torch',
      'torchvision',
      'onnxruntime-gpu',
      'onnxruntime_gpu',
      'nvidia-cublas-cu12',
      'nvidia_cudnn_cu12',
      'triton',
      'pytorch-triton',
    ]) {
      assert.ok(overlayOwns(name), `${name} must be treated as overlay-owned`);
    }
  });

  it('claims nothing the bundle is responsible for', () => {
    for (const name of ['numpy', 'typing_extensions', 'jinja2', 'onnxruntime', 'pillow']) {
      assert.ok(!overlayOwns(name), `${name} must not be treated as overlay-owned`);
    }
  });
});

describe('parseDistInfoDir', () => {
  it('splits a dist-info directory into name and version', () => {
    assert.deepEqual(parseDistInfoDir('typing_extensions-4.15.0.dist-info'), {
      name: 'typing_extensions',
      version: '4.15.0',
      dirName: 'typing_extensions-4.15.0.dist-info',
    });
  });

  it('keeps a local version label with the version, not the name', () => {
    assert.deepEqual(parseDistInfoDir('torch-2.13.0+cu128.dist-info')?.version, '2.13.0+cu128');
  });

  it('ignores anything that is not a dist-info directory', () => {
    assert.equal(parseDistInfoDir('torch'), null);
    assert.equal(parseDistInfoDir('constraints.txt'), null);
    assert.equal(parseDistInfoDir('nodash.dist-info'), null);
  });
});

describe('parseFreezeVersions', () => {
  it('reads pinned versions under normalized names', () => {
    const versions = parseFreezeVersions('numpy==2.5.2\nTyping_Extensions==4.16.0\n');
    assert.equal(versions.get('numpy'), '2.5.2');
    assert.equal(versions.get('typing-extensions'), '4.16.0');
  });

  it('skips direct references, options and comments', () => {
    // A direct reference carries no comparable version, and the app's own wheel
    // is installed that way - it must never look like something to prune against.
    const versions = parseFreezeVersions(
      ['# a comment', '-e .', 'pixlstash @ file:///build/pixlstash.whl', 'numpy==2.5.2'].join('\n'),
    );
    assert.deepEqual([...versions.keys()], ['numpy']);
  });
});

describe('recordedFilesWithin', () => {
  it('resolves RECORD entries against the overlay root', () => {
    const files = recordedFilesWithin(
      `${sep}ovl`,
      ['typing_extensions.py,sha256=abc,123', 'numpy/__init__.py,sha256=def,456'].join('\n'),
    );
    assert.deepEqual(files, [
      join(`${sep}ovl`, 'typing_extensions.py'),
      join(`${sep}ovl`, 'numpy', '__init__.py'),
    ]);
  });

  it('refuses entries that escape the overlay', () => {
    // Console-script entry points are recorded as ../../Scripts/foo.exe. Those
    // live outside the --target directory and are not ours to delete; following
    // one would let a RECORD reach the rest of the installation.
    const files = recordedFilesWithin(
      `${sep}ovl`,
      ['../../Scripts/f2py.exe,,', '../python.exe,,', 'numpy/__init__.py,,'].join('\n'),
    );
    assert.deepEqual(files, [join(`${sep}ovl`, 'numpy', '__init__.py')]);
  });
});

describe('BackendManager.pruneOverlay', () => {
  /** Write a fake `--target` overlay: a dist-info + RECORD per distribution. */
  async function fakeOverlay(dists: Record<string, { version: string; files: string[] }>) {
    const dir = await mkdtemp(join(tmpdir(), 'pixlstash-overlay-'));
    for (const [name, { version, files }] of Object.entries(dists)) {
      const info = join(dir, `${name}-${version}.dist-info`);
      await mkdir(info, { recursive: true });
      for (const rel of files) {
        await mkdir(join(dir, rel, '..'), { recursive: true });
        await writeFile(join(dir, rel), '# payload\n');
      }
      await writeFile(
        join(info, 'RECORD'),
        files.map((f) => `${f},sha256=x,1`).join('\n') + '\n',
      );
    }
    return dir;
  }

  it('deletes a shadowing duplicate\'s files and metadata, and nothing else', async () => {
    const dir = await fakeOverlay({
      typing_extensions: { version: '4.15.0', files: ['typing_extensions.py'] },
      torch: { version: '2.13.0', files: ['torch/__init__.py', 'torch/lib/c10.dll'] },
      mpmath: { version: '1.3.0', files: ['mpmath/__init__.py'] },
    });

    const removed = await new BackendManager().pruneOverlay(
      dir,
      new Map([
        ['typing-extensions', '4.16.0'],
        ['torch', '2.13.0+cpu'],
        // mpmath deliberately absent: the bundle does not ship it.
      ]),
    );

    assert.deepEqual(removed, ['typing_extensions 4.15.0']);
    assert.equal(
      await exists(join(dir, 'typing_extensions.py')),
      false,
      'the shadowing module must be gone - this is the whole point',
    );
    assert.equal(await exists(join(dir, 'typing_extensions-4.15.0.dist-info')), false);
    assert.equal(await exists(join(dir, 'torch', 'lib', 'c10.dll')), true, 'torch is untouched');
    assert.equal(await exists(join(dir, 'mpmath', '__init__.py')), true, 'unshared deps stay');
  });

  it('is idempotent - a second prune finds nothing left to do', async () => {
    const dir = await fakeOverlay({
      numpy: { version: '2.5.2', files: ['numpy/__init__.py'] },
    });
    const bundled = new Map([['numpy', '2.5.2']]);
    const manager = new BackendManager();

    assert.deepEqual(await manager.pruneOverlay(dir, bundled), ['numpy 2.5.2']);
    assert.deepEqual(await manager.pruneOverlay(dir, bundled), []);
  });

  it('leaves a distribution whose RECORD is unreadable, rather than half-deleting it', async () => {
    // Without a RECORD we cannot know which files belong to it. Removing the
    // dist-info alone would strand the files as an undeletable shadow.
    const dir = await mkdtemp(join(tmpdir(), 'pixlstash-overlay-'));
    await mkdir(join(dir, 'numpy-2.5.2.dist-info'), { recursive: true });
    await writeFile(join(dir, 'numpy.py'), '# payload\n');

    const removed = await new BackendManager().pruneOverlay(dir, new Map([['numpy', '2.5.2']]));

    assert.deepEqual(removed, []);
    assert.equal(await exists(join(dir, 'numpy-2.5.2.dist-info')), true);
  });

  it('reports nothing for a directory that does not exist', async () => {
    // The not-installed case repairIfStale asks about, not a failure.
    const removed = await new BackendManager().pruneOverlay(
      join(tmpdir(), 'pixlstash-overlay-does-not-exist'),
      new Map([['numpy', '2.5.2']]),
    );
    assert.deepEqual(removed, []);
  });

  it('throws when the overlay cannot be read at all', async () => {
    // Returning [] here would let installOverlay mark and activate an overlay
    // that was never pruned - the shadowing this whole path exists to prevent.
    const notADir = join(await mkdtemp(join(tmpdir(), 'pixlstash-overlay-')), 'file');
    await writeFile(notADir, 'not a directory\n');

    await assert.rejects(
      () => new BackendManager().pruneOverlay(notADir, new Map([['numpy', '2.5.2']])),
      /Cannot prune the overlay/,
    );
  });

  it('ignores non-distribution entries, and leaves no empty package directory', async () => {
    // constraints.txt and OVERLAY.json live in the same directory and must stay.
    // The emptied `numpy/` must NOT: an empty directory on sys.path is an
    // implicit namespace package (PEP 420), so leaving it there keeps shadowing
    // the bundle's real numpy - the same failure, only quieter.
    const dir = await fakeOverlay({
      numpy: { version: '2.5.2', files: ['numpy/__init__.py', 'numpy/lib/index.py'] },
    });
    await writeFile(join(dir, 'constraints.txt'), 'numpy==2.5.2\n');
    await writeFile(join(dir, 'OVERLAY.json'), '{}');

    await new BackendManager().pruneOverlay(dir, new Map([['numpy', '2.5.2']]));

    const left = (await readdir(dir)).sort();
    assert.deepEqual(left, ['OVERLAY.json', 'constraints.txt']);
  });

  it('keeps a directory a surviving distribution still shares', async () => {
    // Both wheels install into nvidia/, and only one is pruned. Removing the
    // shared parent would take the GPU library with it.
    const dir = await fakeOverlay({
      shared_dep: { version: '1.0.0', files: ['nvidia/dep.py'] },
      nvidia_cublas_cu12: { version: '12.8.0', files: ['nvidia/cublas/lib.dll'] },
    });

    const removed = await new BackendManager().pruneOverlay(
      dir,
      new Map([
        ['shared-dep', '1.0.0'],
        ['nvidia-cublas-cu12', '12.8.0'],
      ]),
    );

    assert.deepEqual(removed, ['shared_dep 1.0.0'], 'the nvidia wheel is overlay-owned');
    assert.equal(await exists(join(dir, 'nvidia', 'dep.py')), false);
    assert.equal(
      await exists(join(dir, 'nvidia', 'cublas', 'lib.dll')),
      true,
      'the shared parent survives because it is not empty',
    );
  });
});

describe('normalizeDistName', () => {
  it('collapses the separators PEP 503 treats as equivalent', () => {
    assert.equal(normalizeDistName('Typing_Extensions'), 'typing-extensions');
    assert.equal(normalizeDistName('onnxruntime.gpu'), 'onnxruntime-gpu');
    assert.equal(normalizeDistName('nvidia--cublas__cu12'), 'nvidia-cublas-cu12');
  });
});
