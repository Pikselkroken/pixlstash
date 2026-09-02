import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, it } from 'node:test';

import { inspectFolder } from '../src/setup/InspectFolder';

function tempDir(): string {
  return mkdtempSync(join(tmpdir(), 'pixlstash-inspect-'));
}

describe('the startup screen’s verdict on a folder', () => {
  it('reads a folder of pictures without claiming it is a library', async () => {
    const dir = tempDir();
    mkdirSync(join(dir, '2024 Shoots'));
    writeFileSync(join(dir, '2024 Shoots', 'a.jpg'), Buffer.alloc(1200));
    writeFileSync(join(dir, '2024 Shoots', 'b.PNG'), Buffer.alloc(800));
    writeFileSync(join(dir, 'notes.txt'), 'not a picture');

    const result = await inspectFolder(dir);

    assert.equal(result.exists, true);
    assert.equal(result.isLibrary, false, 'no vault.db, so this is not a library');
    assert.equal(result.pictureCount, 2, 'the text file is not a picture');
    assert.equal(result.pictureBytes, 2000);
    assert.equal(result.truncated, false);
    assert.ok(result.freeBytes > 0, 'free space is what the empty case has to show');
  });

  it('recognises a library PixlStash made before by its vault', async () => {
    const dir = tempDir();
    writeFileSync(join(dir, 'vault.db'), Buffer.alloc(64));
    writeFileSync(join(dir, 'a.webp'), Buffer.alloc(10));

    const result = await inspectFolder(dir);

    assert.equal(result.isLibrary, true);
    assert.equal(result.pictureCount, 1, 'the vault itself is not counted as a picture');
  });

  it('skips hidden folders rather than counting caches as someone’s library', async () => {
    const dir = tempDir();
    mkdirSync(join(dir, '.thumbnails'));
    writeFileSync(join(dir, '.thumbnails', 'cached.jpg'), Buffer.alloc(10));

    const result = await inspectFolder(dir);

    assert.equal(result.pictureCount, 0);
  });

  it('answers for a folder that does not exist yet, which "start empty" needs', async () => {
    const dir = join(tempDir(), 'PixlStash');

    const result = await inspectFolder(dir);

    assert.equal(result.exists, false);
    assert.equal(result.pictureCount, 0);
    assert.ok(
      result.freeBytes > 0,
      'free space comes from the nearest existing parent, so a new folder can still report it',
    );
  });

  it('refuses to guess about an empty path', async () => {
    const result = await inspectFolder('');
    assert.deepEqual(
      { exists: result.exists, isLibrary: result.isLibrary, pictureCount: result.pictureCount },
      { exists: false, isLibrary: false, pictureCount: 0 },
    );
  });
});
