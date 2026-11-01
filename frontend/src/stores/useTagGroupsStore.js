// Tag groups (#2469): the library-wide map from a tag to its group, and the
// two view choices that show it.
//
// * The map is the server's (`/tag_groups`); every write answers with the
//   whole map, which replaces this copy. Filing tags is one undoable
//   operation, so after a move the history control is re-read and offers it.
// * The rail's Tags | By group choice and the board's Health | Groups choice
//   are this machine's, like the grid's other view choices: localStorage,
//   read once, tolerant of a private window.

import { computed, ref } from "vue";
import { defineStore } from "pinia";

import {
  assignTagsToGroup,
  createTagGroup,
  getTagGroups,
  importTagGroups,
  reorderTagGroups,
} from "../api/tagGroups";
import { isReadOnly, onSessionReset } from "../utils/apiClient";
import { errorDetail } from "../utils/apiError";
import { useFilterStore } from "./useFilterStore";
import { useNoticeStore } from "./useNoticeStore";
import { useOperationStore } from "./useOperationStore";

/** Op type of a tag move, as the server records it. */
export const OP_TAG_GROUPS_MOVE = "tag_groups.move";
/** `Operation.target_type` of a tag move: no picture is its target. */
export const TARGET_TAG_GROUP = "tag_group";

/**
 * A group as a filter term in `tag` / `rejected_tag`: `__group:<id>` has (or
 * lacks) any tag filed there; `__group:none` any tag filed nowhere.
 */
export const GROUP_TOKEN_PREFIX = "__group:";
export const UNGROUPED_TOKEN = `${GROUP_TOKEN_PREFIX}none`;

/** @returns {string} the filter term for a group id, or null for Ungrouped. */
export function groupToken(groupId) {
  return groupId == null ? UNGROUPED_TOKEN : `${GROUP_TOKEN_PREFIX}${groupId}`;
}

// What the server reads as a group: `none` or ASCII digits. Anything else
// after the prefix (`__group:abc`) is a literal tag name there, so here too.
const GROUP_TOKEN_RE = /^__group:(?:none|[0-9]+)$/;

/** @returns {boolean} the filter value is a group term, not a tag. */
export function isGroupToken(value) {
  return GROUP_TOKEN_RE.test(String(value ?? ""));
}

const RAIL_VIEW_KEY = "pixlstash.railTags.view";
const COLLAPSED_KEY = "pixlstash.railTags.collapsed";
const BOARD_VIEW_KEY = "pixlstash.tagReview.view";
// A rail remounts per selection: the map is read at most this often.
const STALE_MS = 30_000;

function readPref(key, fallback) {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch (e) {
    console.warn(`Couldn't read the ${key} preference; using ${fallback}`, e);
    return fallback;
  }
}

function writePref(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (e) {
    console.warn(`Couldn't remember the ${key} preference`, e);
  }
}

function readCollapsed() {
  try {
    const list = JSON.parse(readPref(COLLAPSED_KEY, "[]"));
    return new Set(Array.isArray(list) ? list.map(String) : []);
  } catch (e) {
    console.warn("The folded tag groups preference was unreadable; none fold", e);
    return new Set();
  }
}

export const useTagGroupsStore = defineStore("tagGroups", () => {
  /** Groups in rail order: `{ id, name, position }`. */
  const groups = ref([]);
  /** Tag name -> group id. A tag missing here is Ungrouped. */
  const groupOf = ref(new Map());
  const loaded = ref(false);
  /**
   * Bumped whenever a move or its undo changed which tags a group holds, so a
   * view narrowed by a group term (the stats sidebar) knows to re-read.
   */
  const revision = ref(0);
  let fetchedAt = 0;
  let inflight = null;
  // Reads started, and the number of the one in flight: a forced read joins
  // only a read that started after it was asked for.
  let readsStarted = 0;
  let inflightNo = 0;
  // Bumped on a session reset. Every answer, error and rollback checks it: a
  // request made under the previous library or credential must not touch the
  // new one's map when it lands.
  let generation = 0;

  const hasGroups = computed(() => groups.value.length > 0);
  const byId = computed(() => new Map(groups.value.map((g) => [g.id, g])));

  function apply(body) {
    if (!body || !Array.isArray(body.groups)) return;
    groups.value = [...body.groups].sort(
      (a, b) => a.position - b.position || a.id - b.id,
    );
    groupOf.value = new Map(Object.entries(body.tags || {}));
    loaded.value = true;
    fetchedAt = Date.now();
  }

  /**
   * Read the map, unless it was read in the last 30 s. A read share session
   * cannot read it (owner only): it stays empty, and the rail stays a list.
   */
  async function fetch({ force = false } = {}) {
    if (!force) {
      if (fetchedAt && Date.now() - fetchedAt < STALE_MS) return;
      return inflight || startRead();
    }
    // Forced (after an undo): a read already in flight may have started
    // before the change committed, so wait it out and read again.
    const askedAfter = readsStarted;
    const started = generation;
    while (inflight && inflightNo <= askedAfter) {
      await inflight;
      if (started !== generation) return;
    }
    return inflight || startRead();
  }

  function startRead() {
    const started = generation;
    readsStarted += 1;
    inflightNo = readsStarted;
    const read = (async () => {
      try {
        const body = await getTagGroups();
        if (started !== generation) return;
        apply(body);
      } catch (e) {
        if (started !== generation) return;
        // A refusal (a share session) is not retried on every rail mount.
        fetchedAt = Date.now();
        if (!isReadOnly.value) {
          console.warn("Couldn't read the tag groups; tags show ungrouped", e);
        }
      }
    })().finally(() => {
      if (inflight === read) inflight = null;
    });
    inflight = read;
    return read;
  }

  /** @returns {Object|null} the group `tag` is filed under. */
  function groupFor(tag) {
    const id = groupOf.value.get(String(tag ?? ""));
    return id == null ? null : byId.value.get(id) || null;
  }

  /** @returns {string} a group's name, or "Ungrouped" for null. */
  function nameOf(groupId) {
    return groupId == null
      ? "Ungrouped"
      : byId.value.get(Number(groupId))?.name || "a group";
  }

  /**
   * A grid narrowed by a group term (`__group:3`) shows that group's pictures,
   * which a move or its undo just changed: hand the filter lists back as new
   * arrays so the grid re-reads. A grid with no group term is left alone.
   */
  function refilterGroupTerms() {
    revision.value += 1;
    const filters = useFilterStore();
    if ((filters.tagFilter || []).some(isGroupToken)) {
      filters.tagFilter = [...filters.tagFilter];
    }
    if ((filters.tagRejectedFilter || []).some(isGroupToken)) {
      filters.tagRejectedFilter = [...filters.tagRejectedFilter];
    }
  }

  function report(what, e) {
    console.error(`Couldn't ${what}`, e);
    useNoticeStore().push({
      level: "error",
      text: `Couldn't ${what}: ${errorDetail(e) || e?.message || e}`,
      key: "tag-groups",
    });
  }

  /**
   * Make a group, last in the order.
   * @returns {Promise<Object|null>} the new group, or null when it failed.
   */
  async function createGroup(name) {
    const clean = String(name ?? "").trim();
    if (!clean || isReadOnly.value) return null;
    const started = generation;
    try {
      const body = await createTagGroup(clean);
      if (started !== generation) return null;
      apply(body);
      return body?.group ?? groups.value.find((g) => g.name === clean) ?? null;
    } catch (e) {
      if (started !== generation) return null;
      report(`make the group “${clean}”`, e);
      return null;
    }
  }

  /**
   * File `tags` under `groupId` (null: Ungrouped) on every picture. One
   * undoable step; the history control is re-read so it offers the undo.
   * @returns {Promise<boolean>} whether it was filed.
   */
  async function moveTags(tags, groupId) {
    const names = [...new Set((tags || []).map(String).filter(Boolean))];
    if (!names.length || isReadOnly.value) return false;
    // Always sent, even when this copy says they are there already: it can be
    // 30 s old, and the server records nothing for a move that changes nothing.
    const target = groupId == null ? null : Number(groupId);
    const started = generation;
    try {
      const body = await assignTagsToGroup(names, target);
      if (started !== generation) return false;
      apply(body);
      refilterGroupTerms();
      await useOperationStore().refresh();
      return true;
    } catch (e) {
      if (started !== generation) return false;
      const what = names.length === 1 ? `“${names[0]}”` : `${names.length} tags`;
      report(`move ${what} to ${nameOf(target)}`, e);
      return false;
    }
  }

  /**
   * Parse a tag file without changing anything: the plan the import would
   * make, for the board to ask about before it commits.
   * @returns {Promise<Object|null>} the plan, or null when it failed.
   */
  async function previewImport(file) {
    if (isReadOnly.value) return null;
    const started = generation;
    try {
      const body = await importTagGroups(file, { dryRun: true });
      if (started !== generation) return null;
      return body?.plan ?? null;
    } catch (e) {
      if (started !== generation) return null;
      report(`read ${file?.name || "the tag file"}`, e);
      return null;
    }
  }

  /**
   * Apply the import: the file's filings win over the defaults, and tags the
   * user filed by hand move only when `overwriteUser`. One undoable operation;
   * the history control is re-read so it offers the undo.
   * @returns {Promise<Object|null>} the plan, or null when it failed.
   */
  async function commitImport(file, overwriteUser) {
    if (isReadOnly.value) return null;
    const started = generation;
    try {
      const body = await importTagGroups(file, {
        dryRun: false,
        overwriteUser: Boolean(overwriteUser),
      });
      if (started !== generation) return null;
      apply(body);
      refilterGroupTerms();
      await useOperationStore().refresh();
      return body?.plan ?? null;
    } catch (e) {
      if (started !== generation) return null;
      report(`import the tags from ${file?.name || "the tag file"}`, e);
      return null;
    }
  }

  /** Put the groups in this order. */
  async function reorder(ids) {
    if (isReadOnly.value) return false;
    const before = groups.value;
    const rank = new Map(ids.map((id, i) => [Number(id), i]));
    // Drawn in the new order at once; the server's answer replaces it.
    groups.value = [...before].sort(
      (a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0),
    );
    const started = generation;
    try {
      const body = await reorderTagGroups(ids.map(Number));
      if (started !== generation) return false;
      apply(body);
      return true;
    } catch (e) {
      if (started !== generation) return false;
      groups.value = before;
      report("reorder the tag groups", e);
      return false;
    }
  }

  // ── View choices, this machine's ──────────────────────────────────────────
  /** The rail's tab: `"list"` (Tags) or `"groups"` (By group). */
  const railView = ref(readPref(RAIL_VIEW_KEY, "list") === "groups" ? "groups" : "list");
  function setRailView(view) {
    railView.value = view === "groups" ? "groups" : "list";
    writePref(RAIL_VIEW_KEY, railView.value);
  }

  /** Folded groups in the rail, by id ("none" for Ungrouped). */
  const collapsed = ref(readCollapsed());
  const collapseKey = (groupId) => (groupId == null ? "none" : String(groupId));
  const isCollapsed = (groupId) => collapsed.value.has(collapseKey(groupId));
  function toggleCollapsed(groupId) {
    const next = new Set(collapsed.value);
    const key = collapseKey(groupId);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    collapsed.value = next;
    writePref(COLLAPSED_KEY, JSON.stringify([...next]));
  }

  /**
   * Tags to review's switch: `"groups"` or `"health"`. Groups is the default;
   * only a remembered Health reads back as Health. A read-only session is held
   * on Health by TagHealthBoard, not here.
   */
  const boardView = ref(readPref(BOARD_VIEW_KEY, "groups") === "health" ? "health" : "groups");
  function setBoardView(view) {
    boardView.value = view === "health" ? "health" : "groups";
    writePref(BOARD_VIEW_KEY, boardView.value);
  }

  // Another library is another map.
  onSessionReset(() => {
    groups.value = [];
    groupOf.value = new Map();
    loaded.value = false;
    fetchedAt = 0;
    generation += 1;
    inflight = null;
    revision.value += 1;
  });

  return {
    groups,
    groupOf,
    loaded,
    revision,
    hasGroups,
    byId,
    fetch,
    refilterGroupTerms,
    groupFor,
    nameOf,
    createGroup,
    moveTags,
    previewImport,
    commitImport,
    reorder,
    railView,
    setRailView,
    isCollapsed,
    toggleCollapsed,
    boardView,
    setBoardView,
  };
});
