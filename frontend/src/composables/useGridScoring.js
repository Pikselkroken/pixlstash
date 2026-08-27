import { computed, nextTick } from "vue";
import { isReadOnly } from "../utils/apiClient";
import {
  applyScores,
  getGuestScores,
  listPicturesByIds,
  submitGuestScores,
} from "../api/pictures";
import { getPictureId } from "../utils/media.js";
import { toggleScore } from "../utils/utils.js";
import { useSortStore } from "../stores/useSortStore";
import { useFilterStore } from "../stores/useFilterStore";
import { useNoticeStore } from "../stores/useNoticeStore";
import { errorDetail } from "../utils/apiError";

/**
 * Scoring, and everything the grid has to do about it.
 *
 * Setting a score is only half the job: when the grid is sorted by score or by
 * smart score, the picture that just changed is in the wrong row, so these
 * functions also move it - splicing it to its new position rather than
 * refetching, which is what keeps a rating pass from flickering.
 *
 * Read-only sessions score into a guest session instead, held server-side
 * against a generated id, so a visitor can rate without an account.
 *
 * @param {Object} deps - Grid state and callbacks, same shape as useGridFetch's.
 */
export function useGridScoring({
  backendUrl,
  allGridImages,
  lastFetchedGridImages,
  loadedRanges,
  visibleStart,
  visibleEnd,
  renderBuffer,
  imagesLoading,
  overlayOpen,
  pendingOverlayGridRefresh,
  preserveScrollOnNextFetch,
  skipNextWsRefresh,
  gridContainer,
  guestSessionId,
  guestConsentState,
  guestScoreMap,
  guestConsentBannerVisible,
  pendingGuestScoreIntent,
  emit,
  debouncedFetchAllGridImages,
  fetchImageInfo,
  rebuildGridImagesFromLastFetch,
  triggerNewImageHighlight,
  updateVisibleThumbnails,
  maybeRefreshOverlayForComfyui,
  removeImagesById,
}) {
  const sortStore = useSortStore();
  const filterStore = useFilterStore();
  const noticeStore = useNoticeStore();

  // SCORING
  // ============================================================
  async function setScore(img, n) {
    if (isReadOnly.value) {
      setGuestScore(img, n);
      return;
    }
    const newScore = toggleScore(img.score, n);
    applyScore(img, newScore);
  }

  // ---- Guest scoring helpers ----

  function _generateGuestSessionId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    // Fallback for older browsers - use cryptographically secure random bytes
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant bits
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
    return [
      hex.slice(0, 4).join(""),
      hex.slice(4, 6).join(""),
      hex.slice(6, 8).join(""),
      hex.slice(8, 10).join(""),
      hex.slice(10, 16).join(""),
    ].join("-");
  }

  function _getOrCreateGuestSessionId() {
    if (guestSessionId.value) return guestSessionId.value;
    const id = _generateGuestSessionId();
    guestSessionId.value = id;
    return id;
  }

  async function _submitGuestScores(scores, setCookie) {
    const sid = _getOrCreateGuestSessionId();
    const payload = { session_id: sid, set_cookie: setCookie, scores };
    await submitGuestScores(payload);
  }

  function setGuestScore(img, n) {
    const currentScore = guestScoreMap.value.get(img.id) ?? null;
    const newScore = toggleScore(currentScore, n);

    if (guestConsentState.value === null) {
      // Show consent banner; queue the intent
      pendingGuestScoreIntent.value = { img, newScore };
      guestConsentBannerVisible.value = true;
      return;
    }

    // Optimistic local update
    const updated = new Map(guestScoreMap.value);
    updated.set(img.id, newScore);
    guestScoreMap.value = updated;

    const setCookie = guestConsentState.value === "accepted";
    _submitGuestScores({ [String(img.id)]: newScore }, setCookie)
      .then(() => {
        if (isScoreSortActive()) {
          debouncedFetchAllGridImages({ force: true });
        }
      })
      .catch((err) => {
        console.error("Failed to submit guest score:", err);
      });
  }

  async function handleGuestConsentAccepted() {
    guestConsentState.value = "accepted";
    guestConsentBannerVisible.value = false;
    // Do not persist the guest session identifier in localStorage.
    // The server-managed HttpOnly cookie handles durable session continuity;
    // keeping the session_id only in memory is sufficient for the current visit.
    const intent = pendingGuestScoreIntent.value;
    pendingGuestScoreIntent.value = null;
    if (intent) {
      const updated = new Map(guestScoreMap.value);
      updated.set(intent.img.id, intent.newScore);
      guestScoreMap.value = updated;
      await _submitGuestScores(
        { [String(intent.img.id)]: intent.newScore },
        true,
      )
        .then(() => {
          if (isScoreSortActive()) debouncedFetchAllGridImages({ force: true });
        })
        .catch((err) => console.error("Failed to submit guest score:", err));
    }
  }

  function handleGuestConsentRejected() {
    guestConsentState.value = "rejected";
    guestConsentBannerVisible.value = false;
    // Do NOT persist the session ID anywhere - if the user reloads they get a
    // brand-new session with no connection to these scores.
    const intent = pendingGuestScoreIntent.value;
    pendingGuestScoreIntent.value = null;
    if (intent) {
      const updated = new Map(guestScoreMap.value);
      updated.set(intent.img.id, intent.newScore);
      guestScoreMap.value = updated;
      _submitGuestScores({ [String(intent.img.id)]: intent.newScore }, false)
        .then(() => {
          if (isScoreSortActive()) debouncedFetchAllGridImages({ force: true });
        })
        .catch((err) => console.error("Failed to submit guest score:", err));
    }
  }

  async function fetchGuestScores() {
    // Kept only as a fallback / explicit refresh. The main listing
    // (GET /pictures) now overlays guest scores onto img.score server-side.
    try {
      const resp = await getGuestScores({ baseUrl: backendUrl });
      const scores = resp?.scores ?? {};
      const map = new Map();
      for (const [k, v] of Object.entries(scores)) {
        map.set(Number(k), v);
      }
      guestScoreMap.value = map;
    } catch (err) {
      console.error("[guest-scores] Failed to fetch guest scores:", err);
    }
  }

  function initGuestSession() {
    const readOnly = isReadOnly.value;
    const cookies = document.cookie;
    const ls = localStorage.getItem("guest_session_id");
    if (!readOnly) return;
    // A non-HttpOnly sentinel cookie is set alongside the HttpOnly guest_session
    // cookie when the user accepted persistent storage.
    const hasCookieConsent = cookies
      .split(";")
      .some((c) => c.trim().startsWith("guest_session_active=1"));
    if (hasCookieConsent) {
      guestConsentState.value = "accepted";
      // Restore the session ID from localStorage so POST bodies stay in sync
      // with the HttpOnly cookie the server already knows about.
      if (ls) {
        guestSessionId.value = ls;
      }
      // Scores are now overlaid by the backend in GET /pictures, so
      // fetchAllGridImages() will include them in img.score directly.
      // fetchGuestScores() is only needed to pre-populate guestScoreMap
      // for optimistic-update display before the grid loads.
      fetchGuestScores();
      return;
    }
    // No cookie consent - fresh start.  The banner will appear on first score.
    // (Rejected users are intentionally not remembered across page loads.)
  }

  function isScoreSortActive() {
    return typeof sortStore.selectedSort === "string"
      ? sortStore.selectedSort.toUpperCase() === "SCORE"
      : false;
  }

  function isCharacterLikenessSortActive() {
    return typeof sortStore.selectedSort === "string"
      ? sortStore.selectedSort.toUpperCase() === "CHARACTER_LIKENESS"
      : false;
  }

  function isSmartScoreSortActive() {
    return typeof sortStore.selectedSort === "string"
      ? sortStore.selectedSort.toUpperCase().includes("SMART_SCORE")
      : false;
  }

  // Single source of truth for grid sort direction. ALL client-side ordering -
  // score/smart-score repositions, the apply-scores re-sort, and incremental
  // inserts - must use the SAME rule, or a card lands in a different spot than the
  // array it is spliced into. Nullish selectedDescending → ascending (the store
  // defaults it to a real `true`). Keep this as one computed: a lone inlined
  // `!== false` previously drifted here and mispositioned inserted cards.
  const gridSortDescending = computed(
    () => sortStore.selectedDescending === true,
  );

  function getGridSmartScoreValue(img) {
    if (!img) return null;
    const raw =
      typeof img.smartScore === "number"
        ? img.smartScore
        : typeof img.smart_score === "number"
          ? img.smart_score
          : null;
    return Number.isFinite(raw) ? raw : null;
  }

  // The ONE null-ordering rule shared by every client-side smart-score sort path
  // (fresh insert AND reposition). SQLite ranks NULL below every real value, and
  // the backend sorts the smart_score column with a plain .asc()/.desc() and no
  // NULLS clause (pixlstash/db_models/picture.py), so NULLs sort FIRST on ascending
  // and LAST on descending. Map a null/absent score to -Infinity so the shared
  // comparator (which flips on `descending`) lands a null-scored card exactly where
  // the server put it, in BOTH directions. A 0 sentinel is wrong: it collides with
  // a genuine zero and, now that smart scores can be negative and null (tag edits
  // invalidate them), it mis-orders nulls relative to real scores. Route EVERY path
  // through this helper - never an inline ternary or `?? 0` - so the insert and
  // reposition paths cannot drift (the failure the gridSortDescending comment warns
  // of). This is only an ordering key; it must never be written back as a card's
  // displayed smart score, or a null-scored card would render a fake 0.
  function smartScoreSortKey(smartScore) {
    return Number.isFinite(smartScore) ? smartScore : -Infinity;
  }

  function invalidateVisibleThumbnailRanges() {
    const start = Math.max(0, visibleStart.value - renderBuffer.value);
    const end = Math.min(
      allGridImages.value.length,
      visibleEnd.value + renderBuffer.value,
    );
    loadedRanges.value = loadedRanges.value.filter(
      ([rangeStart, rangeEnd]) => rangeEnd <= start || rangeStart >= end,
    );
    updateVisibleThumbnails();
  }

  function _spliceAndReinsert(
    items,
    currentIndex,
    target,
    targetScore,
    getScore,
    descending,
  ) {
    items.splice(currentIndex, 1);
    let insertIndex = items.findIndex((item) => {
      const score = getScore(item);
      return descending ? score < targetScore : score > targetScore;
    });
    if (insertIndex === -1) insertIndex = items.length;
    if (insertIndex === currentIndex) {
      const updated = allGridImages.value.slice();
      updated[currentIndex] = { ...target, idx: currentIndex };
      allGridImages.value = updated;
      // Still invalidate: we are only here because the card's score changed, and the
      // batch-sourced fields that change with it (penalised_tags - the problem
      // indicator - plus faces/detections) are refreshed ONLY by a thumbnail batch.
      // Without this, loadedRanges still covers the window, updateVisibleThumbnails
      // no-ops, and a card whose position happens not to move keeps its stale
      // indicator. Adding a penalised tag to an already low-scoring card lands
      // exactly here, since it is already at the bottom of a descending sort.
      invalidateVisibleThumbnailRanges();
      return null;
    }
    items.splice(insertIndex, 0, target);
    for (let i = 0; i < items.length; i += 1) {
      items[i].idx = i;
    }
    allGridImages.value = items;
    invalidateVisibleThumbnailRanges();
    return insertIndex;
  }

  function repositionImageByScore(imageId, newScore) {
    const items = allGridImages.value.slice();
    const dId = getPictureId(imageId);
    const currentIndex = items.findIndex(
      (item) => getPictureId(item?.id) === dId,
    );
    if (currentIndex === -1) return;

    const target = items[currentIndex];
    target.score = newScore;
    const targetScore = newScore ?? 0;
    const descending = gridSortDescending.value;
    const insertIndex = _spliceAndReinsert(
      items,
      currentIndex,
      target,
      targetScore,
      (item) => item.score ?? 0,
      descending,
    );
    if (insertIndex !== null) {
      nextTick(() => {
        const grid = gridContainer.value;
        if (!grid) return;
        const card = grid.querySelectorAll(".image-card")[insertIndex];
        if (card && card.scrollIntoView) {
          card.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
    }
  }

  let smartScoreRepositioning = false;

  function repositionImageBySmartScore(imageId, smartScore, latestInfo = null) {
    if (smartScoreRepositioning) return;
    smartScoreRepositioning = true;
    try {
      const items = allGridImages.value.slice();
      const currentIndex = items.findIndex((item) => item.id === imageId);
      if (currentIndex === -1) return;

      // Keep the ordering key separate from the displayed value. The card must
      // store the TRUE smart score (a real number or null → "no score"); only the
      // sort key collapses null to the SQLite sentinel via smartScoreSortKey.
      // Writing the sentinel onto `smartScore` would make a null-scored card render
      // a fake 0.
      const normalisedScore = Number.isFinite(smartScore) ? smartScore : null;
      const targetKey = smartScoreSortKey(normalisedScore);
      // Write the TRUE score to BOTH the camelCase and snake_case keys.
      // getGridSmartScoreValue reads `smartScore` then falls back to `smart_score`
      // (grid cards / metadata responses carry both), so setting only one key would
      // let a stale value leak through the fallback and render a wrong (or fake 0)
      // score. Both must hold the normalised value - null for "no score".
      const target = {
        ...items[currentIndex],
        ...(latestInfo && typeof latestInfo === "object" ? latestInfo : {}),
        smartScore: normalisedScore,
        smart_score: normalisedScore,
        thumbnail:
          items[currentIndex]?.thumbnail ?? latestInfo?.thumbnail ?? null,
      };
      const descending = gridSortDescending.value;
      _spliceAndReinsert(
        items,
        currentIndex,
        target,
        targetKey,
        (item) => smartScoreSortKey(getGridSmartScoreValue(item)),
        descending,
      );
    } finally {
      smartScoreRepositioning = false;
    }
  }

  // Numeric sort key for a freshly-fetched grid item under the active sort,
  // mirroring the fields the grid already displays/sorts on (see
  // getCompactGroupLabel / sort-overlay logic). Returns a finite number used to
  // find the insert index; `id` is the implicit tiebreaker.
  function gridImageSortKey(img) {
    const sort =
      typeof sortStore.selectedSort === "string" ? sortStore.selectedSort : "";
    if (sort === "IMPORTED_AT" && img?.imported_at) {
      return Date.parse(img.imported_at) || 0;
    }
    if (sort.includes("DATE") && img?.created_at) {
      return Date.parse(img.created_at) || 0;
    }
    if (sort.includes("SMART_SCORE")) {
      // Match the server's ORDER BY exactly via the shared null rule (see
      // smartScoreSortKey). The reposition path uses the same helper, so the two
      // ordering paths cannot drift.
      return smartScoreSortKey(getGridSmartScoreValue(img));
    }
    if (
      sort.includes("CHARACTER_LIKENESS") &&
      typeof img?.character_likeness === "number"
    ) {
      return img.character_likeness;
    }
    if (sort === "TEXT_CONTENT" && typeof img?.text_score === "number") {
      return img.text_score;
    }
    if (typeof img?.score === "number") return img.score;
    // Unknown / id-ordered sort → fall back to the picture id.
    return Number(getPictureId(img?.id)) || 0;
  }

  // Insert newly-added pictures into the grid at their sorted position without a
  // full reload. Always mutates lastFetchedGridImages then rebuilds, because the
  // v-for key embeds img.idx - splicing allGridImages directly would corrupt the
  // virtual-scroll window. Deferred to the pill while a streaming fetch is in
  // flight (that fetch writes allGridImages wholesale from a sized placeholder).
  // `options.highlight` (default true) drives the new-picture flash. A scrapheap
  // comeback passes false: the flash means "this was not here before", which is
  // the wrong story for a picture the user just undid the removal of.
  async function insertGridImagesById(ids, options = {}) {
    const highlight = options?.highlight !== false;
    const wanted = (Array.isArray(ids) ? ids : [])
      .map((id) => getPictureId(id))
      .filter((id) => id !== null);
    if (!wanted.length) return;

    // Character-likeness sort can't be positioned incrementally. The likeness
    // value is computed by a backend SQL function relative to the currently
    // selected similarity character (find_pictures_by_character_likeness_sql),
    // not stored on the picture, so it is NOT in the `fields=grid` projection
    // (the backend has no character context to compute it there). gridImageSortKey
    // therefore falls through to `score` and would splice the card at the wrong
    // position. Fall back to a full refetch, which DOES recompute likeness - or,
    // under an open overlay, defer it (the overlay-open deferral contract, §9.1)
    // so we never restructure the filmstrip mid-view.
    if (isCharacterLikenessSortActive()) {
      if (overlayOpen.value) {
        pendingOverlayGridRefresh.value = true;
        return;
      }
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
      return;
    }

    if (imagesLoading.value) {
      // A streaming fetch owns allGridImages wholesale; inserting now would be
      // clobbered. The caller (useGridRealtimeSync) routes these to the pill
      // instead while loading, so just bail.
      console.warn(
        "insertGridImagesById: skipped during in-flight grid fetch",
        wanted,
      );
      return;
    }

    const existing = new Set(
      (Array.isArray(lastFetchedGridImages.value)
        ? lastFetchedGridImages.value
        : []
      ).map((img) => getPictureId(img?.id)),
    );
    const toFetch = wanted.filter((id) => !existing.has(id));
    if (!toFetch.length) return;

    let fetched;
    try {
      const rows = await listPicturesByIds(toFetch, {
        fields: "grid",
      });
      fetched = Array.isArray(rows) ? rows : [];
    } catch (e) {
      console.error("insertGridImagesById: grid metadata fetch failed", {
        ids: toFetch,
        error: e,
      });
      return;
    }
    if (!fetched.length) return;

    const descending = gridSortDescending.value;
    const base = Array.isArray(lastFetchedGridImages.value)
      ? lastFetchedGridImages.value.slice()
      : [];
    const inserted = [];
    for (const pic of fetched) {
      const picId = getPictureId(pic?.id);
      if (
        picId === null ||
        base.some((img) => getPictureId(img?.id) === picId)
      ) {
        continue;
      }
      const key = gridImageSortKey(pic);
      let insertIndex = base.findIndex((img) => {
        const otherKey = gridImageSortKey(img);
        return descending ? otherKey < key : otherKey > key;
      });
      if (insertIndex === -1) insertIndex = base.length;
      base.splice(insertIndex, 0, pic);
      inserted.push(picId);
    }
    if (!inserted.length) return;

    lastFetchedGridImages.value = base;
    rebuildGridImagesFromLastFetch();
    if (highlight) triggerNewImageHighlight(inserted);

    // An in-app ComfyUI result lands here (origin-aware WS picture_imported insert).
    // If the overlay is open with a pending comfyui refresh, reconcile it now that
    // the new stacked output is present in lastFetchedGridImages - this is the i2i/
    // upscale lightbox case that previously relied on the full-grid refetch.
    void maybeRefreshOverlayForComfyui();
  }

  async function refreshSmartScoreForImage(imageId) {
    if (!imageId || !isSmartScoreSortActive()) return;
    const latestInfo = await fetchImageInfo(imageId, { smartScore: true });
    if (!latestInfo || Array.isArray(latestInfo)) return;

    const idx = allGridImages.value.findIndex((img) => img?.id === imageId);
    if (idx !== -1) {
      const current = allGridImages.value[idx] || {};
      const smartScore =
        typeof latestInfo.smartScore === "number"
          ? latestInfo.smartScore
          : null;
      if (current.smartScore === smartScore) {
        return;
      }
      await nextTick();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      // Pass the TRUE smart score (number or null). repositionImageBySmartScore
      // derives its ordering key from the null rule and preserves null as the
      // card's displayed value - collapsing to 0 here would both mis-order the
      // card and show a fake 0.
      repositionImageBySmartScore(imageId, smartScore, latestInfo);
    }
  }

  async function applyScoresByEntries(entries, options = {}) {
    const { updateSort = true, emitRefreshSidebar = true } = options;
    if (!Array.isArray(entries) || !entries.length) return;

    // Score updates are applied locally below (including score-sort reordering),
    // so the immediate WS gridVersion refresh would be redundant and can clear
    // current multi-selection in some paths. Skip that next WS refresh.
    skipNextWsRefresh.value = true;

    const scoresPayload = {};
    for (const [id, score] of entries) {
      scoresPayload[String(id)] = Number(score);
    }

    await applyScores(scoresPayload);

    const scoreMap = new Map(
      entries.map(([id, score]) => [String(id), Number(score)]),
    );

    let updatedImages = allGridImages.value.map((img) => {
      if (!img || img.id == null) return img;
      const key = String(img.id);
      if (!scoreMap.has(key)) return img;
      return { ...img, score: scoreMap.get(key) };
    });

    if (updateSort && isScoreSortActive()) {
      const descending = gridSortDescending.value;
      updatedImages = updatedImages
        .slice()
        .sort((a, b) => {
          const aScore = a?.score ?? 0;
          const bScore = b?.score ?? 0;
          if (aScore === bScore) {
            const aIdx = a?.idx ?? 0;
            const bIdx = b?.idx ?? 0;
            return aIdx - bIdx;
          }
          return descending ? bScore - aScore : aScore - bScore;
        })
        .map((img, idx) => (img ? { ...img, idx } : img));
      allGridImages.value = updatedImages;
      invalidateVisibleThumbnailRanges();
    } else {
      allGridImages.value = updatedImages;
    }

    if (updateSort && isCharacterLikenessSortActive()) {
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
    }

    if (updateSort && isSmartScoreSortActive()) {
      preserveScrollOnNextFetch.value = true;
      debouncedFetchAllGridImages();
    }

    // The unscored filter is the one view a score can throw a picture out of.
    // Drop it here rather than refetching: scoring straight through a backlog
    // is the workflow this filter exists for, and a refetch per keystroke would
    // be both janky and needless. A score of 0 is still unscored (the filter is
    // `score IS NULL OR score = 0`), so only 1-5 leaves.
    if (filterStore.unscoredOnlyFilter && removeImagesById) {
      const scoredIds = entries
        .filter(([, score]) => Number(score) > 0)
        .map(([id]) => id);
      if (scoredIds.length) removeImagesById(scoredIds);
    }

    if (emitRefreshSidebar) {
      emit("refresh-sidebar");
    }
  }

  async function applyScore(img, newScore) {
    const imageId = img?.id;
    if (!imageId) {
      noticeStore.error(
        "Couldn't set that score - the picture id is missing.",
        {
          key: "score-missing-id",
        },
      );
      return;
    }
    try {
      // Suppress the WS-driven gridVersion reload that the score apply triggers;
      // the score update is already applied locally by applyScoresByEntries.
      skipNextWsRefresh.value = true;
      await applyScoresByEntries([[String(imageId), newScore]], {
        updateSort: false,
        emitRefreshSidebar: false,
      });

      if (isScoreSortActive()) {
        if (overlayOpen.value) {
          // Reordering the grid while the overlay is open would break the
          // filmstrip. Defer the reposition until the overlay closes.
          pendingOverlayGridRefresh.value = true;
        } else {
          repositionImageByScore(imageId, newScore);
        }
      }
      if (isCharacterLikenessSortActive()) {
        if (overlayOpen.value) {
          pendingOverlayGridRefresh.value = true;
          return;
        }
        preserveScrollOnNextFetch.value = true;
        debouncedFetchAllGridImages();
        return;
      }
      if (isSmartScoreSortActive()) {
        if (overlayOpen.value) {
          pendingOverlayGridRefresh.value = true;
          return;
        }
        preserveScrollOnNextFetch.value = true;
        debouncedFetchAllGridImages();
        return;
      }
    } catch (e) {
      console.error("Failed to set score", e);
      noticeStore.error(`Couldn't set that score. ${errorDetail(e)}`, {
        key: "score-set",
      });
    }
  }

  async function applyScoresForSelection(imageIds, targetScore) {
    const ids = Array.isArray(imageIds) ? imageIds.filter(Boolean) : [];
    if (!ids.length) return;
    if (!Number.isFinite(targetScore)) return;

    const gridById = new Map(
      allGridImages.value
        .filter((img) => img && img.id != null)
        .map((img) => [String(img.id), img]),
    );

    const entries = [];
    for (const id of ids) {
      const key = String(id);
      const img = gridById.get(key);
      if (!img) continue;
      const current = Number(img.score || 0);
      const nextScore = toggleScore(current, targetScore);
      entries.push([key, nextScore]);
    }

    if (!entries.length) return;

    await applyScoresByEntries(entries, {
      updateSort: true,
      emitRefreshSidebar: true,
    });
  }

  // ============================================================

  return {
    setScore,
    setGuestScore,
    handleGuestConsentAccepted,
    handleGuestConsentRejected,
    fetchGuestScores,
    initGuestSession,
    isScoreSortActive,
    isCharacterLikenessSortActive,
    isSmartScoreSortActive,
    gridSortDescending,
    getGridSmartScoreValue,
    smartScoreSortKey,
    invalidateVisibleThumbnailRanges,
    repositionImageByScore,
    repositionImageBySmartScore,
    gridImageSortKey,
    insertGridImagesById,
    refreshSmartScoreForImage,
    applyScoresByEntries,
    applyScore,
    applyScoresForSelection,
  };
}
