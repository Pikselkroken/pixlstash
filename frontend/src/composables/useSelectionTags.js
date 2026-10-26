import { computed, ref } from "vue";
import { newOperationBatchId } from "../utils/apiClient";
import {
  addPictureTag,
  bulkFetchTags,
  confirmTagPrediction,
  listTagPredictions,
  rejectTagPrediction,
  removePictureTag,
} from "../api/tags";
import { errorDetail } from "../utils/apiError";

/** How many pictures' tags a selection reads at most. */
export const MAX_TAG_FETCH = 100;

/**
 * The tags of a multi-picture selection, and the gestures on them.
 *
 * Coverage (a tag on all of them, or on some), the rejected predictions summed
 * across them, and the fan-outs that remove a tag from all, add it to the rest
 * or confirm a rejected one on all. Shared by the tag dialog (`TbTagPanel`) and
 * the grid's rail (#2161), so the two read and write a selection's tags the
 * same way.
 *
 * Nothing is read until the host calls `fetchSelectedImageTags` and
 * `fetchSelectedImagePredictions`; each gesture re-reads what it changed.
 *
 * @param {() => Array<number|string>} selectedIds - the selection, read on use.
 * @param {Object} [options]
 * @param {(payload: Object) => void} [options.onApplied] - told after a
 *   gesture changed tags (`{ tag, pictureIds, action }`), so the host can
 *   refresh what shows them.
 */
export function useSelectionTags(selectedIds, { onApplied = () => {} } = {}) {
  const fetchedTagData = ref([]);
  const tagDataLoading = ref(false);
  const tagDataCapped = ref(false);
  const fetchedPredictionData = ref([]);
  const predictionLoading = ref(false);
  const predictionAcceptanceThreshold = ref(0.95);
  const labelThresholds = ref({});
  const tagActionLoading = ref([]);
  const tagError = ref("");
  // The newest read of each kind wins: an answer for a selection the user has
  // since changed is dropped rather than shown under the new one.
  let tagRead = 0;
  let predictionRead = 0;

  function pictureIds() {
    return (Array.isArray(selectedIds()) ? selectedIds() : [])
      .map((id) => Number(id))
      .filter((id) => Number.isFinite(id) && id > 0);
  }

  async function fetchSelectedImageTags() {
    const ids = pictureIds();
    tagDataCapped.value = ids.length > MAX_TAG_FETCH;
    const toFetch = ids.slice(0, MAX_TAG_FETCH);
    if (!toFetch.length) {
      tagRead += 1;
      fetchedTagData.value = [];
      tagDataLoading.value = false;
      return;
    }
    const mine = (tagRead += 1);
    if (!fetchedTagData.value.length) tagDataLoading.value = true;
    try {
      const rows = await bulkFetchTags(toFetch);
      if (mine !== tagRead) return;
      fetchedTagData.value = Array.isArray(rows) ? rows : [];
    } catch (e) {
      console.warn("Couldn't read the selected pictures' tags", e);
      if (mine === tagRead) fetchedTagData.value = [];
    } finally {
      if (mine === tagRead) tagDataLoading.value = false;
    }
  }

  async function fetchSelectedImagePredictions() {
    const ids = pictureIds()
      .slice(0, MAX_TAG_FETCH);
    const mine = (predictionRead += 1);
    if (!ids.length) {
      fetchedPredictionData.value = [];
      predictionLoading.value = false;
      return;
    }
    predictionLoading.value = true;
    try {
      const results = await Promise.all(
        ids.map((id) =>
          listTagPredictions(id, {
            status: "REJECTED",
          })
            .then((payload) => {
              const predictions = Array.isArray(payload)
                ? payload
                : Array.isArray(payload?.tag_predictions)
                  ? payload.tag_predictions
                  : [];
              const threshold = Number(payload?.meta?.acceptance_threshold);
              if (mine === predictionRead) {
                if (
                  Number.isFinite(threshold) &&
                  threshold > 0 &&
                  threshold <= 1
                ) {
                  predictionAcceptanceThreshold.value = threshold;
                }
                labelThresholds.value = payload?.meta?.label_thresholds || {};
              }
              return { id, predictions };
            })
            .catch((e) => {
              console.warn(`Couldn't read picture ${id}'s rejected tags`, e);
              return { id, predictions: [] };
            }),
        ),
      );
      if (mine !== predictionRead) return;
      fetchedPredictionData.value = results;
    } catch (e) {
      console.warn("Couldn't read the selected pictures' rejected tags", e);
      if (mine === predictionRead) fetchedPredictionData.value = [];
    } finally {
      if (mine === predictionRead) predictionLoading.value = false;
    }
  }

  const totalWithTagData = computed(() => fetchedTagData.value.length);

  const tagFrequency = computed(() => {
    const freq = new Map();
    for (const img of fetchedTagData.value) {
      for (const t of img.tags || []) {
        const name = typeof t === "string" ? t : t.tag;
        const tagId = typeof t === "string" ? null : t.id;
        if (!name) continue;
        if (!freq.has(name))
          freq.set(name, { count: 0, tagsByImageId: new Map() });
        const entry = freq.get(name);
        entry.count++;
        entry.tagsByImageId.set(Number(img.id), tagId);
      }
    }
    return freq;
  });

  const tagsOnAll = computed(() => {
    if (!totalWithTagData.value) return [];
    return [...tagFrequency.value.entries()]
      .filter(([, v]) => v.count === totalWithTagData.value)
      .map(([name, v]) => ({
        name,
        count: v.count,
        tagsByImageId: v.tagsByImageId,
      }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  });

  const tagMinCoverage = ref(1);

  const tagsOnSome = computed(() => {
    if (!totalWithTagData.value) return [];
    return [...tagFrequency.value.entries()]
      .filter(
        ([, v]) =>
          v.count > 0 &&
          v.count < totalWithTagData.value &&
          v.count >= tagMinCoverage.value,
      )
      .map(([name, v]) => ({
        name,
        count: v.count,
        tagsByImageId: v.tagsByImageId,
      }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  });

  const predMinCoverage = ref(1);

  const aggregatedPredictions = computed(() => {
    if (!fetchedPredictionData.value.length) return [];
    const confirmedAll = new Set(
      tagsOnAll.value.map((t) => t.name.toLowerCase()),
    );
    const freq = new Map();
    for (const { id, predictions } of fetchedPredictionData.value) {
      for (const p of predictions) {
        const key = p.tag.toLowerCase();
        if (confirmedAll.has(key)) continue;
        // Skip synthetic `manual` rows: these are human-label ledger entries with a
        // placeholder confidence (1.0/0.0), not tagger predictions. A manually
        // removed tag would otherwise resurface here as a high-confidence "suggested
        // tag to confirm" (see backend label_ledger.record_human_label).
        if (p.model_version === "manual") continue;
        if (!freq.has(key))
          freq.set(key, { tag: p.tag, count: 0, totalConf: 0, ids: [] });
        const e = freq.get(key);
        e.count++;
        e.totalConf += p.confidence;
        e.ids.push(id);
      }
    }
    return [...freq.values()]
      .filter((e) => e.count >= predMinCoverage.value)
      .map((e) => {
        const avgConf = e.totalConf / e.count;
        const perLabel = labelThresholds.value[e.tag];
        const threshold =
          typeof perLabel === "number" && Number.isFinite(perLabel)
            ? perLabel
            : Number(predictionAcceptanceThreshold.value) || 0.95;
        const avgNeeded = Math.max(0, threshold - avgConf);
        return { ...e, avgConf, avgNeeded };
      })
      .sort((a, b) => b.count - a.count || b.avgConf - a.avgConf);
  });

  const predActionLoading = ref([]);

  /**
   * Run one request per picture and keep going past a failure.
   *
   * Every gesture here is a fan-out, and with `Promise.all` one refused
   * picture skipped the notice and the re-read for the others that did
   * change, leaving the panel showing tags that were no longer true.
   *
   * @param {Array<number>} ids
   * @param {(id: number) => Promise} call
   * @returns {Promise<Array<number>>} the ids whose request succeeded;
   *   `tagError` says how many did not, and why.
   */
  async function fanOut(ids, call) {
    const results = await Promise.allSettled(ids.map((id) => call(id)));
    const done = ids.filter((_, i) => results[i].status === "fulfilled");
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length) {
      const reason = failed[0].reason;
      console.warn(`${failed.length} of ${ids.length} tag changes failed`, reason);
      tagError.value = `${failed.length} of ${ids.length} pictures were not changed: ${
        errorDetail(reason) || reason?.message || String(reason)
      }`;
    }
    return done;
  }

  /**
   * Confirm one predicted tag on every picture that carries the prediction.
   *
   * The fan-out records one operation per picture; they share a gesture batch id
   * so the whole confirm is one history step (the receipt shows it as one entry
   * with its `+N` count) and one Ctrl+Z walks all of it back.
   *
   * @param {Object} predEntry - the aggregated prediction row (`tag`, `ids`).
   * @param {Object} [options]
   * @param {string} [options.batchId] - an id from a caller that is already part
   *   of a larger gesture; a fresh one is minted otherwise.
   */
  async function confirmPredictionOnAll(
    predEntry,
    { batchId = newOperationBatchId() } = {},
  ) {
    if (predActionLoading.value.includes(predEntry.tag)) return;
    predActionLoading.value = [...predActionLoading.value, predEntry.tag];
    tagError.value = "";
    try {
      const done = await fanOut(predEntry.ids, (id) =>
        confirmTagPrediction(id, predEntry.tag, { batchId }),
      );
      if (done.length) {
        onApplied({ tag: predEntry.tag, pictureIds: done, action: "add" });
      }
      await Promise.all([
        fetchSelectedImageTags(),
        fetchSelectedImagePredictions(),
      ]);
    } finally {
      predActionLoading.value = predActionLoading.value.filter(
        (n) => n !== predEntry.tag,
      );
    }
  }

  const tagsOnSomeHiddenCount = computed(() => {
    if (!totalWithTagData.value || tagMinCoverage.value <= 1) return 0;
    return [...tagFrequency.value.entries()].filter(
      ([, v]) =>
        v.count > 0 &&
        v.count < totalWithTagData.value &&
        v.count < tagMinCoverage.value,
    ).length;
  });

  /**
   * Remove one tag from every selected picture that carries it.
   *
   * @param {Object} tagEntry - the aggregated tag row (`name`, `tagsByImageId`).
   * @param {Object} [options]
   * @param {string} [options.batchId] - gesture batch id shared across the
   *   fan-out (and with the reject that follows it on a drag), so the whole
   *   gesture is one history step and one Ctrl+Z.
   */
  async function removeTagFromAll(
    tagEntry,
    { batchId = newOperationBatchId() } = {},
  ) {
    if (tagActionLoading.value.includes(tagEntry.name)) return;
    tagActionLoading.value = [...tagActionLoading.value, tagEntry.name];
    tagError.value = "";
    const tagIdOf = new Map(
      [...tagEntry.tagsByImageId.entries()].filter(([, tagId]) => tagId != null),
    );
    try {
      const done = await fanOut([...tagIdOf.keys()], (imgId) =>
        removePictureTag(imgId, tagIdOf.get(imgId), { batchId }),
      );
      if (done.length) {
        onApplied({ tag: tagEntry.name, pictureIds: done, action: "remove" });
      }
      await fetchSelectedImageTags();
    } finally {
      tagActionLoading.value = tagActionLoading.value.filter(
        (n) => n !== tagEntry.name,
      );
    }
  }

  async function addTagToRemaining(tagEntry) {
    if (tagActionLoading.value.includes(tagEntry.name)) return;
    tagActionLoading.value = [...tagActionLoading.value, tagEntry.name];
    tagError.value = "";
    // Past the read cap the tags of the rest are unknown, so "the rest" is
    // every selected picture not known to have it; adding a tag a picture
    // already has changes nothing.
    const candidates = tagDataCapped.value
      ? pictureIds()
      : fetchedTagData.value.map((img) => Number(img.id));
    const missingIds = candidates.filter(
      (id) => !tagEntry.tagsByImageId.has(id),
    );
    try {
      const done = await fanOut(missingIds, (id) =>
        addPictureTag(id, tagEntry.name),
      );
      if (done.length) {
        onApplied({ tag: tagEntry.name, pictureIds: done, action: "add" });
      }
      await fetchSelectedImageTags();
    } finally {
      tagActionLoading.value = tagActionLoading.value.filter(
        (n) => n !== tagEntry.name,
      );
    }
  }

  /**
   * Record the human NEG for a tag on every picture it was just removed from.
   *
   * @param {Object} tagEntry - the aggregated tag row (`name`, `tagsByImageId`).
   * @param {Object} [options]
   * @param {string} [options.batchId] - the gesture batch id of the removal this
   *   reject belongs to.
   */
  async function rejectTagOnAll(tagEntry, { batchId } = {}) {
    const imageIds = [...tagEntry.tagsByImageId.keys()];
    // A second drop of the same tag while the first is still out would
    // record every NEG twice.
    if (!imageIds.length || tagActionLoading.value.includes(tagEntry.name)) {
      return;
    }
    tagActionLoading.value = [...tagActionLoading.value, tagEntry.name];
    try {
      await fanOut(imageIds, (id) =>
        rejectTagPrediction(id, tagEntry.name, { batchId }),
      );
      await fetchSelectedImagePredictions();
    } finally {
      tagActionLoading.value = tagActionLoading.value.filter(
        (n) => n !== tagEntry.name,
      );
    }
  }

  /**
   * Add these tags to every selected picture.
   *
   * @param {Array<string>} tags
   * @returns {Promise<boolean>} whether they were added to every picture;
   *   `tagError` says why not.
   */
  async function addTagsToAll(tags) {
    const ids = pictureIds();
    if (!ids.length || !tags.length) return false;
    tagError.value = "";
    const done = await fanOut(ids, (id) =>
      Promise.all(tags.map((tag) => addPictureTag(id, tag))),
    );
    if (done.length) {
      onApplied({ tag: tags.join(", "), pictureIds: done, action: "add" });
    }
    await fetchSelectedImageTags();
    return done.length === ids.length;
  }

  return {
    fetchedTagData,
    tagDataLoading,
    tagDataCapped,
    fetchedPredictionData,
    predictionLoading,
    predictionAcceptanceThreshold,
    labelThresholds,
    tagActionLoading,
    predActionLoading,
    tagError,
    totalWithTagData,
    tagsOnAll,
    tagMinCoverage,
    tagsOnSome,
    tagsOnSomeHiddenCount,
    predMinCoverage,
    aggregatedPredictions,
    fetchSelectedImageTags,
    fetchSelectedImagePredictions,
    removeTagFromAll,
    addTagToRemaining,
    addTagsToAll,
    confirmPredictionOnAll,
    rejectTagOnAll,
  };
}
