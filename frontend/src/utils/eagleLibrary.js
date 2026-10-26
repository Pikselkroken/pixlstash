import { attachImportMetadata } from "../api/pictureImport.js";
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
 * ("Travel / Norway"). Items in Eagle's trash, file types PixlStash does not
 * import and items whose `metadata.json` is missing or will not parse (a
 * half-synced library) are left out and counted; the rest still import.
 *
 * @param {FileList|File[]} fileList Every file under the picked folder.
 * @returns {Promise<{files: File[], metadata: Object[], trashed: number,
 *   unsupported: number, unreadable: number}>} `metadata[i]` is
 *   `{tags, score, sets}` for `files[i]`.
 * @throws {Error} When the folder holds no Eagle library.
 */
async function readJson(file) {
  try {
    return JSON.parse(await file.text());
  } catch (e) {
    console.warn(
      `[IMPORT] Eagle: skipping unreadable ${file.webkitRelativePath || file.name}`,
      e,
    );
    return null;
  }
}

export async function readEagleLibrary(fileList) {
  const filesByDir = new Map();
  for (const file of Array.from(fileList || [])) {
    const path = file.webkitRelativePath || file.name;
    const slash = path.lastIndexOf("/");
    const dir = slash === -1 ? "" : path.slice(0, slash);
    if (!filesByDir.has(dir)) filesByDir.set(dir, new Map());
    filesByDir.get(dir).set(path.slice(slash + 1), file);
  }

  const folderPathsByRoot = new Map();
  async function folderPaths(root) {
    if (!folderPathsByRoot.has(root)) {
      const paths = new Map();
      const libraryMeta = filesByDir.get(root)?.get("metadata.json");
      // Shape-checked like the items: one odd entry must not end the read.
      const walk = (folders, parent) => {
        for (const folder of Array.isArray(folders) ? folders : []) {
          if (typeof folder?.name !== "string") continue;
          const path = parent ? `${parent} / ${folder.name}` : folder.name;
          paths.set(folder.id, path);
          walk(folder.children, path);
        }
      };
      // Unreadable, the folder tree is lost but the pictures still import.
      if (libraryMeta) walk((await readJson(libraryMeta))?.folders, "");
      folderPathsByRoot.set(root, paths);
    }
    return folderPathsByRoot.get(root);
  }

  const result = {
    files: [],
    metadata: [],
    trashed: 0,
    unsupported: 0,
    unreadable: 0,
  };
  let items = 0;
  for (const [dir, files] of filesByDir) {
    const match = dir.match(/^(?:(.*)\/)?images\/[^/]+\.info$/);
    if (!match) continue;
    items += 1;
    // A missing metadata.json (half-synced) counts like an unparseable one.
    const metaFile = files.get("metadata.json");
    const meta = metaFile ? await readJson(metaFile) : null;
    if (!meta) {
      result.unreadable += 1;
      continue;
    }
    if (meta.isDeleted) {
      result.trashed += 1;
      continue;
    }
    // The original is `<name>.<ext>`; failing that (a name the file system
    // would not take), the one file of type `ext` that is not the thumbnail.
    // `ext` stays the authority when given, so an unsupported item (a PSD)
    // never imports a stray preview; only without one does the type of a
    // non-thumbnail file decide.
    const ext = typeof meta.ext === "string" ? meta.ext.toLowerCase() : "";
    const fileExt = (name) =>
      name.slice(name.lastIndexOf(".") + 1).toLowerCase();
    const original =
      files.get(`${meta.name}.${meta.ext}`) ??
      [...files].find(
        ([name]) =>
          name !== "metadata.json" &&
          !name.endsWith("_thumbnail.png") &&
          (ext
            ? fileExt(name) === ext
            : IMPORT_MEDIA_EXTENSIONS.includes(fileExt(name))),
      )?.[1];
    if (
      !original ||
      !IMPORT_MEDIA_EXTENSIONS.includes(fileExt(original.name))
    ) {
      result.unsupported += 1;
      continue;
    }
    const paths = await folderPaths(match[1] ?? "");
    result.files.push(original);
    result.metadata.push({
      // Checked types only: the server refuses the whole batch over one bad
      // entry (tags are strings, the score an integer 1-5).
      tags: Array.isArray(meta.tags)
        ? meta.tags.filter((t) => typeof t === "string")
        : [],
      score:
        Number.isInteger(meta.star) && meta.star >= 1 && meta.star <= 5
          ? meta.star
          : null,
      sets: (Array.isArray(meta.folders) ? meta.folders : [])
        .map((id) => paths.get(id))
        .filter(Boolean),
    });
  }
  if (!items) {
    throw new Error(
      "That folder is not an Eagle library: choose the folder whose name ends in .library.",
    );
  }
  return result;
}

/**
 * The files to hand the ordinary import for an Eagle library, each carrying
 * its tags, rating and sets. Both import surfaces (the toolbar panel and the
 * full dialog) go through here.
 *
 * @param {FileList|File[]} fileList Every file under the picked folder.
 * @returns {Promise<File[]>}
 * @throws {Error} With a message for the person when nothing can be imported.
 */
export async function eagleImportFiles(fileList) {
  const library = await readEagleLibrary(fileList);
  console.info(
    `[IMPORT] Eagle library: ${library.files.length} to import, ` +
      `${library.trashed} in Eagle's trash, ${library.unsupported} unsupported, ` +
      `${library.unreadable} unreadable.`,
  );
  if (!library.files.length) {
    throw new Error("That library has no pictures PixlStash can import.");
  }
  library.files.forEach((file, i) =>
    attachImportMetadata(file, library.metadata[i]),
  );
  return library.files;
}
