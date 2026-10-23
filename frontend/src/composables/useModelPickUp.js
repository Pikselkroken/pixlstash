// Pick up a model that landed after its folder's last scan (#1985).
//
// A workflow names a file, ComfyUI does not list it and the shelf has no row
// for it, so it reads as missing - while the file sits in a model folder,
// downloaded after that folder was scanned. The Workflow tab and the Run popup
// hand their missing names here: a quick look first, and only when it finds
// something does `adding` say which, while those files are registered.

import { ref } from "vue";

import { pickUpModelFiles } from "../api/modelFolders";

/** How long a name looked for is not looked for again: a pre-flight runs per edit. */
export const LOOK_AGAIN_MS = 15000;

/** The file a name means, as the server matches it: its basename, any case. */
export function pickUpKey(file) {
  return String(file).split(/[\\/]/).pop().toLowerCase();
}

export function useModelPickUp() {
  /** Basenames being registered right now, for the rows that name them. */
  const adding = ref(new Set());
  const lookedAt = new Map();

  /**
   * Look for *files* in the model folders and register what is found.
   *
   * @param {string[]} files - missing file names, as the pre-flight gives them.
   * @returns {Promise<boolean>} whether anything was registered, so the caller
   *   asks its pre-flight again.
   */
  async function pickUp(files) {
    const now = Date.now();
    const names = [...new Set(files.map(String))].filter(
      (name) => !(now - (lookedAt.get(name) ?? -Infinity) < LOOK_AGAIN_MS),
    );
    if (!names.length) return false;
    names.forEach((name) => lookedAt.set(name, now));
    try {
      const found = await pickUpModelFiles(names);
      if (!found.length) return false;
      adding.value = new Set(found.map((file) => pickUpKey(file.relpath)));
      const added = await pickUpModelFiles(names, { add: true });
      return added.some((file) => file.model_id != null);
    } catch (err) {
      // A remote owner is refused (§16.3); the file stays missing, as before.
      console.warn(`[models] could not look for ${names.join(", ")}`, err);
      return false;
    } finally {
      adding.value = new Set();
    }
  }

  /** Whether *file* is one being registered right now. */
  function isAdding(file) {
    return Boolean(file) && adding.value.has(pickUpKey(file));
  }

  return { adding, pickUp, isAdding };
}
