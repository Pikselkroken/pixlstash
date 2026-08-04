import { existsSync } from 'node:fs';
import { join } from 'node:path';

/** Split a filename into its stem and extension (the extension keeps its dot). */
function splitExtension(filename: string): { stem: string; extension: string } {
  // A leading dot belongs to the stem (".gitignore" is not an extension-only name).
  const dot = filename.lastIndexOf('.');
  if (dot <= 0) return { stem: filename, extension: '' };
  return { stem: filename.slice(0, dot), extension: filename.slice(dot) };
}

/**
 * Pick a free path for `filename` inside `dir`, suffixing " (n)" the way a browser
 * does rather than overwriting a file the user already has. `exists` is injectable
 * so the choice is testable without touching the filesystem.
 *
 * The counter is bounded: once a stem has that many copies, the caller gets the
 * highest-numbered candidate back and the write overwrites it. A silent unbounded
 * loop would be worse than one predictable collision.
 */
export function uniqueDownloadPath(
  dir: string,
  filename: string,
  exists: (path: string) => boolean = existsSync,
): string {
  const candidate = join(dir, filename);
  if (!exists(candidate)) return candidate;
  const { stem, extension } = splitExtension(filename);
  let numbered = candidate;
  for (let n = 1; n <= 1000; n += 1) {
    numbered = join(dir, `${stem} (${n})${extension}`);
    if (!exists(numbered)) return numbered;
  }
  return numbered;
}
