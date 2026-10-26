import { IMPORT_MEDIA_EXTENSIONS } from "./media.js";

/**
 * Read an Eagle library folder picked with `<input webkitdirectory>` (#2137).
 *
 * A library is `<name>.library/` holding `metadata.json` (the folder tree:
 * `folders[]` of `{id, name, children[]}`) and `images/<id>.info/`, one per
 * item, each with the original file, a `_thumbnail.png` and the item's own
 * `metadata.json` (`name`, `ext`, `tags`, `folders` ids, `star` 0-5,
 * `isDeleted`). Everything is read here, in the browser, so the import is the
 * ordinary upload: only the originals travel, each with the labels it carries.
 *
 * Folders become picture sets, nested ones named by their path
 * ("Travel / Norway"). Items in Eagle's trash and file types PixlStash does not
 * import are left out and counted.
 *
 * @param {FileList|File[]} fileList Every file under the picked folder.
 * @returns {Promise<{files: File[], metadata: Object[], trashed: number,
 *   unsupported: number}>} `metadata[i]` is `{tags, score, sets}` for `files[i]`.
 * @throws {Error} When the folder holds no Eagle library.
 */
export async function readEagleLibrary(fileList) {
  const filesByDir = new Map();
  for (const file of Array.from(fileList || [])) {
    const path = file.webkitRelativePath || file.name;
    const slash = path.lastIndexOf("/");
    const dir = path.slice(0, slash);
    if (!filesByDir.has(dir)) filesByDir.set(dir, new Map());
    filesByDir.get(dir).set(path.slice(slash + 1), file);
  }

  const folderPathsByRoot = new Map();
  async function folderPaths(root) {
    if (!folderPathsByRoot.has(root)) {
      const paths = new Map();
      const libraryMeta = filesByDir.get(root)?.get("metadata.json");
      const walk = (folders, parent) => {
        for (const folder of folders || []) {
          const path = parent ? `${parent} / ${folder.name}` : folder.name;
          paths.set(folder.id, path);
          walk(folder.children, path);
        }
      };
      if (libraryMeta) walk(JSON.parse(await libraryMeta.text()).folders, "");
      folderPathsByRoot.set(root, paths);
    }
    return folderPathsByRoot.get(root);
  }

  const result = { files: [], metadata: [], trashed: 0, unsupported: 0 };
  let items = 0;
  for (const [dir, files] of filesByDir) {
    const match = dir.match(/^(?:(.*)\/)?images\/[^/]+\.info$/);
    const metaFile = files.get("metadata.json");
    if (!match || !metaFile) continue;
    items += 1;
    const meta = JSON.parse(await metaFile.text());
    if (meta.isDeleted) {
      result.trashed += 1;
      continue;
    }
    const ext = String(meta.ext || "").toLowerCase();
    // The original is `<name>.<ext>`; failing that (a name the file system
    // would not take), the one file of that type that is not the thumbnail.
    const original =
      files.get(`${meta.name}.${meta.ext}`) ??
      [...files].find(
        ([name]) =>
          name.toLowerCase().endsWith(`.${ext}`) &&
          !name.endsWith("_thumbnail.png"),
      )?.[1];
    if (!original || !IMPORT_MEDIA_EXTENSIONS.includes(ext)) {
      result.unsupported += 1;
      continue;
    }
    const paths = await folderPaths(match[1] ?? "");
    result.files.push(original);
    result.metadata.push({
      tags: Array.isArray(meta.tags) ? meta.tags : [],
      score: meta.star >= 1 && meta.star <= 5 ? meta.star : null,
      sets: (meta.folders || []).map((id) => paths.get(id)).filter(Boolean),
    });
  }
  if (!items) {
    throw new Error(
      "That folder is not an Eagle library: choose the folder whose name ends in .library.",
    );
  }
  return result;
}
