import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import { getWorkflowCard, listWorkflowCards } from "../api/workflows";
import { isStack } from "../utils/workflowCard";
import { onSessionReset } from "../utils/apiClient";
import { errorMessage } from "../utils/apiError";

/**
 * The Workflows grid (v1.12 F1a) — the cards, the sort, and which stack is open.
 *
 * Separate from `useWorkflowShelfStore`, which owns the shipped topology list
 * on `/workflows`: the two read different routes and answer different
 * questions, and F1b replaces the shelf rather than merging the two.
 *
 * **One stack open at a time**, deliberately unlike the picture grid, where any
 * number of stacks can be expanded at once. A workflow stack's panel is a
 * full-width band that pushes every later row down, so two open panels put the
 * second one a screen and a half below the card that opened it; a picture
 * stack expands in place and has no such cost. See
 * `docs/frontend_architecture.md` §5.
 */

export const SORT_KEYS = ["rating", "used", "pictures"];

export const SORT_LABELS = {
  rating: { label: "Your ratings", icon: "mdi-star" },
  used: { label: "Recently used", icon: "mdi-history" },
  pictures: { label: "Picture count", icon: "mdi-image-multiple" },
};

/** The sort keys, each as `(card) => number`, descending. */
const SORT_VALUES = {
  // `rank`, not `rating`: it is the same stars smoothed towards the library's
  // mean, which is what makes a 5.0 from one picture rank below a 4.8 from
  // ninety instead of above it. `rating` is what the card SHOWS; ordering by
  // it puts every one-picture fluke at the top of the screen.
  rating: (card) => card.rank ?? 0,
  // A card whose pictures are all binned has no last use. It sorts BELOW
  // every dated card rather than being read as a date of its own.
  used: (card) => (card.last_used ? Date.parse(card.last_used) : -Infinity),
  pictures: (card) => card.picture_count ?? 0,
};

export const useWorkflowsStore = defineStore("workflows", () => {
  const cards = ref([]);
  const oneOffs = ref(0);
  const hidden = ref(0);
  const loading = ref(false);
  const loaded = ref(false);
  const error = ref("");

  const sortKey = ref("rating");

  /** The open stack's cover key, or null. At most one, ever. */
  const openStackKey = ref(null);
  /** `{coverKey: [cover, …members]}` — a stack's cards, once fetched. */
  const members = ref({});
  /** Keys whose member requests are in flight, so a second open is a no-op. */
  const inflight = ref(new Set());
  /** Keys whose member request came back empty-handed. A retry clears them. */
  const membersFailed = ref(new Set());

  /**
   * The OPEN stack's members are still coming.
   *
   * Read off `inflight` rather than kept as a boolean of its own: one flag
   * shared by every stack cleared on whichever request finished first, so
   * opening `b` and then `e` had `b`'s completion tell the panel that `e`'s
   * members "could not be read" while they were still on the wire.
   */
  const membersLoading = computed(() => inflight.value.has(openStackKey.value));

  // A stamp, not a boolean: an in-flight fetch that resolves after a session
  // reset must not write its rows, or its cover thumbnail URLs, into the new
  // session's store.
  let epoch = 0;

  /** Selected card keys; a member's key mixes freely with a top-level one. */
  const selectedKeys = ref([]);

  const sortedCards = computed(() => {
    const value = SORT_VALUES[sortKey.value] ?? SORT_VALUES.rating;
    // A copy: `cards` is the fetch order and a sort in place would make the
    // next resort depend on the last one.
    return [...cards.value].sort((a, b) => value(b) - value(a));
  });

  /**
   * The open stack's cards, cover first.
   *
   * **The cover alone until the members arrive**, never an empty list: the
   * grid already holds that card, so the panel can draw it and its header
   * immediately rather than leaving a blank band on screen for as many round
   * trips as the stack has members.
   */
  const openMembers = computed(() => {
    const key = openStackKey.value;
    if (!key) return [];
    const fetched = members.value[key];
    if (fetched) return fetched;
    const cover = cards.value.find((entry) => entry.key === key);
    return cover ? [cover] : [];
  });

  /** How many cards the open stack HAS, which is not how many have arrived. */
  const openStackSize = computed(() => {
    const key = openStackKey.value;
    if (!key) return 0;
    const cover = cards.value.find((entry) => entry.key === key);
    return cover ? Math.max(cover.stack_size ?? 1, 1) : 0;
  });

  async function fetchCards() {
    if (loading.value) return;
    loading.value = true;
    error.value = "";
    const mine = epoch;
    try {
      const body = await listWorkflowCards();
      if (mine !== epoch) return;
      cards.value = body.cards;
      oneOffs.value = body.one_offs;
      hidden.value = body.hidden;
      loaded.value = true;
    } catch (err) {
      console.warn("[workflows] could not read the cards", err);
      if (mine !== epoch) return;
      error.value = errorMessage(err, "Could not read the workflows.");
    } finally {
      if (mine === epoch) loading.value = false;
    }
  }

  /**
   * Open one stack, closing whichever was open.
   *
   * The grid lists the cover alone, so a member is only readable one card at a
   * time on the detail route. They are fetched once and kept: a panel reopened
   * after a resize must not re-request the same six cards.
   *
   * **Idempotent on the same key, including while the fetch is still running**
   * — a double-click on ▸ reaches here twice, and without `inflight` the
   * second call would re-issue every member request because nothing has landed
   * in `members` yet.
   *
   * A card that is not a stack has nothing to open, so it is refused here
   * rather than only at the call sites: a panel drawn for a lone card shows
   * that card twice, once flagged "Cover".
   */
  async function openStack(coverKey) {
    const card = cards.value.find((entry) => entry.key === coverKey);
    if (!card || !isStack(card)) return;
    openStackKey.value = coverKey;
    if (members.value[coverKey] || inflight.value.has(coverKey)) return;
    const mine = epoch;
    inflight.value = new Set(inflight.value).add(coverKey);
    // Cleared as the attempt starts, not as it ends: a retry must not read as
    // failed while it is in flight.
    if (membersFailed.value.has(coverKey)) {
      const cleared = new Set(membersFailed.value);
      cleared.delete(coverKey);
      membersFailed.value = cleared;
    }
    try {
      const fetched = await Promise.all(
        (card.member_keys ?? []).map((key) => getWorkflowCard(key)),
      );
      if (mine === epoch) {
        members.value = {
          ...members.value,
          [coverKey]: [card, ...fetched.map((body) => body.card)],
        };
      }
    } catch (err) {
      console.warn(
        `[workflows] could not read the members of ${coverKey}`,
        err,
      );
      if (mine !== epoch) return;
      error.value = errorMessage(err, "Could not read this stack.");
      // The failure is RECORDED, not written into `members` as an answer.
      // Writing the cover alone there satisfied the "already have them" guard
      // above, so one dropped request made the panel say "N could not be
      // read" for the rest of the session however often it was reopened.
      // `openMembers` still falls back to the cover, so the panel draws the
      // same thing — it just asks again next time.
      membersFailed.value = new Set(membersFailed.value).add(coverKey);
    } finally {
      if (mine === epoch) {
        const next = new Set(inflight.value);
        next.delete(coverKey);
        inflight.value = next;
      }
    }
  }

  function closeStack() {
    openStackKey.value = null;
  }

  /**
   * Forget the fetched stack members, keeping the selection and the cards.
   *
   * For the one gesture that invalidates them wholesale: marking a LoRA slot
   * re-keys every card of the topology, so the cards cached under a cover key
   * can name workflows the hub no longer has. `fetchCards` does not clear
   * them — it only re-reads the grid, which lists covers — so an open stack
   * would keep drawing its pre-flip members until the session reset.
   */
  function forgetMembers() {
    members.value = {};
    membersFailed.value = new Set();
  }

  function toggleStack(coverKey) {
    if (openStackKey.value === coverKey) closeStack();
    else openStack(coverKey);
  }

  /** Replace the selection, or toggle one key into it (Ctrl/Cmd). */
  function select(key, { additive = false } = {}) {
    if (!additive) {
      selectedKeys.value = [key];
      return;
    }
    selectedKeys.value = selectedKeys.value.includes(key)
      ? selectedKeys.value.filter((entry) => entry !== key)
      : [...selectedKeys.value, key];
  }

  /**
   * Select a run of keys (Shift). The caller hands the run because only it
   * knows the flat order, which interleaves a stack's members into the grid.
   */
  function selectRange(keys) {
    selectedKeys.value = [...keys];
  }

  function clearSelection() {
    selectedKeys.value = [];
  }

  function setSortKey(key) {
    if (SORT_KEYS.includes(key)) sortKey.value = key;
  }

  function reset() {
    epoch += 1;
    cards.value = [];
    oneOffs.value = 0;
    hidden.value = 0;
    loaded.value = false;
    // Cleared here too: the in-flight fetch's `finally` belongs to the old
    // epoch and will not clear it, and `fetchCards` refuses to start while it
    // is set — which would leave the new session looking at an empty grid.
    loading.value = false;
    error.value = "";
    members.value = {};
    inflight.value = new Set();
    membersFailed.value = new Set();
    openStackKey.value = null;
    selectedKeys.value = [];
  }

  onScopeDispose(onSessionReset(reset));

  return {
    cards,
    oneOffs,
    hidden,
    loading,
    loaded,
    error,
    sortKey,
    openStackKey,
    members,
    membersLoading,
    selectedKeys,
    sortedCards,
    openMembers,
    openStackSize,
    fetchCards,
    openStack,
    closeStack,
    forgetMembers,
    toggleStack,
    select,
    selectRange,
    clearSelection,
    setSortKey,
    reset,
  };
});
