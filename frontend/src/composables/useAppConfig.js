import { reactive, ref, watch } from "vue";
import { isReadOnly } from "../utils/apiClient";
import { getUserConfig, patchUserConfig } from "../api/config";
import {
  clampSizeLevel,
  nearestSizeLevelForColumns,
} from "../utils/thumbnailSizes";
import { useUserPrefsStore } from "../stores/useUserPrefsStore";
import { useGridStore } from "../stores/useGridStore";
import { useSortStore } from "../stores/useSortStore";
import { useFilterStore } from "../stores/useFilterStore";
import { useSidebarStore } from "../stores/useSidebarStore";

/**
 * The user's server-side config: load it once on start, and write UI options
 * back whenever one of them changes.
 *
 * `configApplying` is what keeps the two halves from fighting: fetchConfig sets
 * it while it pushes the loaded values into the stores, so the persistence
 * watchers those writes trigger see it and stay quiet instead of PATCHing the
 * server with what it just told us.
 *
 * Read-only sessions never call the endpoint at all; they take the demo
 * defaults and nothing is ever persisted.
 *
 * @param {object} hooks
 * @param {Function} hooks.onThumbnailSizeChanged - re-measure the column
 *   ceiling after a thumbnail-size change (a layout concern App.vue owns).
 * @param {Function} hooks.onTelemetryConsentRequired - the user has never
 *   answered the telemetry question, so App.vue should ask.
 */
/**
 * Hand the question back to the desktop startup screen rather than opening a
 * dialog over a library that is already on screen. True when the shell took it,
 * in which case this window is on its way to the startup screen and the answer
 * arrives parked on the next load.
 */
async function askStartupScreenInstead() {
  const desktop =
    typeof window !== "undefined" ? window.pixlstashDesktop : null;
  if (!desktop?.askStartupQuestion) return false;
  try {
    return Boolean(await desktop.askStartupQuestion("privacy"));
  } catch (e) {
    console.error("Failed to hand the privacy question to the shell:", e);
    return false;
  }
}

/**
 * The privacy answer the desktop startup screen collected, handed over once.
 * Null in a browser, and null on desktop when the question was never asked
 * there (an older shell, or a launch that skipped the step).
 */
async function takeParkedTelemetryAnswer() {
  const desktop =
    typeof window !== "undefined" ? window.pixlstashDesktop : null;
  if (!desktop?.takePendingTelemetry) return null;
  try {
    return (await desktop.takePendingTelemetry()) || null;
  } catch (e) {
    console.error("Failed to read the startup screen's privacy answer:", e);
    return null;
  }
}

export function useAppConfig({
  onThumbnailSizeChanged,
  onTelemetryConsentRequired,
} = {}) {
  const userPrefsStore = useUserPrefsStore();
  const gridStore = useGridStore();
  const sortStore = useSortStore();
  const filterStore = useFilterStore();
  const sidebarStore = useSidebarStore();

  // The last config the server gave us, and the shape the PATCH is built
  // against. Kept here so nothing outside this composable can drift from it.
  const configSnapshot = ref({});
  const config = reactive({
    sort: "",
    thumbnail: 256,
    thumbnail_mode: "square",
    sidebar_thumbnail_size: 64,
    show_stars: true,
    show_face_bboxes: false,
    show_problem_icon: true,
    expand_all_stacks: true,
    date_format: "locale",
    theme_mode: "dark",
    stack_strictness: 0.92,
  });

  const configLoaded = ref(false);
  const configLoading = ref(false);
  const configApplying = ref(false);

  async function fetchConfig() {
    if (isReadOnly.value) {
      userPrefsStore.themeMode = "dark";
      userPrefsStore.sidebarThumbnailSize = 32;
      gridStore.showProblemIcon = true;
      gridStore.showFaceBboxes = false;
      gridStore.showStars = true;
      return;
    }
    if (configLoading.value) return;
    configLoading.value = true;
    configApplying.value = true;
    try {
      const cfg = await getUserConfig();
      const sortValue = cfg.sort_order ?? cfg.sort;
      if (typeof sortValue === "string" && sortValue) {
        sortStore.selectedSort = sortValue;
      }
      if (typeof cfg.show_keyboard_hint === "boolean")
        userPrefsStore.showKeyboardHint = cfg.show_keyboard_hint;
      if (typeof cfg.show_face_bboxes === "boolean") {
        gridStore.showFaceBboxes = cfg.show_face_bboxes;
      }
      if (typeof cfg.show_problem_icon === "boolean") {
        gridStore.showProblemIcon = cfg.show_problem_icon;
      }
      if (typeof cfg.expand_all_stacks === "boolean") {
        gridStore.showStacks = cfg.expand_all_stacks;
      } else if (typeof cfg.show_stacks === "boolean") {
        gridStore.showStacks = cfg.show_stacks;
      }
      if (typeof cfg.compact_mode === "boolean") {
        gridStore.compactMode = cfg.compact_mode;
      }
      if (typeof cfg.sidebar_docked === "boolean") {
        sidebarStore.setSidebarDocked(cfg.sidebar_docked);
      }
      if (typeof cfg.sidebar_pinned === "boolean") {
        sidebarStore.setSidebarPinned(cfg.sidebar_pinned);
      }
      if (typeof cfg.date_format === "string" && cfg.date_format) {
        userPrefsStore.dateFormat = cfg.date_format;
      }
      if (typeof cfg.theme_mode === "string" && cfg.theme_mode) {
        userPrefsStore.themeMode = cfg.theme_mode;
      }
      if (typeof cfg.descending === "boolean") {
        sortStore.selectedDescending = cfg.descending;
      }
      if (typeof cfg.thumbnail_size_level === "number") {
        gridStore.sizeLevel = clampSizeLevel(cfg.thumbnail_size_level);
      } else if (typeof cfg.columns === "number") {
        // Legacy config predating the size ladder: derive the nearest level so
        // an old install keeps roughly the same tile size after upgrading.
        gridStore.sizeLevel = nearestSizeLevelForColumns(cfg.columns);
      }
      if (
        cfg.thumbnail_mode === "square" ||
        cfg.thumbnail_mode === "justified"
      ) {
        gridStore.thumbnailMode = cfg.thumbnail_mode;
      }
      if (typeof cfg.sidebar_thumbnail_size === "number") {
        userPrefsStore.sidebarThumbnailSize = cfg.sidebar_thumbnail_size;
      }
      if (typeof cfg.sidebar_width === "number") {
        userPrefsStore.sidebarWidth = cfg.sidebar_width;
      }
      if (cfg.stack_strictness != null) {
        sortStore.stackThreshold = String(cfg.stack_strictness);
      }
      config.sort_order = sortValue || sortStore.selectedSort;
      config.descending = sortStore.selectedDescending;
      config.columns = gridStore.columns;
      config.thumbnail_mode = gridStore.thumbnailMode;
      config.sidebar_thumbnail_size = userPrefsStore.sidebarThumbnailSize;
      config.sidebar_width = userPrefsStore.sidebarWidth;
      config.show_stars =
        typeof cfg.show_stars === "boolean"
          ? cfg.show_stars
          : gridStore.showStars;
      config.show_face_bboxes =
        typeof cfg.show_face_bboxes === "boolean"
          ? cfg.show_face_bboxes
          : gridStore.showFaceBboxes;
      config.show_problem_icon =
        typeof cfg.show_problem_icon === "boolean"
          ? cfg.show_problem_icon
          : gridStore.showProblemIcon;
      config.expand_all_stacks =
        typeof cfg.expand_all_stacks === "boolean"
          ? cfg.expand_all_stacks
          : typeof cfg.show_stacks === "boolean"
            ? cfg.show_stacks
            : gridStore.showStacks;
      config.compact_mode =
        typeof cfg.compact_mode === "boolean"
          ? cfg.compact_mode
          : gridStore.compactMode;
      config.sidebar_docked =
        typeof cfg.sidebar_docked === "boolean"
          ? cfg.sidebar_docked
          : sidebarStore.sidebarDocked;
      config.sidebar_pinned =
        typeof cfg.sidebar_pinned === "boolean"
          ? cfg.sidebar_pinned
          : sidebarStore.sidebarPinned;
      config.date_format = userPrefsStore.dateFormat;
      config.theme_mode = userPrefsStore.themeMode;
      config.stack_strictness =
        cfg.stack_strictness != null
          ? cfg.stack_strictness
          : config.stack_strictness;
      const similarityValue =
        cfg.similarity_character ?? cfg.selected_similarity_character;
      sortStore.selectedSimilarityCharacter =
        similarityValue ?? sortStore.selectedSimilarityCharacter ?? null;
      const newHiddenTags = Array.isArray(cfg.hidden_tags)
        ? cfg.hidden_tags
        : [];
      if (
        userPrefsStore.hiddenTags.length !== newHiddenTags.length ||
        userPrefsStore.hiddenTags.some((tag, i) => tag !== newHiddenTags[i])
      ) {
        userPrefsStore.hiddenTags = newHiddenTags;
      }
      userPrefsStore.applyTagFilter = Boolean(cfg.apply_tag_filter);
      const rawPt = cfg.smart_score_penalised_tags;
      if (rawPt && typeof rawPt === "object" && !Array.isArray(rawPt)) {
        userPrefsStore.penalisedTagWeights = Object.fromEntries(
          Object.entries(rawPt).map(([k, v]) => [
            String(k).trim().toLowerCase(),
            Number(v) || 0,
          ]),
        );
      } else if (Array.isArray(rawPt)) {
        userPrefsStore.penalisedTagWeights = Object.fromEntries(
          rawPt.map((t) => [
            String(t || "")
              .trim()
              .toLowerCase(),
            3,
          ]),
        );
      } else {
        userPrefsStore.penalisedTagWeights = {};
      }
      config.selectedSimilarityCharacter =
        sortStore.selectedSimilarityCharacter;
      configSnapshot.value = {
        sort: sortStore.selectedSort || "",
        descending: sortStore.selectedDescending,
        columns:
          typeof gridStore.columns === "number" ? gridStore.columns : null,
        sidebar_thumbnail_size:
          typeof userPrefsStore.sidebarThumbnailSize === "number"
            ? userPrefsStore.sidebarThumbnailSize
            : null,
        show_keyboard_hint: userPrefsStore.showKeyboardHint,
        show_face_bboxes: gridStore.showFaceBboxes,
        show_problem_icon: gridStore.showProblemIcon,
        expand_all_stacks: gridStore.showStacks,
        compact_mode: gridStore.compactMode,
        sidebar_docked: sidebarStore.sidebarDocked,
        sidebar_pinned: sidebarStore.sidebarPinned,
        date_format: userPrefsStore.dateFormat,
        theme_mode: userPrefsStore.themeMode,
        similarity_character: sortStore.selectedSimilarityCharacter,
        stack_strictness:
          cfg.stack_strictness != null ? Number(cfg.stack_strictness) : null,
        hidden_tags: userPrefsStore.hiddenTags,
        apply_tag_filter: userPrefsStore.applyTagFilter,
      };
      filterStore.comfyuiConfigured = Boolean(cfg?.comfyui_url);
      if (typeof cfg?.public_url === "string" && cfg.public_url) {
        userPrefsStore.publicUrl = cfg.public_url;
      }
      userPrefsStore.embedWatermark = Boolean(cfg?.embed_watermark);
      userPrefsStore.hidePurgeSnapshotWarning = Boolean(
        cfg?.hide_purge_snapshot_warning,
      );
      const cfu = cfg?.check_for_updates;
      userPrefsStore.checkForUpdates =
        cfu === true ? true : cfu === false ? false : null;
      userPrefsStore.hydrateTelemetry(cfg);
      if (!userPrefsStore.telemetryConsentPrompted) {
        // On desktop the question is asked by the startup screen, before the
        // app loads, and the answer waits for us here. Applying it is what
        // stops the dialog asking a second time; only a desktop launch that
        // somehow has no parked answer falls through to asking in-app.
        const parked = await takeParkedTelemetryAnswer();
        if (parked) await userPrefsStore.saveTelemetry(parked);
        else if (!(await askStartupScreenInstead()))
          await onTelemetryConsentRequired?.({
            isUpgrade: userPrefsStore.checkForUpdates !== null,
          });
      }
    } catch (e) {
      console.error("Failed to fetch user config:", e);
    } finally {
      configApplying.value = false;
      configLoading.value = false;
      configLoaded.value = true;
    }
  }

  async function patchConfigUIOptions() {
    if (!configLoaded.value || configLoading.value || configApplying.value)
      return;
    const patch = {};
    if (sortStore.selectedSort) patch.sort = sortStore.selectedSort;
    patch.descending = sortStore.selectedDescending;
    patch.thumbnail_size_level = gridStore.sizeLevel;
    // Keep the legacy `columns` field in sync with the derived count so older
    // clients (and anything still reading `columns`) stay consistent.
    if (gridStore.columns) patch.columns = gridStore.columns;
    if (userPrefsStore.sidebarThumbnailSize) {
      patch.sidebar_thumbnail_size = userPrefsStore.sidebarThumbnailSize;
    }
    if (userPrefsStore.sidebarWidth) {
      patch.sidebar_width = userPrefsStore.sidebarWidth;
    }
    if (typeof userPrefsStore.showKeyboardHint === "boolean")
      patch.show_keyboard_hint = userPrefsStore.showKeyboardHint;
    if (typeof gridStore.showFaceBboxes === "boolean") {
      patch.show_face_bboxes = gridStore.showFaceBboxes;
    }
    if (typeof gridStore.showProblemIcon === "boolean") {
      patch.show_problem_icon = gridStore.showProblemIcon;
    }
    if (typeof gridStore.showStacks === "boolean") {
      patch.expand_all_stacks = gridStore.showStacks;
    }
    if (typeof gridStore.compactMode === "boolean") {
      patch.compact_mode = gridStore.compactMode;
    }
    if (
      gridStore.thumbnailMode === "square" ||
      gridStore.thumbnailMode === "justified"
    ) {
      patch.thumbnail_mode = gridStore.thumbnailMode;
    }
    if (typeof sidebarStore.sidebarDocked === "boolean") {
      patch.sidebar_docked = sidebarStore.sidebarDocked;
    }
    if (typeof sidebarStore.sidebarPinned === "boolean") {
      patch.sidebar_pinned = sidebarStore.sidebarPinned;
    }
    if (
      typeof userPrefsStore.dateFormat === "string" &&
      userPrefsStore.dateFormat
    ) {
      patch.date_format = userPrefsStore.dateFormat;
    }
    if (
      typeof userPrefsStore.themeMode === "string" &&
      userPrefsStore.themeMode
    ) {
      patch.theme_mode = userPrefsStore.themeMode;
    }
    if (sortStore.selectedSimilarityCharacter != null) {
      patch.similarity_character = sortStore.selectedSimilarityCharacter;
    }
    if (sortStore.stackThreshold != null && sortStore.stackThreshold !== "") {
      const parsed = parseFloat(String(sortStore.stackThreshold));
      if (Number.isFinite(parsed)) {
        patch.stack_strictness = parsed;
      }
    }

    const snapshot = configSnapshot.value || {};
    const changed = Object.fromEntries(
      Object.entries(patch).filter(([key, value]) => snapshot[key] !== value),
    );
    if (Object.keys(changed).length === 0) {
      return;
    }

    try {
      await patchUserConfig(changed);
      configSnapshot.value = { ...snapshot, ...changed };
    } catch (e) {
      console.error("Error patching user config:", e);
    }
  }

  // Persist a UI option whenever it changes. Every one of these is gated on
  // configLoaded so the initial store writes from fetchConfig do not echo
  // straight back to the server.
  function persist() {
    if (!configLoaded.value) return;
    patchConfigUIOptions();
  }

  watch(
    () => gridStore.thumbnailSize,
    () => {
      patchConfigUIOptions();
      onThumbnailSizeChanged?.();
    },
  );
  watch(
    [() => sortStore.selectedSort, () => sortStore.selectedDescending],
    () => {
      patchConfigUIOptions();
      // No refreshGridVersion() here: the grid's own selection watch already
      // fires on a sort change, so bumping the version would cost a second
      // redundant fetch.
    },
  );
  watch(() => userPrefsStore.themeMode, persist);
  watch(() => userPrefsStore.showKeyboardHint, persist);
  watch(
    [
      () => gridStore.showFaceBboxes,
      () => gridStore.showProblemIcon,
      () => gridStore.showStacks,
      () => gridStore.compactMode,
    ],
    () => patchConfigUIOptions(),
  );
  watch(
    () => sortStore.selectedSimilarityCharacter,
    () => patchConfigUIOptions(),
  );
  watch(() => sortStore.stackThreshold, persist);
  watch(() => gridStore.sizeLevel, persist);
  watch(() => gridStore.thumbnailMode, persist);
  watch(() => sidebarStore.sidebarDocked, persist);
  watch(() => sidebarStore.sidebarPinned, persist);
  watch(() => userPrefsStore.sidebarThumbnailSize, persist);
  watch(() => userPrefsStore.sidebarWidth, persist);
  watch(
    () => userPrefsStore.dateFormat,
    () => {
      if (!configLoaded.value) return;
      patchConfigUIOptions();
      // The grid renders dates per row, so a format change has to repaint it.
      gridStore.refreshGridVersion();
    },
  );

  return {
    configLoaded,
    configLoading,
    configApplying,
    fetchConfig,
    patchConfigUIOptions,
  };
}
