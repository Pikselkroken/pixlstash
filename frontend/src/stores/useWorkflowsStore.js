import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import {
  getWorkflowCard,
  listWorkflowCards,
  patchWorkflowCard,
  reorderStack,
  unstackWorkflow,
} from "../api/workflows";
import {
  WORKFLOW_SOURCE_LABELS,
  workflowFilterChips,
} from "../utils/filterChips";
import { checkpointModel, isStack } from "../utils/workflowCard";
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
 *
 * **The panel follows a CHANGE of selection** (`syncPanelToSelection`), which
 * is what its rule is about and not an invariant over `selectedKeys`: picking
 * one stack opens it, picking something outside the open one closes it, and ▸
 * may leave a stack selected with no panel because nothing was picked.
 */

/**
 * The longest the panel's collapse is allowed to hold the stack open, in ms.
 *
 * A ceiling, not the duration: the panel reports its own `animationend` and
 * `finishCollapse` usually runs long before this. `--dur-2` is what it
 * animates over, and a machine asking for reduced motion runs that in
 * microseconds — which is exactly why the end has to be read off the animation
 * rather than counted here, or a motion preference would silently gate input
 * for a fifth of a second. This is only the backstop for an animation that
 * never starts at all.
 */
export const PANEL_COLLAPSE_MS = 200;

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

/**
 * What the Filters panel starts on (v1.12 F7).
 *
 * `hideOneOffs` is TRUE by default and `showHidden` FALSE, which is the grid
 * the server draws when it is asked nothing — so the default state of the
 * panel is no filter at all, and the filter button's count is 0.
 */
export const DEFAULT_FILTERS = Object.freeze({
  hideOneOffs: true,
  showHidden: false,
  // "Keeps something deleted": a picture ghost or the name of a model that is
  // not on the shelf. The card counts the two apart; this row asks for either.
  ghosts: false,
  type: null,
  checkpoint: null,
  // `imported` (a workflow file on this machine runs it) or `found` (it came
  // in with the pictures it made).
  source: null,
  minRating: null,
});

/** Does this card keep something deleted? */
function keepsGhost(card) {
  return (card.ghosts ?? 0) + (card.model_ghosts ?? 0) > 0;
}

export const useWorkflowsStore = defineStore("workflows", () => {
  const cards = ref([]);
  const oneOffs = ref(0);
  const hidden = ref(0);
  const loading = ref(false);
  const loaded = ref(false);
  const error = ref("");

  const sortKey = ref("rating");
  const filters = ref({ ...DEFAULT_FILTERS });

  /** The open stack's cover key, or null. At most one, ever. */
  const openStackKey = ref(null);
  /** True while the open panel is collapsing: still drawn, and shrinking. */
  const panelClosing = ref(false);
  let collapseTimer = null;
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

  /**
   * The cards the grid draws, after the four narrowing filters.
   *
   * **Hide one-offs and Show hidden are NOT here**: they are the server's,
   * because widening the set changes the stacking (`read_grid`), and a client
   * that let a hidden member back in would draw it beside a cover still
   * calling itself a stack of one. The four below only ever remove a card, so
   * no grouping can disagree with them.
   */
  const filteredCards = computed(() => {
    const view = filters.value;
    return cards.value.filter((card) => {
      if (view.ghosts && !keepsGhost(card)) return false;
      if (view.type != null && card.type !== view.type) return false;
      if (
        view.checkpoint != null &&
        checkpointModel(card)?.name !== view.checkpoint
      ) {
        return false;
      }
      if (view.source != null) {
        const imported = view.source === "imported";
        if (Boolean(card.imported) !== imported) return false;
      }
      if (view.minRating != null && (card.rating ?? 0) < view.minRating) {
        return false;
      }
      return true;
    });
  });

  const sortedCards = computed(() => {
    const value = SORT_VALUES[sortKey.value] ?? SORT_VALUES.rating;
    // A copy: `cards` is the fetch order and a sort in place would make the
    // next resort depend on the last one.
    return [...filteredCards.value].sort((a, b) => value(b) - value(a));
  });

  /**
   * Every filter that is not on its default, as the chips the strip draws.
   *
   * One list for both the strip and the filter button's badge, so the two
   * cannot disagree about how many filters are on — the same reason
   * `Toolbar.vue` counts `filterChips(filterStore)` rather than keeping a
   * tally of its own. The chips' WORDS are `utils/filterChips.js`'s, which is
   * the module that exists so one filter cannot be spelled two ways; this
   * store owns the state and the refetch decision, and nothing else.
   */
  const filterChips = computed(() =>
    workflowFilterChips(filters.value, cards.value, setFilters),
  );

  /**
   * "12 of 40" — what the grid is drawing of what the server sent.
   *
   * The one spelling of it, because the filter panel's header and the chip
   * strip's tail both show it and a second copy would drift. The server's two
   * flags are not in it: they change what `cards` IS, so there is no larger
   * number for the drawn one to be "of".
   */
  const filterOfLabel = computed(() => {
    const shown = filteredCards.value.length;
    const all = cards.value.length;
    return shown === all ? `${shown}` : `${shown} of ${all}`;
  });

  /**
   * What each pick-one row offers, counted.
   *
   * Over `cards` and not `filteredCards`: a count that narrowed as its
   * neighbours were picked would go to zero on the row you are reading and
   * read as "this library has none", which is the opposite of true.
   *
   * **`cards` is one card per STACK, so these lists describe covers.** A
   * checkpoint carried only by a stacked member is absent from the Checkpoint
   * list, and a stack of six on one checkpoint counts 1. That is what the
   * payload holds: `GET /workflows/cards` sends covers and names the members
   * in `member_keys`, and a member's card arrives only when somebody opens
   * that stack — so building the lists from what has been opened would grow
   * them as the reader browsed, which is worse than being consistently about
   * the grid. Making them library-wide needs the members in the payload, or a
   * facet count beside it, and that is a read this step does not add.
   *
   * The same seam is why `filteredCards` filters top-level cards only: an
   * open stack shows its whole stack. Filtering inside the panel would mean a
   * stack card saying "6 workflows" over a panel drawing two.
   */
  const filterOptions = computed(() => {
    const types = new Map();
    const checkpoints = new Map();
    let imported = 0;
    let ghosts = 0;
    const ratings = [0, 0, 0, 0, 0];
    for (const card of cards.value) {
      if (card.type) {
        const seen = types.get(card.type) ?? {
          id: card.type,
          label: card.type_label ?? card.type,
          count: 0,
        };
        seen.count += 1;
        types.set(card.type, seen);
      }
      const name = checkpointModel(card)?.name;
      if (name) {
        checkpoints.set(name, (checkpoints.get(name) ?? 0) + 1);
      }
      if (card.imported) imported += 1;
      if (keepsGhost(card)) ghosts += 1;
      for (let star = 1; star <= 5; star += 1) {
        if ((card.rating ?? 0) >= star) ratings[star - 1] += 1;
      }
    }
    return {
      ghosts,
      types: [...types.values()].sort((a, b) => b.count - a.count),
      checkpoints: [...checkpoints.entries()]
        .map(([id, count]) => ({ id, label: id, count }))
        .sort((a, b) => b.count - a.count || a.id.localeCompare(b.id)),
      sources: [
        {
          id: "imported",
          label: WORKFLOW_SOURCE_LABELS.imported,
          count: imported,
        },
        {
          id: "found",
          label: WORKFLOW_SOURCE_LABELS.found,
          count: cards.value.length - imported,
        },
      ],
      ratings: ratings.map((count, index) => ({
        id: index + 1,
        label: `${index + 1}★ and up`,
        count,
      })),
    };
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
      const body = await listWorkflowCards({
        includeHidden: filters.value.showHidden,
        includeOneOffs: !filters.value.hideOneOffs,
      });
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
    // **A collapse of THIS stack is left to finish.** ▸ does not stop its
    // click, so closing a stack from its caret also selects that card, and
    // `syncPanelToSelection` then asks for the same stack — cancelling here
    // would leave the caret unable to close anything at all.
    if (openStackKey.value === coverKey && panelClosing.value) return;
    cancelCollapse();
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
      // **Only the stack still on screen gets to put up the banner.** A plain
      // click moves the panel from one stack to another, so a member read for
      // the stack the reader has already left is routinely still on the wire;
      // its failure is worth recording, never worth telling them that the
      // stack they are looking at could not be read.
      if (openStackKey.value === coverKey) {
        error.value = errorMessage(err, "Could not read this stack.");
      }
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

  /**
   * Drop the panel now, with no animation.
   *
   * The immediate close is the right one wherever the panel's CONTENT has
   * stopped being true — members invalidated, the cover filtered off the grid
   * — because there is nothing left worth watching shrink, and a collapse
   * would spend a fifth of a second drawing "N could not be read" over a stack
   * that is being torn down. A gesture wants `collapseStack` instead.
   */
  function closeStack() {
    cancelCollapse();
    openStackKey.value = null;
  }

  /**
   * Close the panel, letting it collapse first.
   *
   * The stack stays open, marked `panelClosing`, until the panel reports the
   * animation finished — the member rows the grid splices in around the panel
   * go with `openStackKey`, so dropping it first leaves nothing on screen to
   * animate. Calling it twice is a no-op rather than a restart.
   */
  function collapseStack() {
    if (!openStackKey.value || panelClosing.value) return;
    panelClosing.value = true;
    collapseTimer = setTimeout(finishCollapse, PANEL_COLLAPSE_MS);
  }

  /** The panel has finished collapsing (or never started): drop it. */
  function finishCollapse() {
    if (!panelClosing.value) return;
    closeStack();
  }

  /** Abandon a collapse in progress and leave the panel open. */
  function cancelCollapse() {
    if (collapseTimer !== null) {
      clearTimeout(collapseTimer);
      collapseTimer = null;
    }
    panelClosing.value = false;
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
    if (openStackKey.value === coverKey) collapseStack();
    else openStack(coverKey);
  }

  /**
   * Every card a selection of the stack card `key` covers: the cover and its
   * members.
   *
   * **A stack is selected whole.** Its cover key alone left the whole gesture
   * naming the one workflow at the top of the pile: a reader who clicked a
   * card marked "3 workflows" had selected one of them, and the panel then
   * drew the mark on its first row and on none of the others. The stack is the
   * thing on screen, so it is the thing selected.
   *
   * Read off `cards`, the top-level list, so a key it does not hold answers
   * `[key]`. No `isStack` test: `routes/workflows.py` fills `member_keys` only
   * for a stack and always excludes the card itself, so a non-stack card's list
   * is empty and spreading it is the same answer the test would give.
   *
   * The CALLER says whether to expand at all (`select`'s `whole`), because the
   * one key that needs both answers is the cover's: it is the grid's stack card
   * AND the panel's first member row, and only the caller knows which was
   * clicked.
   *
   * @param {string|null} key
   * @returns {Array<string>}
   */
  function stackKeys(key) {
    if (!key) return [];
    const card = cards.value.find((entry) => entry.key === key);
    return [key, ...(card?.member_keys ?? [])];
  }

  /**
   * Replace the selection, or toggle one key into it (Ctrl/Cmd).
   *
   * **`whole` is opt-in.** It expands a stack card into its whole stack, and
   * every caller that means the stack says so: the two gesture callers pass
   * `entry.kind === "card"`. Defaulting it to true made the `?topology=` deep
   * link — which names ONE workflow, from one picture's Recipe panel — select
   * the whole stack that workflow happens to sit in.
   */
  function select(key, { additive = false, whole = false } = {}) {
    const keys = whole ? stackKeys(key) : [key];
    if (!additive) {
      selectedKeys.value = keys;
      syncPanelToSelection();
      return;
    }
    // Ctrl removes only what is ALREADY there in full. Toggling on the clicked
    // key alone emptied a half-selected stack — take one member out, then Ctrl
    // the cover, and the cover's own membership dropped all of it — where what
    // that gesture means is "make this whole".
    if (keys.every((entry) => selectedKeys.value.includes(entry))) {
      const dropped = new Set(keys);
      selectedKeys.value = selectedKeys.value.filter(
        (entry) => !dropped.has(entry),
      );
      syncPanelToSelection();
      return;
    }
    const held = new Set(selectedKeys.value);
    selectedKeys.value = [
      ...selectedKeys.value,
      ...keys.filter((entry) => !held.has(entry)),
    ];
    syncPanelToSelection();
  }

  /**
   * An OPEN panel follows a change of selection.
   *
   * **It never opens one.** Opening a stack is the caret, Enter, or a
   * double-click on its card — a gesture that says "show me inside this" —
   * and a plain click is a selection, not that. What this rule settles is the
   * state the two used to contradict: a panel standing open under one stack
   * while the reader has gone and selected another, or several. So it moves
   * the band to the stack that was just selected, or shuts it, and when
   * nothing is open it does nothing at all.
   *
   * Moving it wants the WHOLE stack selected, which is what `select`'s
   * `whole` makes a click on a stack card mean. Closing it wants a selection
   * reaching OUTSIDE the open stack — outside, not "more than one": a count
   * would make Ctrl-click and Shift-click inside an open panel impossible, the
   * second pick destroying the rows being picked from, and the grid declares
   * `aria-multiselectable`, so multi-selecting a stack's members is a gesture
   * it promises.
   *
   * **A single card that is NOT a stack leaves the panel alone.** `openStack`
   * refuses a card with nothing under it, and every row inside the panel is
   * exactly that. So is a stack's cover key ALONE, which is what the
   * `?topology=` deep link selects: it names one workflow rather than the pile
   * it sits in.
   *
   * Because this is about a change, it is not an invariant over
   * `selectedKeys`: ▸ can leave a stack selected with no panel, and nothing
   * here re-opens it. That is the same state the caret has always produced.
   */
  function syncPanelToSelection() {
    // Nothing open, nothing to follow. This is what keeps a plain click a
    // selection rather than a way in.
    if (!openStackKey.value) return;
    const keys = selectedKeys.value;
    if (!keys.length) return;
    // The one card the whole selection is: a stack taken whole, or a single
    // row standing for itself.
    const only = keys.find((key) => {
      const covered = stackKeys(key);
      return (
        covered.length === keys.length &&
        covered.every((entry) => keys.includes(entry))
      );
    });
    if (only) {
      void openStack(only);
      return;
    }
    const inside = new Set(stackKeys(openStackKey.value));
    if (keys.every((key) => inside.has(key))) return;
    collapseStack();
  }

  /**
   * Select a run of keys (Shift). The caller hands the run because only it
   * knows the flat order, which interleaves a stack's members into the grid —
   * and, for the same reason, which of those keys is a stack card to expand and
   * which is a member row standing for itself.
   */
  function selectRange(keys) {
    selectedKeys.value = [...keys];
    syncPanelToSelection();
  }

  function clearSelection() {
    selectedKeys.value = [];
  }

  function setSortKey(key) {
    if (SORT_KEYS.includes(key)) sortKey.value = key;
  }

  /**
   * Change some of the filters, re-reading the grid when the server's two move.
   *
   * A partial patch, like `setView` on the retired shelf: a checkbox row knows
   * its own key and nothing about its neighbours' current values.
   */
  function setFilters(changes) {
    const before = filters.value;
    const next = { ...before, ...changes };
    filters.value = next;
    // **A filter that removes the open stack's COVER closes it.** The grid
    // draws the panel inside the cover's row, so a cover filtered out takes
    // the panel and the reader's cursor off the screen while `openStackKey`,
    // `openMembers` and the row's `aria-expanded` all go on saying a stack is
    // open - and the members, which were never filtered, would come back the
    // moment an unrelated filter moved. Closing it is the state the screen is
    // already in.
    if (
      openStackKey.value &&
      !filteredCards.value.some((card) => card.key === openStackKey.value)
    ) {
      closeStack();
    }
    // **And the selection goes with what left the screen.** The selection bar
    // and the rail's bulk verbs act on `selectedKeys`, so a filter that takes
    // a card away would otherwise leave "3 workflows selected" over a grid
    // drawing one — and Hide or Stack together would act on cards the reader
    // cannot see. A member of a stack that is still drawn stays selected:
    // its panel is on screen, and the cards it holds were never filtered.
    if (selectedKeys.value.length) {
      const onScreen = new Set(filteredCards.value.map((card) => card.key));
      for (const list of Object.values(members.value)) {
        for (const member of list) {
          if (onScreen.has(list[0]?.key)) onScreen.add(member.key);
        }
      }
      selectedKeys.value = selectedKeys.value.filter((key) =>
        onScreen.has(key),
      );
    }
    if (
      next.hideOneOffs !== before.hideOneOffs ||
      next.showHidden !== before.showHidden
    ) {
      // The cached members belong to the stacking the old flags produced, and
      // letting a hidden card back in re-groups it. `forgetMembers` bumps the
      // epoch, so the in-flight read cannot write the old grouping back.
      forgetMembers();
      fetchCards();
    }
  }

  function clearFilters() {
    setFilters({ ...DEFAULT_FILTERS });
  }

  function reset() {
    epoch += 1;
    filters.value = { ...DEFAULT_FILTERS };
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
    cancelCollapse();
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
    filters,
    openStackKey,
    panelClosing,
    members,
    membersLoading,
    selectedKeys,
    filteredCards,
    sortedCards,
    filterChips,
    filterOfLabel,
    filterOptions,
    openMembers,
    openStackId,
    openStackSize,
    fetchCards,
    openStack,
    closeStack,
    collapseStack,
    finishCollapse,
    forgetMembers,
    invalidate,
    toggleStack,
    reorderMembers,
    moveMember,
    makeCover,
    unstackMember,
    hideMember,
    stackKeys,
    select,
    selectRange,
    clearSelection,
    setSortKey,
    setFilters,
    clearFilters,
    reset,
  };
});
