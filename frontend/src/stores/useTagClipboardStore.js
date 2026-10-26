// Tag copy and paste (#2139), and the tag chip size.
//
// * Copy remembers the tags here and also puts them on the system clipboard as
//   comma-separated text for a prompt. Ctrl+V pastes what was remembered onto
//   pictures: it only adds what each picture is missing, as one undo step. The
//   copied tags are not stored on the server and last until the next copy or
//   until the app closes.
// * The chip size is stored with the user's settings (hub, follows the user).

import { onScopeDispose, ref, watch } from "vue";
import { defineStore } from "pinia";

import {
  isReadOnly,
  newOperationBatchId,
  onSessionReset,
} from "../utils/apiClient";
import { patchUserConfig } from "../api/config";
import { addPictureTag, bulkFetchTags } from "../api/tags";
import { useNoticeStore } from "./useNoticeStore";

export const CHIP_SIZES = ["compact", "comfortable", "large"];
// Three steps on the type ramp: 11, 12 and 13px.
const CHIP_FONT = {
  compact: "var(--text-2xs)",
  comfortable: "var(--text-xs)",
  large: "var(--text-sm)",
};

/**
 * Split pasted text (a prompt, a comma list) into one tag per item.
 *
 * Prompt weighting is dropped: `(masterpiece:1.2)` is the tag `masterpiece`.
 *
 * @param {string} text
 * @returns {string[]} trimmed, de-duplicated case-insensitively, in order.
 */
export function splitTagText(text) {
  const seen = new Set();
  const out = [];
  for (const raw of String(text ?? "").split(/[,\n]/)) {
    const tag = raw
      .trim()
      .replace(/^[([{]+|[)\]}]+$/g, "")
      .replace(/:\s*-?\d+(?:\.\d+)?$/, "")
      .trim();
    const key = tag.toLowerCase();
    if (!tag || seen.has(key)) continue;
    seen.add(key);
    out.push(tag);
  }
  return out;
}

// POST /pictures/tags/bulk_fetch answers for at most this many ids.
const BULK_FETCH_LIMIT = 200;

/**
 * Copied tags as they are, trimmed and de-duplicated case-insensitively. Not
 * through splitTagText: that is for free text, and a real tag may hold a comma
 * or end in a bracket ("pikachu (pokemon)").
 */
function uniqueTags(tags) {
  const seen = new Set();
  const out = [];
  for (const raw of tags || []) {
    const tag = String(raw ?? "").trim();
    const key = tag.toLowerCase();
    if (!tag || seen.has(key)) continue;
    seen.add(key);
    out.push(tag);
  }
  return out;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export const useTagClipboardStore = defineStore("tagClipboard", () => {
  const clipboardTags = ref([]);
  const chipSize = ref("comfortable");

  // The tags copied in one session are not the next credential's. The chip
  // size is a display preference and stays.
  const unsubscribeSessionReset = onSessionReset(() => {
    clipboardTags.value = [];
  });
  onScopeDispose(() => unsubscribeSessionReset());

  // The chip size is one CSS variable on the root, read by every tag chip.
  watch(
    chipSize,
    (size) => {
      if (typeof document === "undefined") return;
      document.documentElement.style.setProperty(
        "--tag-chip-font",
        CHIP_FONT[size] || CHIP_FONT.comfortable,
      );
    },
    { immediate: true },
  );

  function hydrate(cfg) {
    chipSize.value = CHIP_SIZES.includes(cfg?.tag_chip_size)
      ? cfg.tag_chip_size
      : "comfortable";
  }

  async function setChipSize(size) {
    if (!CHIP_SIZES.includes(size) || size === chipSize.value) return;
    chipSize.value = size;
    if (isReadOnly.value) return;
    try {
      await patchUserConfig({ tag_chip_size: size });
    } catch (e) {
      console.error("Failed to save the tag chip size:", size, e);
      useNoticeStore().push({
        level: "error",
        text: "Couldn't save the chip size. It is kept until the app closes.",
        key: "tag-chip-size-save",
      });
    }
  }

  /**
   * Copy tags: remember them for Ctrl+V and put them on the system clipboard
   * as comma-separated text for a prompt.
   *
   * @param {string[]} tags
   */
  async function copyTags(tags) {
    const next = uniqueTags(tags);
    if (!next.length) return;
    clipboardTags.value = next;
    useNoticeStore().push({
      level: "success",
      text: `Copied ${plural(next.length, "tag")}`,
      key: "tag-copy",
    });
    try {
      await navigator.clipboard?.writeText(next.join(", "));
    } catch (e) {
      // Ctrl+V still pastes them; only the text copy for a prompt was refused
      // (no focus, or no permission in a browser).
      console.warn("Couldn't put the tags on the system clipboard:", e);
    }
  }

  /**
   * Ctrl+V on pictures: add the copied tags each picture is missing.
   *
   * Never rejects: a failure is logged and reported in a notice.
   *
   * @param {Array<number>} pictureIds
   * @returns {Promise<{pictureIds: number[], failed: number}|null>} the
   *   pictures that changed, or null when nothing was pasted.
   */
  async function pasteClipboard(pictureIds) {
    const ids = (pictureIds || []).map(Number).filter((id) => id > 0);
    if (isReadOnly.value || !ids.length) return null;
    const notices = useNoticeStore();
    const tags = clipboardTags.value;
    if (!tags.length) {
      notices.push({
        level: "info",
        key: "tag-clipboard-empty",
        text: "Nothing to paste. Select tags on a picture and copy them first.",
      });
      return null;
    }
    try {
      const rows = [];
      for (let i = 0; i < ids.length; i += BULK_FETCH_LIMIT) {
        rows.push(
          ...((await bulkFetchTags(ids.slice(i, i + BULK_FETCH_LIMIT))) || []),
        );
      }
      const has = new Map(
        rows.map((row) => [
          Number(row.id),
          new Set(
            (row.tags || []).map((t) =>
              String(typeof t === "string" ? t : t.tag || "").toLowerCase(),
            ),
          ),
        ]),
      );
      const batchId = newOperationBatchId();
      const writes = [];
      const written = [];
      const touched = new Set();
      for (const id of ids) {
        for (const tag of tags) {
          if (has.get(id)?.has(tag.toLowerCase())) continue;
          touched.add(id);
          written.push(tag);
          writes.push(addPictureTag(id, tag, { batchId }));
        }
      }
      // One refused write (a locked set's picture answers 423) must not hide
      // the rest, so every write is waited for and the refusals counted.
      const results = await Promise.allSettled(writes);
      const failed = results.filter((r) => r.status === "rejected");
      if (failed.length) {
        console.warn(
          `${failed.length} of ${results.length} tag writes failed pasting`,
          tags,
          "on",
          ids,
          failed.map((r) => r.reason),
        );
      }
      // The tags that reached at least one picture, not every tag copied: a
      // picture that already had some of them got only the rest.
      const added = new Set(
        written.filter((_, i) => results[i].status === "fulfilled"),
      ).size;
      const where = ids.length === 1 ? "" : ` to ${plural(touched.size, "picture")}`;
      let text = touched.size
        ? `Added ${plural(added, "tag")}${where}`
        : `Already ${ids.length === 1 ? "has" : "have"} ${tags.length === 1 ? "that tag" : "those tags"}`;
      if (failed.length) {
        text += ` (${failed.length} refused, e.g. a locked set's pictures)`;
      }
      notices.push({
        level: failed.length ? "warning" : "success",
        key: "tag-clipboard-paste",
        text,
      });
      if (failed.length && failed.length === results.length) return null;
      return { pictureIds: [...touched], failed: failed.length };
    } catch (e) {
      console.error("Pasting the copied tags failed on", ids, tags, e);
      notices.push({ level: "error", text: "Couldn't paste the tags." });
      return null;
    }
  }

  return {
    clipboardTags,
    chipSize,
    hydrate,
    setChipSize,
    copyTags,
    pasteClipboard,
  };
});
