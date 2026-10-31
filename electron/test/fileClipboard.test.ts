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

  it('refuses a symlink inside the root that points out of it', async () => {
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
