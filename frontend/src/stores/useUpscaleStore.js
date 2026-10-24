// useUpscaleStore.js - the one upscale, offered in two places (#1868).
//
// The Run popup's "Upscale · added for this run" row and the Upscale… verb on
// the selection pill and the lightbox send the same nodes, so they share one
// remembered factor and model: changing it in one place changes the default in
// the other. The verb's dialog is mounted once, in App.vue, and opened here.

import { defineStore } from "pinia";
import { computed, onScopeDispose, ref } from "vue";
import { onSessionReset } from "../utils/apiClient";
import { listAdapters } from "../api/modelShelf";
import { modelName } from "../utils/modelShelf";

export const UPSCALE_FACTORS = [1.5, 2, 4];
const KEY = "pixlstash:upscale";

function load() {
  try {
    const stored = JSON.parse(window.localStorage?.getItem(KEY) || "null");
    if (stored && UPSCALE_FACTORS.includes(stored.factor)) return stored;
  } catch {
    // Unreadable or unavailable: the defaults below.
  }
  return { factor: 2, model: null };
}

export const useUpscaleStore = defineStore("upscale", () => {
  const remembered = load();
  /** 1.5, 2 or 4. */
  const factor = ref(remembered.factor);
  /**
   * The SHA-256 of the shelf upscaler last picked, "" for the Lanczos resize
   * picked by hand, null for no pick yet (the shelf's first, see `model`).
   */
  const chosen = ref(remembered.model ?? null);
  /** The upscalers on the shelf, `[{sha256, name}]`; null until read. */
  const models = ref(null);
  /** The Upscale… dialog's pictures, or null when it is closed. */
  const pictureIds = ref(null);

  function save() {
    try {
      window.localStorage?.setItem(
        KEY,
        JSON.stringify({ factor: factor.value, model: chosen.value }),
      );
    } catch {
      // Not remembered this session; the choice still applies.
    }
  }

  async function loadModels() {
    try {
      const rows = await listAdapters({ fileKind: "upscaler" });
      models.value = rows
        .filter((row) => row.sha256)
        .map((row) => ({
          sha256: row.sha256,
          name: modelName(row).text || row.filename,
        }));
    } catch (err) {
      console.warn("Could not list the upscalers on the shelf:", err);
      models.value = [];
    }
  }

  /**
   * The model a run sends: the one last used while the shelf still has it,
   * else the shelf's first, else none (the resize). An explicit pick of the
   * resize is kept as that.
   */
  const model = computed(() => {
    const list = models.value || [];
    if (chosen.value === "") return null;
    return (
      list.find((m) => m.sha256 === chosen.value) || list[0] || null
    );
  });

  function setFactor(value) {
    factor.value = value;
    save();
  }

  /** A model's SHA-256, or "" for the resize. */
  function setModel(sha256) {
    chosen.value = sha256;
    save();
  }

  /** The `upscale` of a request body. */
  function body() {
    return { factor: factor.value, model_sha256: model.value?.sha256 ?? null };
  }

  function openFor(ids) {
    if (ids?.length) pictureIds.value = [...ids];
  }

  function close() {
    pictureIds.value = null;
  }

  // The shelf's rows and the dialog's pictures are the session's. The factor
  // and model last used are the owner's own preference and stay.
  const unsubscribeSessionReset = onSessionReset(() => {
    close();
    models.value = null;
  });
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    factor,
    models,
    model,
    pictureIds,
    loadModels,
    setFactor,
    setModel,
    body,
    openFor,
    close,
  };
});
