import { ref, watch } from "vue";
import { getPictureMetadata, listPictureFaces } from "../api/pictures";
import { isDescriptionSentinel } from "../utils/descriptions";
import { dedupeDetections } from "../utils/media.js";

/**
 * What the rail's head calls one picture (#2161).
 *
 * Its description's first sentence without a leading "A", "An" or "The"
 * ("Elderly man with white hair…", cut to fit by `fitWords`); with no
 * description yet, who is in it and when; and only then its file name. Files mostly have random names, so the
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
    const sentence = description.split(/[.!?\n]/)[0].trim() || description;
    const bare = sentence.replace(/^(a|an|the)\s+/i, "") || sentence;
    const title = bare.charAt(0).toUpperCase() + bare.slice(1);
    return { title, full: description };
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
 * Cut text after the last whole word that fits, with an ellipsis there.
 *
 * CSS `text-overflow` cuts mid-word ("white hair a…"); a title reads better
 * cut where a word ends ("white hair…").
 *
 * @param {string} text
 * @param {number} width - the room there is, in px.
 * @param {(text: string) => number} measure - a string's width in px.
 * @returns {string} the text, whole when it fits; a single word too long
 *   for the room comes back with its ellipsis, for CSS to trim further.
 */
export function fitWords(text, width, measure) {
  if (!text || !(width > 0) || measure(text) <= width) return text;
  const words = text.split(/\s+/);
  for (let count = words.length - 1; count > 1; count -= 1) {
    const cut = `${words.slice(0, count).join(" ").replace(/[\s,;:–—-]+$/, "")}…`;
    if (measure(cut) <= width) return cut;
  }
  return `${words[0]}…`;
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
