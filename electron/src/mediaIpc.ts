import { basename } from 'node:path';

export function ipcBytes(value: unknown): Buffer {
  if (value instanceof ArrayBuffer) return Buffer.from(value);
  if (ArrayBuffer.isView(value)) {
    return Buffer.from(value.buffer, value.byteOffset, value.byteLength);
  }
  throw new Error('Media bytes are required.');
}

export function safeMediaFilename(value: unknown): string {
  const raw = typeof value === 'string' ? basename(value.replace(/\\/g, '/')) : '';
  // Keep the IPC semantic: this is a filename suggestion, never a path.
  // Windows-reserved punctuation is invalid on the strictest supported OS.
  // eslint is not used for the Electron package; the regex intentionally names controls.
  const cleaned = raw
    .replace(/[\u0000-\u001f\u007f<>:"/\\|?*]/g, '_')
    .replace(/[. ]+$/g, '')
    .trim();
  return (cleaned || 'media').slice(0, 255);
}

export function pngClipboardPayload(value: unknown): Buffer {
  const data = ipcBytes(value);
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  if (data.length < signature.length || !data.subarray(0, signature.length).equals(signature)) {
    throw new Error('Clipboard payload must be a PNG image.');
  }
  return data;
}
