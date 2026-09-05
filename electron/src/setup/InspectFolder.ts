import { execFile } from 'node:child_process';
import { opendir, stat, statfs } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { promisify } from 'node:util';

const execFileP = promisify(execFile);

export type ExecRunner = (
  file: string,
  args: string[],
  options: { timeout: number },
) => Promise<{ stdout: string; stderr: string }>;

/** What the startup screen says about the folder someone picked. */
export type FolderInspection = {
  /** The folder is there. A "start empty" answer may name one that is not. */
  exists: boolean;
  /** A `vault.db` sits in it, so this is a library PixlStash made before. */
  isLibrary: boolean;
  pictureCount: number;
  pictureBytes: number;
  /** True when the walk hit its cap, so the count is a floor, not a total. */
  truncated: boolean;
  /** Free space on the drive the folder is (or would be) on. */
  freeBytes: number;
  /**
   * What the library itself holds, when there is one. **A `vault.db` alone
   * proves nothing about content**: a library made and never filled has one,
   * and reporting only "library found" over it is how someone opens an empty
   * library believing they opened their pictures. `null` when there is no
   * vault, or when it could not be read (the walk's numbers stand instead).
   */
  library: { pictures: number; people: number; tags: number } | null;
};

/**
 * What PixlStash counts as a picture here.
 *
 * Deliberately a superset of nothing: this list only decides what the setup
 * screen SAYS is in a folder, never what the library imports. Keep it in step
 * with the backend's own reading (`workflow_hash.IMAGE_EXTENSIONS` and the
 * tagger tasks' `_IMAGE_EXTS`) so the number a person sees before they commit
 * matches the number they get after.
 */
const PICTURE_EXTENSIONS = new Set([
  '.jpg',
  '.jpeg',
  '.png',
  '.webp',
  '.bmp',
  '.gif',
  '.tif',
  '.tiff',
  '.heic',
  '.heif',
  '.avif',
  '.mp4',
  '.webm',
  '.mov',
]);

/**
 * Stop rather than crawl a whole disk. A person picking a folder wants to know
 * "are my pictures in here", and 20k files answers that; the exact total is the
 * import's job. Both caps are floors, and `truncated` says which one was hit.
 */
const MAX_FILES = 20_000;
const MAX_MS = 2_500;

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot < 0 ? '' : name.slice(dot).toLowerCase();
}

/** Free bytes on the filesystem holding `dir`, or its nearest existing parent. */
async function freeSpaceFor(dir: string): Promise<number> {
  let probe = dir;
  for (let i = 0; i < 40; i += 1) {
    if (existsSync(probe)) {
      try {
        const fs = await statfs(probe);
        return Number(fs.bsize) * Number(fs.bavail);
      } catch (e) {
        console.warn(`setup: could not read free space for ${probe}:`, e);
        return 0;
      }
    }
    const parent = dirname(probe);
    if (parent === probe) break;
    probe = parent;
  }
  return 0;
}

/**
 * Ask the library itself what it holds. Read-only (`mode=ro`), through the
 * bundled interpreter that is already used to locate a standalone config, and
 * best-effort: a vault that cannot be read leaves the caller with the walk's
 * own numbers rather than an error the user cannot act on.
 */
export async function readVaultCounts(
  interpreter: string,
  vaultPath: string,
  run: ExecRunner = execFileP,
): Promise<{ pictures: number; people: number; tags: number } | null> {
  const script =
    'import json,sqlite3,sys,urllib.parse\n' +
    'db = sqlite3.connect("file:" + urllib.parse.quote(sys.argv[1]) + "?mode=ro", uri=True)\n' +
    'def n(sql):\n'
    + '    try: return db.execute(sql).fetchone()[0]\n'
    + '    except Exception: return 0\n' +
    'print(json.dumps({"pictures": n("select count(*) from picture"), '
    + '"people": n("select count(*) from character"), '
    + '"tags": n("select count(*) from tag")}))';
  try {
    const { stdout } = await run(interpreter, ['-c', script, vaultPath], { timeout: 8000 });
    const parsed = JSON.parse(stdout.trim());
    return {
      pictures: Number(parsed.pictures) || 0,
      people: Number(parsed.people) || 0,
      tags: Number(parsed.tags) || 0,
    };
  } catch (e) {
    console.warn(`setup: could not read the library at ${vaultPath}:`, e);
    return null;
  }
}

/**
 * Read what is in a folder, for the verdict under the startup screen's folder
 * field. Never writes, never follows symlinks out of the tree, and gives up on
 * directories it cannot read rather than failing the whole answer: a picture
 * folder with one unreadable subfolder still has pictures in it.
 */
export async function inspectFolder(
  dir: string,
  interpreter?: string,
  run: ExecRunner = execFileP,
): Promise<FolderInspection> {
  const target = (dir || '').trim();
  const result: FolderInspection = {
    exists: false,
    isLibrary: false,
    pictureCount: 0,
    pictureBytes: 0,
    truncated: false,
    freeBytes: await freeSpaceFor(target),
    library: null,
  };
  if (!target || !existsSync(target)) return result;

  result.exists = true;
  const vault = join(target, 'vault.db');
  result.isLibrary = existsSync(vault);
  if (result.isLibrary && interpreter) {
    result.library = await readVaultCounts(interpreter, vault, run);
  }

  const deadline = Date.now() + MAX_MS;
  const queue: string[] = [target];
  while (queue.length) {
    if (result.pictureCount >= MAX_FILES || Date.now() > deadline) {
      result.truncated = true;
      break;
    }
    const current = queue.shift() as string;
    let entries;
    try {
      entries = await opendir(current);
    } catch (e) {
      console.warn(`setup: skipping unreadable folder ${current}:`, e);
      continue;
    }
    for await (const entry of entries) {
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        if (!entry.name.startsWith('.')) queue.push(join(current, entry.name));
        continue;
      }
      if (!entry.isFile() || !PICTURE_EXTENSIONS.has(extensionOf(entry.name))) continue;
      result.pictureCount += 1;
      try {
        result.pictureBytes += Number((await stat(join(current, entry.name))).size);
      } catch (e) {
        console.warn(`setup: could not size ${entry.name}:`, e);
      }
      if (result.pictureCount >= MAX_FILES) {
        result.truncated = true;
        break;
      }
    }
  }
  return result;
}
