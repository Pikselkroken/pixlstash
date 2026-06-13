// Copy the plain (non-TypeScript) renderer assets into dist/ so the packaged
// app can load them with the same relative paths used in development.
import { copyFileSync, cpSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, '..', 'src', 'renderer');
const dest = join(here, '..', 'dist', 'renderer');

if (!existsSync(src)) {
  console.error(`copy-assets: renderer source missing at ${src}`);
  process.exit(1);
}
mkdirSync(dest, { recursive: true });
cpSync(src, dest, { recursive: true });
console.log('copy-assets: renderer -> dist/renderer');

// The window/alt-tab icon needs a square source. The brand Logo.png is not
// square, so bundle the canonical 1024² packaging icon (assets/icon.png) into
// dist/ — it's part of the shipped files (dist/**/*) and the main process loads
// it at runtime via nativeImage. Keeps one source of truth for the app icon.
const iconSrc = join(here, '..', 'assets', 'icon.png');
if (existsSync(iconSrc)) {
  copyFileSync(iconSrc, join(dest, 'icon.png'));
  console.log('copy-assets: assets/icon.png -> dist/renderer/icon.png');
} else {
  console.warn(`copy-assets: app icon missing at ${iconSrc}`);
}
