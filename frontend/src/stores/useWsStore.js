import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";
import { onSessionReset, setRequestClientId } from "../utils/apiClient";

const CLIENT_ID_STORAGE_KEY = "pixlstash:clientId";

// Generate (or restore) a stable per-tab client id. Persisted in sessionStorage
// so it survives a reload but stays unique per tab. Private-browsing modes can
// throw on sessionStorage access, so every access is wrapped and falls back to
// an in-memory id (logged, never silently swallowed).
function resolveClientId() {
  let stored = null;
  try {
    stored = window.sessionStorage.getItem(CLIENT_ID_STORAGE_KEY);
  } catch (e) {
    console.warn(
      "pixlstash: sessionStorage unavailable, using an in-memory client id",
      e,
    );
  }
  if (stored) return stored;

  const generated =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `cid-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  try {
    window.sessionStorage.setItem(CLIENT_ID_STORAGE_KEY, generated);
  } catch (e) {
    console.warn(
      "pixlstash: could not persist client id to sessionStorage; it will not survive a reload",
      e,
    );
  }
  return generated;
}

export const useWsStore = defineStore("ws", () => {
  const wsTagUpdate = ref({ key: 0, pictureIds: [] });
  const wsDescriptionUpdate = ref({ key: 0, pictureIds: [] });
  // Signals a smart_score recompute landed for the given pictures. Unlike the
  // grid (which only shows/sorts smart_score under SMART_SCORE sort, so it gates
  // this behind pictureChangeAffectsView), the open lightbox ALWAYS shows the
  // score, so it must refresh on any smart_score change regardless of sort or
  // origin. Fired for both origin-stamped (interactive tag edits) and origin-less
  // (bulk penalised-tag settings drain) CHANGED_PICTURES events.
  const wsSmartScoreUpdate = ref({ key: 0, pictureIds: [] });
  // Signals a DetectionTask (Segment) finished for the given pictures. The grid
  // treats `detections` as a card-content field and refreshes the card in place,
  // but that refresh is deferred while the lightbox is open, and the overlay
  // fetches its boxes from /pictures/{id}/detections rather than from the card.
  // Without this signal an open overlay kept showing the pre-segment boxes until
  // it was closed and reopened.
  const wsDetectionUpdate = ref({ key: 0, pictureIds: [] });
  // Signals the text read from the given pictures (OCR, `ocr_text`) changed.
  // The grid never shows that text, so only the open lightbox listens.
  const wsTextUpdate = ref({ key: 0, pictureIds: [] });
  // Signals the given pictures were TURNED (`orientation`): an in-place rotate,
  // or the undo/redo of one. The grid's card refresh for this is deferred while
  // the lightbox is open (§9.1), and the overlay builds its `<img>` URL from
  // `orientation`, which none of its other signals re-read - so without this the
  // open lightbox kept showing the picture the wrong way up until it was closed
  // and reopened. Deliberately narrower than `pixels`, which a thumbnail
  // regeneration and a layout move also raise without turning anything.
  const wsOrientationUpdate = ref({ key: 0, pictureIds: [] });
  const wsPluginProgress = ref({ key: 0, payload: null });
  const isUploadInProgress = ref(false);

  // Stable per-tab client id. Mirrored into apiClient module scope so the
  // request interceptor can attach it as X-Client-Id without a Pinia lookup.
  const clientId = ref(resolveClientId());
  setRequestClientId(clientId.value);

  // Pills are driven by ids (deduplicated), not raw counts, so a click can
  // splice exactly the affected pictures into the grid.
  const pendingExternalImportIds = ref([]);
  const sortChangedExternalIds = ref([]);

  const pendingExternalImportCount = computed(
    () => pendingExternalImportIds.value.length,
  );
  const sortChangedExternalCount = computed(
    () => sortChangedExternalIds.value.length,
  );

  function addPendingExternalImportIds(ids) {
    if (!Array.isArray(ids) || !ids.length) return;
    const next = new Set(pendingExternalImportIds.value);
    for (const id of ids) {
      if (id != null) next.add(id);
    }
    pendingExternalImportIds.value = Array.from(next);
  }

  function clearPendingExternalImportIds() {
    pendingExternalImportIds.value = [];
  }

  function addSortChangedExternalIds(ids) {
    if (!Array.isArray(ids) || !ids.length) return;
    const next = new Set(sortChangedExternalIds.value);
    for (const id of ids) {
      if (id != null) next.add(id);
    }
    sortChangedExternalIds.value = Array.from(next);
  }

  function clearSortChangedExternalIds() {
    sortChangedExternalIds.value = [];
  }

  // Both pills are picture ids, and a picture id means nothing outside the
  // session that produced it - a different library reuses the same numbers.
  // Logout notifies BEFORE it flips `isAuthenticated`, so anything the app
  // writes here on its way down (`useUpdatesSocket` hands over the ids a tag
  // pass was holding) lands after the reset and would be offered to whoever
  // logs in next, in the same tab, as "pictures that changed". Same reasoning
  // as `useLibrariesStore.reset`: one chokepoint, no second mechanism.
  const unsubscribeSessionReset = onSessionReset(() => {
    clearPendingExternalImportIds();
    clearSortChangedExternalIds();
  });
  onScopeDispose(unsubscribeSessionReset);

  return {
    wsTagUpdate,
    wsDescriptionUpdate,
    wsSmartScoreUpdate,
    wsDetectionUpdate,
    wsTextUpdate,
    wsOrientationUpdate,
    wsPluginProgress,
    isUploadInProgress,
    clientId,
    pendingExternalImportIds,
    sortChangedExternalIds,
    pendingExternalImportCount,
    sortChangedExternalCount,
    addPendingExternalImportIds,
    clearPendingExternalImportIds,
    addSortChangedExternalIds,
    clearSortChangedExternalIds,
  };
});
