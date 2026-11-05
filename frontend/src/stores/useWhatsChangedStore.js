import { defineStore } from "pinia";
import { computed, nextTick, ref } from "vue";
import { patchUserConfig } from "../api/config";
import { isReadOnly } from "../utils/apiClient";
import { useEntityListsStore } from "./useEntityListsStore";
import { useNoticeStore } from "./useNoticeStore";
import { useSidebarStore } from "./useSidebarStore";

// The 2.0 "What's changed" checklist and the training tour card (#2145).
//
// Trigger: `last_seen_version` on the user's config. A library that holds
// pictures and has no stored version (or an older one) is an upgrade, and gets
// the checklist in the grid's inspector and the tour card at the foot of the
// sidebar. A new install stores the running version and shows nothing: a new
// user has nothing to unlearn. `whats_changed` holds the ticks and which of
// the two surfaces is still shown, so a half-done list survives a restart.

/** The release this content describes. A later release needs new content. */
export const RELEASE = "2.0";

/**
 * The gestures 2.0 moved, as things to do. Each id is ticked by the event the
 * app already sees for it (ImageGrid, Lightbox). Order is the list's order.
 */
export const STEP_IDS = ["click", "close", "wheel", "menu", "drop"];

/** The inspector band both panes draw while the checklist is up. */
export const INSPECTOR_BAND = [
  { value: "details", label: "Details" },
  {
    value: "changes",
    label: "What's changed",
    closeLabel: "Close What's changed",
  },
];

/** How long "That's all of them" stays before the tab sees itself out. */
export const ALL_DONE_MS = 4000;
const TOAST_MS = 8000;

/** `[major, minor]` of a version string ("2.0.0.dev.3" → [2, 0]), or null. */
export function releaseOf(version) {
  const m = /^(\d+)\.(\d+)/.exec(String(version ?? ""));
  return m ? [Number(m[1]), Number(m[2])] : null;
}

function isBefore(a, b) {
  return a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);
}

export const useWhatsChangedStore = defineStore("whatsChanged", () => {
  const notices = useNoticeStore();
  const entityLists = useEntityListsStore();
  const sidebar = useSidebarStore();

  const loaded = ref(false);
  const lastSeenVersion = ref(null);
  const done = ref([]);
  const checklist = ref(false);
  const card = ref(false);

  // Session-only UI state.
  /** The inspector's band: "changes" (the checklist) or "details". */
  const railTab = ref("changes");
  /** The step whose explanation is open: the last one ticked. */
  const openStep = ref(null);
  /** Every step ticked: the tab says so, then sees itself out. */
  const allDone = ref(false);
  /** Bumped by `reopen`; App.vue brings the grid up when elsewhere. */
  const reopened = ref(0);
  /** 0 when the tour is off, else its step, 1-4. */
  const tourStep = ref(0);
  let evaluated = false;
  let allDoneTimer = null;

  const checklistShown = computed(() => checklist.value && !isReadOnly.value);
  const railShown = computed(
    () => checklistShown.value && railTab.value === "changes",
  );

  /**
   * Who the tour trains: the person with the most pictures, else the largest
   * set. With neither there is nothing to point at, and no card.
   */
  const tourSubject = computed(() => {
    const top = (rows, countKey, type) => {
      let best = null;
      for (const row of rows ?? []) {
        const count = Number(row?.[countKey] ?? 0);
        if (row?.id == null || count <= 0) continue;
        if (!best || count > best.count) {
          best = { type, id: row.id, name: row.name || "", count };
        }
      }
      return best;
    };
    return (
      top(entityLists.characters, "image_count", "character") ??
      top(entityLists.pictureSets, "picture_count", "set")
    );
  });
  const cardShown = computed(
    () => card.value && !isReadOnly.value && !!tourSubject.value,
  );

  function snapshot() {
    return {
      done: [...done.value],
      checklist: checklist.value,
      card: card.value,
    };
  }

  async function save(patch) {
    if (isReadOnly.value) return;
    try {
      await patchUserConfig(patch);
    } catch (e) {
      // The state stays in memory for this session; only a restart loses it.
      console.error("Failed to save the What's changed state:", patch, e);
    }
  }

  function saveState() {
    return save({ whats_changed: snapshot() });
  }

  /** Read the stored state from GET /users/me/config. */
  function hydrate(cfg) {
    lastSeenVersion.value =
      typeof cfg?.last_seen_version === "string" ? cfg.last_seen_version : null;
    const state = cfg?.whats_changed;
    if (state && typeof state === "object") {
      done.value = Array.isArray(state.done)
        ? state.done.filter((id) => STEP_IDS.includes(id))
        : [];
      checklist.value = state.checklist === true;
      card.value = state.card === true;
    } else {
      // Nothing stored: nothing to show, whatever an earlier read held.
      done.value = [];
      checklist.value = false;
      card.value = false;
    }
    loaded.value = true;
  }

  /**
   * Decide, once per session, whether this launch is the upgrade. Needs the
   * config (`hydrate`), the running version and whether the library is empty.
   * True only on the call that found the upgrade.
   */
  async function evaluate({ version, libraryEmpty }) {
    if (evaluated || !loaded.value || isReadOnly.value) return;
    const running = releaseOf(version);
    const release = releaseOf(RELEASE);
    if (!running) return;
    evaluated = true;
    if (isBefore(running, release)) return;
    const seen = releaseOf(lastSeenVersion.value);
    if (seen && !isBefore(seen, release)) return;
    const upgrade = seen !== null || !libraryEmpty;
    lastSeenVersion.value = version;
    if (!upgrade) {
      await save({ last_seen_version: version });
      return false;
    }
    done.value = [];
    checklist.value = true;
    card.value = true;
    showRails();
    await save({ last_seen_version: version, whats_changed: snapshot() });
    return true;
  }

  /**
   * Put the checklist and the card on screen: the inspector open on What's
   * changed, and the left sidebar pinned and expanded (docked, it is a rail
   * of icons with no room for the card). Kept as the reader's layout after.
   */
  function showRails() {
    railTab.value = "changes";
    if (!sidebar.statsOpen) sidebar.toggleStats();
    if (!sidebar.sidebarPinned) sidebar.setSidebarPinned(true);
    if (sidebar.sidebarDocked) sidebar.setSidebarDocked(false);
  }

  /** A gesture was done for real. Ticks its step if the checklist is up. */
  function tick(id) {
    if (!checklistShown.value || done.value.includes(id)) return;
    if (!STEP_IDS.includes(id)) return;
    done.value = [...done.value, id];
    openStep.value = id;
    saveState();
    if (done.value.length === STEP_IDS.length) {
      allDone.value = true;
      clearTimeout(allDoneTimer);
      allDoneTimer = setTimeout(() => {
        allDone.value = false;
        if (checklist.value) dismissChecklist();
      }, ALL_DONE_MS);
    }
  }

  function dismissChecklist() {
    clearTimeout(allDoneTimer);
    allDone.value = false;
    checklist.value = false;
    railTab.value = "details";
    saveState();
    notices.push({
      text: "Fine, I'm going. It's in Settings, beside the version, if you miss me.",
      key: "whats-changed-dismissed",
      timeout: TOAST_MS,
      action: {
        label: "Undo",
        handler: () => {
          checklist.value = true;
          railTab.value = "changes";
          saveState();
          notices.push({
            text: "Knew you'd be back.",
            key: "whats-changed-back",
          });
        },
      },
    });
  }

  /**
   * Get outta here, or the band's ×: dismiss, and land on Details. The band
   * goes with the checklist, so focus would otherwise drop to `body`.
   */
  function leave() {
    dismissChecklist();
    nextTick(() =>
      document
        .querySelector(
          ".stats-sidebar .inspector-tab--active, .stats-sidebar .rail-clear",
        )
        ?.focus(),
    );
  }

  function dismissCard() {
    card.value = false;
    tourStep.value = 0;
    saveState();
    notices.push({
      text: "Suit yourself. It's in Settings, beside the version, if you change your mind.",
      key: "whats-changed-card-dismissed",
      timeout: TOAST_MS,
      action: {
        label: "Undo",
        handler: () => {
          card.value = true;
          saveState();
        },
      },
    });
  }

  /**
   * Switch the inspector between Details and the checklist. They are two
   * panes, so the switch remounts the rail: focus goes to the tab picked
   * rather than dropping to `body`.
   */
  function showTab(value) {
    railTab.value = value;
    nextTick(() =>
      document
        .querySelector(`.inspector-band [data-band="${value}"]`)
        ?.focus(),
    );
  }

  /** Settings' version and What's new pill: bring back whatever was dismissed. */
  function reopen() {
    if (isReadOnly.value) return;
    // A pending "That's all of them" exit must not take back what was asked for.
    clearTimeout(allDoneTimer);
    allDone.value = false;
    checklist.value = true;
    card.value = true;
    showRails();
    reopened.value += 1;
    saveState();
  }

  /** The tour's last step was done: the card has served its purpose. */
  function finishTour() {
    tourStep.value = 0;
    card.value = false;
    saveState();
  }

  return {
    loaded,
    lastSeenVersion,
    done,
    checklist,
    card,
    railTab,
    openStep,
    allDone,
    tourStep,
    reopened,
    checklistShown,
    railShown,
    tourSubject,
    cardShown,
    hydrate,
    evaluate,
    tick,
    dismissChecklist,
    leave,
    dismissCard,
    showTab,
    reopen,
    finishTour,
  };
});
