import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { ipcBytes, pngClipboardPayload, safeMediaFilename } from '../src/mediaIpc';

describe('media IPC boundary', () => {
  it('accepts typed bytes and sanitizes filename suggestions independently', () => {
    assert.equal(safeMediaFilename('../../bad:name?.JPG'), 'bad_name_.JPG');
    assert.deepEqual([...ipcBytes(new Uint8Array([1, 2, 3]))], [1, 2, 3]);
  });

  it('never accepts a renderer-provided destination path', () => {
    assert.equal(safeMediaFilename('C:\\private\\photo.png'), 'photo.png');
    assert.equal(safeMediaFilename('/private/photo.png'), 'photo.png');
  });

  it('rejects non-byte save payloads', () => {
    assert.throws(() => ipcBytes('not bytes'));
  });

  it('accepts PNG clipboard bytes and rejects other formats', () => {
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1]);
    assert.equal(pngClipboardPayload(png).length, 9);
    assert.throws(() => pngClipboardPayload(new Uint8Array([0xff, 0xd8, 0xff])));
  });
});
