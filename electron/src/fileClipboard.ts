import { realpath, stat } from 'node:fs/promises';
import { isAbsolute, relative } from 'node:path';
import { pathToFileURL } from 'node:url';

/**
 * Narrow renderer-supplied paths to share copies main itself knows about.
 *
 * The paths come from `POST /pictures/share-copies` *through* the renderer,
 * which main never trusts with a filesystem path (integration_architecture.md
 * §2.5). Each one must resolve, symlinks followed, to a regular file inside
 * `root` (`shareCopiesRoot()`), so a page cannot put a vault file or anything
 * else on the clipboard by naming it.
 */
export async function requireShareCopyPaths(value: unknown, root: string): Promise<string[]> {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error('A list of share-copy paths is required.');
  }
  const realRoot = await realpath(root);
  const out: string[] = [];
  for (const item of value) {
    if (typeof item !== 'string' || !isAbsolute(item)) {
      throw new Error('Share-copy paths must be absolute.');
    }
    const real = await realpath(item);
    const rel = relative(realRoot, real);
    if (!rel || rel.startsWith('..') || isAbsolute(rel)) {
      throw new Error('Only share copies can be put on the clipboard.');
    }
    if (!(await stat(real)).isFile()) throw new Error('A share copy is not a file.');
    out.push(real);
  }
  return out;
}

/**
 * The single clipboard format that carries a file list on macOS and Linux.
 *
 * Electron writes one custom format per `clipboard.writeBuffer` call and each
 * call replaces the clipboard, so this picks the one the file manager reads:
 * - macOS: `NSFilenamesPboardType`, a plist array of paths (Finder, Mail).
 * - Linux: `x-special/gnome-copied-files` for the GTK file managers (Nautilus,
 *   Nemo, Caja, Thunar); `text/uri-list` on KDE, which is what Dolphin reads.
 *   Chromium also offers the portal file formats for a `text/uri-list`.
 * Windows has no such buffer (a format named `CF_HDROP` would be a new
 * registered format, not the standard one) and goes through PowerShell.
 */
export function fileClipboardPayload(
  paths: string[],
  platform: NodeJS.Platform,
  desktop = '',
): { format: string; data: Buffer } {
  if (platform === 'darwin') {
    const esc = (s: string) =>
      s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const plist =
      '<?xml version="1.0" encoding="UTF-8"?>\n' +
      '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" ' +
      '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n' +
      `<plist version="1.0"><array>${paths
        .map((p) => `<string>${esc(p)}</string>`)
        .join('')}</array></plist>\n`;
    return { format: 'NSFilenamesPboardType', data: Buffer.from(plist, 'utf8') };
  }
  const uris = paths.map((p) => pathToFileURL(p).href);
  if (/kde/i.test(desktop)) {
    return { format: 'text/uri-list', data: Buffer.from(uris.join('\r\n') + '\r\n', 'utf8') };
  }
  return {
    format: 'x-special/gnome-copied-files',
    data: Buffer.from(['copy', ...uris].join('\n'), 'utf8'),
  };
}

/**
 * Windows PowerShell 5.1 (on every Windows 10/11) writes a real `CF_HDROP`.
 * The paths travel in an environment variable, never in the command line, so
 * no file name is ever parsed as script.
 */
export const WINDOWS_COPY_FILES_SCRIPT =
  'Set-Clipboard -LiteralPath ($env:PIXLSTASH_COPY_FILES -split "`n")';
