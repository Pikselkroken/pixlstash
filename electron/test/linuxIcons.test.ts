import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'node:test';

// Sizes freedesktop's hicolor/index.theme actually declares. An icon installed
// into a directory hicolor does not declare — our 1024x1024 one — is invisible
// to every icon-theme lookup, so the desktop entry renders with no icon at all.
const HICOLOR_SIZES = [16, 22, 24, 28, 32, 36, 48, 64, 72, 96, 128, 192, 256, 384, 512];
const REQUIRED = [16, 24, 32, 48, 64, 128, 256, 512];

const electronDir = join(__dirname, '..', '..');
const iconsDir = join(electronDir, 'assets', 'icons');

/** Width/height from the PNG IHDR chunk, which sits at a fixed offset. */
function pngSize(file: string): { width: number; height: number } {
  const buf = readFileSync(file);
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

describe('linux icon set', () => {
  it('is a directory, so electron-builder installs every size', () => {
    // Handed a single .png, electron-builder installs that one file at its own
    // resolution instead of generating a set. Only a directory yields a set.
    const { build } = JSON.parse(readFileSync(join(electronDir, 'package.json'), 'utf8'));
    assert.equal(build.linux.icon, 'assets/icons');
  });

  it('covers the sizes a desktop actually asks for', () => {
    const present = readdirSync(iconsDir)
      .map((n) => /^(\d+)x\d+\.png$/.exec(n)?.[1])
      .filter((n): n is string => n != null)
      .map(Number);
    for (const size of REQUIRED) {
      assert.ok(present.includes(size), `missing ${size}x${size}.png`);
    }
  });

  it('offers only square icons at sizes hicolor declares', () => {
    for (const name of readdirSync(iconsDir)) {
      const match = /^(\d+)x\d+\.png$/.exec(name);
      if (!match) continue;
      const size = Number(match[1]);
      assert.ok(HICOLOR_SIZES.includes(size), `${name} is not a hicolor-declared size`);
      assert.deepEqual(pngSize(join(iconsDir, name)), { width: size, height: size });
    }
  });
});
