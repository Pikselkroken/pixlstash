import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, sep } from 'node:path';
import { describe, it } from 'node:test';

import { inspectFolder, readVaultCounts } from '../src/setup/InspectFolder';

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

  it('counts what the import will index, not PixlStash’s own files', async () => {
    // A folder that was a library once: thumbnails beside the originals, and
    // the root folders a library makes. The import's read skips all of them.
    const dir = tempDir();
    mkdirSync(join(dir, '2014'));
    writeFileSync(join(dir, '2014', 'beach.jpg'), Buffer.alloc(10));
    writeFileSync(join(dir, '2014', 'beach_thumb.webp'), Buffer.alloc(10));
    writeFileSync(join(dir, '2014', 'sunset.webp'), Buffer.alloc(10));
    mkdirSync(join(dir, 'tmp', 'face_thumbnails'), { recursive: true });
    writeFileSync(join(dir, 'tmp', 'face_thumbnails', 'character_1.png'), Buffer.alloc(10));
    mkdirSync(join(dir, 'snapshots'));
    writeFileSync(join(dir, 'snapshots', 'x.png'), Buffer.alloc(10));
    // Only the root's: a `tmp` deeper down is the owner's own folder.
    mkdirSync(join(dir, '2014', 'tmp'));
    writeFileSync(join(dir, '2014', 'tmp', 'kept.jpg'), Buffer.alloc(10));

    const result = await inspectFolder(dir);

    assert.equal(result.pictureCount, 3, 'beach.jpg, sunset.webp and 2014/tmp/kept.jpg');
  });

  it('skips the root’s own folders however the root path is written', async () => {
    // The root is compared with itself (it is the queue's first entry), so a
    // trailing separator or a relative spelling cannot let `tmp` back in.
    const dir = tempDir();
    writeFileSync(join(dir, 'a.jpg'), Buffer.alloc(10));
    mkdirSync(join(dir, 'tmp'));
    writeFileSync(join(dir, 'tmp', 'face.png'), Buffer.alloc(10));

    for (const spelling of [`${dir}${sep}`, relative(process.cwd(), dir)]) {
      const result = await inspectFolder(spelling);
      assert.equal(result.pictureCount, 1, spelling);
    }
  });

  it('asks the library what it holds, because a vault proves only that one exists', async () => {
    const dir = tempDir();
    writeFileSync(join(dir, 'vault.db'), Buffer.alloc(64));
    const runner = async () => ({
      stdout: JSON.stringify({ pictures: 12101, people: 17, tags: 325727 }),
      stderr: '',
    });

    const result = await inspectFolder(dir, '/fake/python', runner);

    assert.deepEqual(result.library, { pictures: 12101, people: 17, tags: 325727 });
  });

  it('reports an empty library as empty rather than as a library', async () => {
    // The failure this exists for: a folder someone made and never filled has a
    // vault.db, and "library found here" over it reads as "your pictures".
    const dir = tempDir();
    writeFileSync(join(dir, 'vault.db'), Buffer.alloc(64));
    const runner = async () => ({
      stdout: JSON.stringify({ pictures: 0, people: 0, tags: 0 }),
      stderr: '',
    });

    const result = await inspectFolder(dir, '/fake/python', runner);

    assert.equal(result.isLibrary, true);
    assert.equal(result.library?.pictures, 0, 'the count is what the screen has to say out loud');
  });

  it('falls back to the walk when the vault cannot be read', async () => {
    const dir = tempDir();
    writeFileSync(join(dir, 'vault.db'), Buffer.alloc(64));
    writeFileSync(join(dir, 'a.jpg'), Buffer.alloc(10));
    const runner = async () => {
      throw new Error('locked');
    };

    const result = await inspectFolder(dir, '/fake/python', runner);

    assert.equal(result.library, null, 'no invented numbers');
    assert.equal(result.pictureCount, 1, 'the walk still answers');
  });

  it('reads the vault read-only, so inspecting can never write to it', async () => {
    let seen = '';
    const runner = async (_file: string, args: string[]) => {
      seen = args[1];
      return { stdout: '{"pictures":1,"people":0,"tags":0}', stderr: '' };
    };
    await readVaultCounts('/fake/python', '/tmp/vault.db', runner);
    assert.match(seen, /mode=ro/);
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
