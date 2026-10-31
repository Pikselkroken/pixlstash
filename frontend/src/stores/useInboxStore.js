// useInboxStore.js - the Inbox's period and how many pictures each one holds.
//
// The Inbox (sidebar › Review) is the grid narrowed to pictures added within a
// period that have no person and no set: `?inbox=<period>` on the listing,
// count and stats routes, which the backend reads as the unassigned view with a
// cutoff on `imported_at`. The period is a view setting the reader keeps, so it
// is remembered across visits; the counts are the sidebar row's number and the
// toolbar menu's per-row numbers, one `/pictures/count` each.

import { defineStore } from "pinia";
import { computed, onScopeDispose, ref } from "vue";
import { getPictureCount } from "../api/pictures";
import { isReadOnly, onSessionReset } from "../utils/apiClient";
import { useProjectStore } from "./useProjectStore";

// `days` dates the empty Inbox's "since"; `span` words the period in "from the
// <span> before". Any time has neither: it has no cutoff.
export const INBOX_PERIODS = [
  { id: "24h", label: "Last 24 hours", days: 1, span: "day" },
  { id: "7d", label: "Last 7 days", days: 7, span: "week" },
  { id: "14d", label: "Last 2 weeks", days: 14, span: "two weeks" },
  { id: "30d", label: "Last 30 days", days: 30, span: "month" },
  { id: "90d", label: "Last 3 months", days: 90, span: "three months" },
  { id: "all", label: "Any time", days: null, span: null },
];
export const DEFAULT_INBOX_PERIOD = "14d";

const STORAGE_KEY = "pixlstash:inboxPeriod";
const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * What an empty Inbox says, and the period it offers instead.
 *
 * The way out is the narrowest wider period that holds anything, so a narrow
 * period never hides an older pile of unfiled work; with no counts read yet,
 * or nothing anywhere, there is none.
 *
 * @param {string} periodId - the chosen period.
 * @param {Object<string, number>} counts - period id → count, as read so far.
 * @param {Date} [now]
 * @returns {{title: string, subtitle: string, wider: Object|null}}
 */
export function inboxEmptyState(periodId, counts, now = new Date()) {
  const at = INBOX_PERIODS.findIndex((p) => p.id === periodId);
  const info = INBOX_PERIODS[at] ?? INBOX_PERIODS[2];
  if (info.days == null) {
    return {
      title: "Nothing to file",
      subtitle: "Every picture has a person or a set.",
      wider: null,
    };
  }
  const wider =
    INBOX_PERIODS.slice(at + 1).find((p) => counts?.[p.id] > 0) ?? null;
  const since = new Date(now.getTime() - info.days * DAY_MS).toLocaleDateString(
    undefined,
    { weekday: "short", day: "numeric", month: "short" },
  );
  let subtitle = `Everything added since ${since} has a person or a set.`;
  if (wider) {
    const n = counts[wider.id];
    const pictures = `${n.toLocaleString()} ${n === 1 ? "picture" : "pictures"}`;
    const verb = n === 1 ? "has" : "have";
    subtitle += wider.span
      ? ` ${pictures} from the ${wider.span} before still ${verb} neither.`
      : ` ${pictures} added before then still ${verb} neither.`;
  }
  return {
    title: `Nothing new in the ${info.label.toLowerCase()}`,
    subtitle,
    wider,
  };
}

function isPeriod(id) {
  return INBOX_PERIODS.some((p) => p.id === id);
}

function loadPeriod() {
  try {
    const stored = window.localStorage?.getItem(STORAGE_KEY);
    return isPeriod(stored) ? stored : DEFAULT_INBOX_PERIOD;
  } catch (err) {
    console.warn(`[inbox] could not read ${STORAGE_KEY}; using the default`, err);
    return DEFAULT_INBOX_PERIOD;
  }
}

function savePeriod(id) {
  try {
    window.localStorage?.setItem(STORAGE_KEY, id);
  } catch (err) {
    console.warn(`[inbox] could not save ${STORAGE_KEY}; it lasts this session`, err);
  }
}

export const useInboxStore = defineStore("inbox", () => {
  const projectStore = useProjectStore();

  const period = ref(loadPeriod());
  // Period id → count, filled as each is read; a missing key is "not read yet".
  const counts = ref({});
  let epoch = 0;

  const periodInfo = computed(
    () => INBOX_PERIODS.find((p) => p.id === period.value) ?? INBOX_PERIODS[2],
  );
  /** The sidebar row's number: the chosen period's, once it has been read. */
  const count = computed(() => counts.value[period.value] ?? null);

  function setPeriod(id) {
    if (!isPeriod(id) || id === period.value) return;
    period.value = id;
    savePeriod(id);
    void refreshCount();
  }

  /**
   * What the Inbox holds for `id`, unfiltered: the grid's view without the
   * Filter menu, stack leaders as the tiles are, inside the sidebar's project.
   */
  function countQuery(id) {
    const params = new URLSearchParams({ inbox: id, stack_leaders_only: "true" });
    if (projectStore.projectViewMode === "project") {
      params.set(
        "project_id",
        projectStore.selectedProjectId != null
          ? String(projectStore.selectedProjectId)
          : "UNASSIGNED",
      );
    }
    return params.toString();
  }

  async function readCounts(ids) {
    if (isReadOnly.value) return;
    const requestEpoch = epoch;
    const results = await Promise.all(
      ids.map((id) =>
        getPictureCount(countQuery(id)).then(
          (body) => [id, body?.count ?? 0],
          (err) => {
            console.warn("Could not count the Inbox for", id, err);
            return null;
          },
        ),
      ),
    );
    if (requestEpoch !== epoch) return;
    const next = { ...counts.value };
    for (const result of results) if (result) next[result[0]] = result[1];
    counts.value = next;
  }

  /** The chosen period's count, for the sidebar row. */
  function refreshCount() {
    return readCounts([period.value]);
  }

  /** Every period's count: the menu's rows and the empty Inbox's way out. */
  function refreshAllCounts() {
    return readCounts(INBOX_PERIODS.map((p) => p.id));
  }

  function reset() {
    epoch += 1;
    counts.value = {};
  }

  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    period,
    periodInfo,
    counts,
    count,
    setPeriod,
    countQuery,
    refreshCount,
    refreshAllCounts,
    reset,
  };
});
