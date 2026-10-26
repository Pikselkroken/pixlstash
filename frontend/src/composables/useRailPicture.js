import { ref, watch } from "vue";
import { getPictureMetadata, listPictureFaces } from "../api/pictures";
import { isDescriptionSentinel } from "../utils/descriptions";
import { dedupeDetections } from "../utils/media.js";

/**
 * What the rail's head calls one picture (#2161).
 *
 * Its description's first clause; with no description yet, who is in it and
 * when; and only then its file name. Files mostly have random names, so the
 * file name is the last resort and never shown while the details are still
 * being read.
 *
 * @param {Object|null} image - the picture's details; null while reading.
 * @param {Array<string>} people - the names of the people in it.
 * @param {(date: string) => string} formatDate
 * @returns {{ title: string, full: string }} the head's text and, for its
 *   tooltip, the whole of what it was cut from.
 */
export function pictureTitle(image, people, formatDate) {
  if (!image) return { title: "", full: "" };
  const description = String(image.description || "").trim();
  if (description && !isDescriptionSentinel(image.description)) {
    const clause = description.split(/[.,;:!?\n]/)[0].trim() || description;
    return { title: clause, full: description };
  }
  const when = image.created_at ? formatDate(image.created_at) : "";
  const byline = [people.join(", "), when].filter(Boolean).join(" · ");
  if (byline) return { title: byline, full: byline };
  const file =
    image.original_file_name ||
    String(image.file_path || "")
      .split(/[\\/]/)
      .pop();
  return { title: file, full: file };
}

/**
 * The one selected picture's details, for the grid's rail (#2161).
 *
 * The grid's card carries only what a tile draws, so the rail reads the rest
 * when the selection settles on a picture: its metadata (description, tags,
 * file, size, model, lock) and its faces. An answer that lands after the
 * selection moved on is dropped, as the lightbox drops one for a picture it
 * has stepped past.
 *
 * @param {() => (number|string|null)} pictureId - read reactively.
 * @returns {{ image: import("vue").Ref<Object|null>,
 *   faces: import("vue").Ref<Array<Object>>, refresh: () => Promise<void> }}
 */
export function useRailPicture(pictureId) {
  const image = ref(null);
  const faces = ref([]);
  let request = 0;

  async function load(id, { keep = false } = {}) {
    const mine = (request += 1);
    // A refresh keeps what is on screen until the new answer replaces it; a
    // new picture must never show the last one's details meanwhile.
    if (!keep) {
      image.value = null;
      faces.value = [];
    }
    if (id == null) return;
    const [meta, faceRows] = await Promise.all([
      getPictureMetadata(id).catch((e) => {
        console.warn(`Couldn't read picture ${id}'s details for the rail`, e);
        return null;
      }),
      listPictureFaces(id).catch((e) => {
        console.warn(`Couldn't read picture ${id}'s faces for the rail`, e);
        return [];
      }),
    ]);
    if (mine !== request) return;
    image.value = meta;
    const rows = Array.isArray(faceRows) ? faceRows : (faceRows?.faces ?? []);
    faces.value = dedupeDetections(rows).filter(
      (f) =>
        f.frame_index === 0 && Array.isArray(f.bbox) && f.bbox.length === 4,
    );
  }

  watch(pictureId, (id) => load(id), { immediate: true });

  return { image, faces, refresh: () => load(pictureId(), { keep: true }) };
}
