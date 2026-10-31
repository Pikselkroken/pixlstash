import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, it } from 'node:test';
import { fileClipboardPayload, requireShareCopyPaths } from '../src/fileClipboard';

const base = mkdtempSync(join(tmpdir(), 'pixlstash-clip-'));
const root = join(base, 'share-copies');
const batch = join(root, 'batch');
const outside = join(base, 'vault.png');
mkdirSync(batch, { recursive: true });
writeFileSync(join(batch, 'a.png'), 'a');
writeFileSync(join(batch, 'b b.png'), 'b');
writeFileSync(outside, 'x');
after(() => rmSync(base, { recursive: true, force: true }));

describe('requireShareCopyPaths: only share copies reach the clipboard', () => {
  it('accepts files inside the staging root', async () => {
    const paths = [join(batch, 'a.png'), join(batch, 'b b.png')];
    assert.equal((await requireShareCopyPaths(paths, root)).length, 2);
  });

  it('refuses a file outside it, a traversal, the root itself and a folder', async () => {
    await assert.rejects(requireShareCopyPaths([outside], root), /Only share copies/);
    await assert.rejects(
      requireShareCopyPaths([join(batch, '..', '..', 'vault.png')], root),
      /Only share copies/,
    );
    await assert.rejects(requireShareCopyPaths([root], root), /Only share copies/);
    await assert.rejects(requireShareCopyPaths([batch], root), /not a file/);
  });

  it('accepts a copy whose name starts with two dots', async () => {
    writeFileSync(join(root, '..x.png'), 'x');
    assert.equal((await requireShareCopyPaths([join(root, '..x.png')], root)).length, 1);
  });

  // Windows needs Developer Mode to make a symlink; the gate runs on Linux.
  it('refuses a symlink inside the root that points out of it', { skip: process.platform === 'win32' }, async () => {
    const link = join(batch, 'link.png');
    symlinkSync(outside, link);
    await assert.rejects(requireShareCopyPaths([link], root), /Only share copies/);
  });

  it('refuses a missing file, a relative path and a non-list', async () => {
    await assert.rejects(requireShareCopyPaths([join(batch, 'gone.png')], root));
    await assert.rejects(requireShareCopyPaths(['a.png'], root), /absolute/);
    await assert.rejects(requireShareCopyPaths(join(batch, 'a.png'), root), /list/);
    await assert.rejects(requireShareCopyPaths([], root), /list/);
  });
});

describe('requireShareCopyPaths: a library\'s staged links (#2420)', () => {
  const library = join(base, 'library');
  const linkBatch = join(library, '.pixlstash-share', '0123456789abcdef0123456789abcdef');
  mkdirSync(linkBatch, { recursive: true });
  writeFileSync(join(linkBatch, 'ComfyUI_00001_.png'), 'l');
  writeFileSync(join(library, 'original.png'), 'o');

  it('accepts a file in a .pixlstash-share batch, wherever the library is', async () => {
    const path = join(linkBatch, 'ComfyUI_00001_.png');
    assert.deepEqual(await requireShareCopyPaths([path], root), [path]);
  });

  it('refuses the library\'s own files and anything not shaped like a batch', async () => {
    const notHex = join(library, '.pixlstash-share', 'not-a-batch');
    mkdirSync(notHex, { recursive: true });
    writeFileSync(join(notHex, 'a.png'), 'a');
    const deeper = join(linkBatch, 'sub');
    mkdirSync(deeper, { recursive: true });
    writeFileSync(join(deeper, 'a.png'), 'a');
    // A batch-named folder anywhere else is not one of ours.
    const looseBatch = join(library, '0123456789abcdef0123456789abcdef');
    mkdirSync(looseBatch, { recursive: true });
    writeFileSync(join(looseBatch, 'a.png'), 'a');
    for (const bad of [
      join(library, 'original.png'),
      join(notHex, 'a.png'),
      join(deeper, 'a.png'),
      join(looseBatch, 'a.png'),
    ]) {
      await assert.rejects(requireShareCopyPaths([bad], root), /Only share copies/, bad);
    }
  });

  it('refuses a symlink in a batch that points out of it', { skip: process.platform === 'win32' }, async () => {
    const link = join(linkBatch, 'sneaky.png');
    symlinkSync(join(library, 'original.png'), link);
    await assert.rejects(requireShareCopyPaths([link], root), /Only share copies/);
  });
});

describe('fileClipboardPayload', () => {
  const paths = ['/home/me/share-copies/x/a b.png', '/home/me/share-copies/x/c#1.png'];

  it('writes gnome-copied-files on a GTK desktop, URL-encoded', () => {
    const { format, data } = fileClipboardPayload(paths, 'linux', 'X-Cinnamon');
    assert.equal(format, 'x-special/gnome-copied-files');
    assert.equal(
      data.toString(),
      'copy\nfile:///home/me/share-copies/x/a%20b.png\nfile:///home/me/share-copies/x/c%231.png',
    );
  });

  it('writes text/uri-list on KDE', () => {
    const { format, data } = fileClipboardPayload(paths, 'linux', 'KDE');
    assert.equal(format, 'text/uri-list');
    assert.match(data.toString(), /^file:\/\/\/home\/me\/share-copies\/x\/a%20b\.png\r\n/);
  });

  it('writes an escaped plist of paths on macOS', () => {
    const { format, data } = fileClipboardPayload(['/Users/me/a&<b>.png'], 'darwin');
    assert.equal(format, 'NSFilenamesPboardType');
    assert.match(data.toString(), /<array><string>\/Users\/me\/a&amp;&lt;b&gt;\.png<\/string><\/array>/);
  });
});
