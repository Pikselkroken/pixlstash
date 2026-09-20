import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import {
  getWorkflowCard,
  listWorkflowCards,
  patchWorkflowCard,
  reorderStack,
  unstackWorkflow,
} from "../api/workflows";
import { isStack } from "../utils/workflowCard";
import { onSessionReset } from "../utils/apiClient";
import { errorMessage } from "../utils/apiError";

/**
 * The Workflows grid (v1.12 F1a) — the cards, the sort, and which stack is open.
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

  /**
   * The open stack's id, for the routes addressed by one.
   *
   * Read off the COVER card rather than kept alongside `openStackKey`: a
   * reorder that changes the cover re-fetches the grid, and a copy taken when
   * the panel opened would then address the stack by an id the new payload no
   * longer agrees with. Null while the cover has not arrived, which is what
   * `canReorder` below is for.
   */
  const openStackId = computed(() => {
    const key = openStackKey.value;
    if (!key) return null;
    return cards.value.find((entry) => entry.key === key)?.stack_id ?? null;
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
   *
   * **The open stack is collapsed, not re-read.** Left open with no members,
   * `openMembers` falls back to the cover alone and `StackPanel` reports the
   * difference as "N could not be read" — a failure message for a deliberate
   * invalidation, with no way out but collapsing it by hand. Worse when the
   * flip re-keyed the cover out of the grid: `openMembers` is then empty, the
   * panel is not drawn at all, and the row keeps `aria-expanded="true"`.
   * Re-expanding fetches fresh members through the path that already exists.
   *
   * **The epoch is bumped for the reason `reset` bumps it**: `openStack`'s
   * completion path guards only on `mine === epoch`, so a member read that
   * was on the wire writes the PRE-flip members straight back into the map
   * this function just emptied. Clearing `inflight` alone would not stop it,
   * and would let a second open re-issue reads the first is still making.
   * `loading` goes with it because the old epoch's `finally` will not clear
   * it — and every caller here follows this with `fetchCards()`, which
   * early-returns to nothing while `loading` is set.
   */
  function forgetMembers() {
    epoch += 1;
    loading.value = false;
    members.value = {};
    inflight.value = new Set();
    membersFailed.value = new Set();
    closeStack();
  }

  /**
   * Forget what was read and read the grid again, after something OUTSIDE this
   * view changed what the cards say.
   *
   * The one caller is a ghost purge in Settings › Privacy: forgetting a model
   * name or a picture ghost changes what a card names and how many pictures it
   * counts, and a grid left alone would go on showing what was just purged.
   * The cached stack members go with it for `forgetMembers`' own reason.
   *
   * **Only re-read when the grid has been read**, so a purge made by somebody
   * who has never opened Workflows fires no request.
   */
  function invalidate() {
    forgetMembers();
    if (loaded.value) fetchCards();
  }

  // ── Writing to a stack (v1.12 F2) ─────────────────────────────────────
  //
  // Every write here re-reads the grid rather than patching the store by hand.
  // A reorder moves the cover, and the cover is what the grid draws, what the
  // panel is keyed on and what `differs_by` is computed against — three things
  // the server derives and no client can re-derive from the response, which
  // only names the new order.

  /**
   * Re-read the grid after a write, and re-open the panel on `coverKey`.
   *
   * `forgetMembers` first, not a hand-cleared `members`: the cached members
   * are keyed on the cover, and after a reorder that key names a different
   * card — and the epoch bump it carries is what stops a member read still on
   * the wire writing the PRE-write members back into the map.
   */
  async function refreshAfterWrite(coverKey) {
    forgetMembers();
    await fetchCards();
    if (!coverKey) return;
    // Re-read rather than trusted: a card that no longer stacks — the last
    // unstack of a pair, or a hidden cover — must not leave the panel open
    // over a stack that is not there any more. `openStack` refuses one
    // anyway; this keeps the panel closed rather than flickering.
    const cover = cards.value.find((entry) => entry.key === coverKey);
    if (cover && isStack(cover)) await openStack(coverKey);
  }

  /**
   * Set the open stack's member order. `keys[0]` becomes the cover.
   *
   * The whole list, always: the route refuses a partial one, deliberately —
   * a key left out would leave the stack with no record that it had gone. The
   * server serves no `stack_id` at all for a stack the grid drew only part
   * of, exactly so this cannot be attempted and refused.
   *
   * **Answers whether the order actually changed**, because the callers
   * announce it: a refusal and a stack with no id both land here, and
   * announcing "position 2 of 6" over either tells a screen-reader user a row
   * moved when it did not.
   */
  async function reorderMembers(keys) {
    const stackId = openStackId.value;
    if (!stackId || keys.length < 2) return false;
    try {
      await reorderStack(stackId, keys);
      await refreshAfterWrite(keys[0]);
      return true;
    } catch (err) {
      console.warn(`[workflows] could not reorder ${stackId}`, err);
      error.value = errorMessage(err, "Could not reorder this stack.");
      return false;
    }
  }

  /**
   * Move one member `delta` places. Position 0 is the cover.
   *
   * A no-op at either end rather than a wrap: Alt+Up on the cover is a reader
   * finding the top of the list, not asking for the cover to become the last
   * member.
   */
  function moveMember(key, delta) {
    const keys = openMembers.value.map((card) => card.key);
    const at = keys.indexOf(key);
    const to = at + delta;
    if (at < 0 || to < 0 || to >= keys.length) return Promise.resolve(false);
    keys.splice(to, 0, ...keys.splice(at, 1));
    return reorderMembers(keys);
  }

  /** Make one member the cover: position 0, the rest in the order they had. */
  function makeCover(key) {
    const keys = openMembers.value.map((card) => card.key);
    if (!keys.includes(key) || keys[0] === key) return Promise.resolve(false);
    return reorderMembers([key, ...keys.filter((entry) => entry !== key)]);
  }

  /** Take one member out of the stack; it stands on its own afterwards. */
  async function unstackMember(key) {
    const cover = openStackKey.value;
    try {
      await unstackWorkflow(key);
      // The cover leaving makes the next member the cover, and the panel has
      // to follow it rather than stay keyed on a card that is no longer a
      // stack. `refreshAfterWrite` closes the panel if nothing is left.
      const next =
        key === cover
          ? (openMembers.value.find((card) => card.key !== key)?.key ?? null)
          : cover;
      await refreshAfterWrite(next);
    } catch (err) {
      console.warn(`[workflows] could not unstack ${key}`, err);
      error.value = errorMessage(err, "Could not take this card out.");
    }
  }

  /**
   * Hide one member. It leaves the grid, and the stack it was in with it.
   *
   * Nothing is deleted: the `workflow_stack_member` rows stand and the card
   * still opens on its own URL. The view's subtitle counts it from then on.
   */
  async function hideMember(key) {
    const cover = openStackKey.value;
    try {
      await patchWorkflowCard(key, { hidden: true });
      await refreshAfterWrite(key === cover ? null : cover);
    } catch (err) {
      console.warn(`[workflows] could not hide ${key}`, err);
      error.value = errorMessage(err, "Could not hide this card.");
    }
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
    openStackId,
    openStackSize,
    fetchCards,
    openStack,
    closeStack,
    forgetMembers,
    invalidate,
    toggleStack,
    reorderMembers,
    moveMember,
    makeCover,
    unstackMember,
    hideMember,
    select,
    selectRange,
    clearSelection,
    setSortKey,
    reset,
  };
});
