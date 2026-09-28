import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import {
  cloneWorkflowWithModels,
  deleteWorkflowFile,
  duplicateWorkflow,
  listWorkflowCards,
  mergeWorkflows,
  patchWorkflowCard,
  splitWorkflow,
} from "../api/workflows";
import {
  WORKFLOW_SOURCE_LABELS,
  workflowFilterChips,
} from "../utils/filterChips";
import { checkpointModel } from "../utils/workflowCard";
import { onSessionReset } from "../utils/apiClient";
import { errorMessage } from "../utils/apiError";

/**
 * The Workflows grid (v1.12 F1a) — the cards, the sort and the selection.
 *
 * **One card per workflow, keyed by its `id`** (#1623): `auto:<core hash>` or
 * a manual group's uuid. There are no stacks to open any more; a workflow that
 * holds several topologies is still one card, and Merge / Split are the two
 * writes that change which topologies a workflow holds.
 */

export const SORT_KEYS = ["rating", "used", "pictures", "name"];

export const SORT_LABELS = {
  rating: { label: "Your ratings" },
  used: { label: "Recently used" },
  pictures: { label: "Picture count" },
  name: { label: "Name" },
};

/** The numeric sort keys, each as `(card) => number`, descending. */
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

// Name is the one key that reads A to Z rather than highest first. Numeric so
// "v2" sorts before "v10"; the collator already keeps "flux" beside "Flux".
const nameCollator = new Intl.Collator(undefined, { numeric: true });
const byName = (a, b) => nameCollator.compare(a.name ?? "", b.name ?? "");

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

  // A stamp, not a boolean: an in-flight fetch that resolves after a session
  // reset must not write its rows, or its cover thumbnail URLs, into the new
  // session's store.
  let epoch = 0;

  /** Selected workflow ids. */
  const selectedKeys = ref([]);

  /**
   * Where the reader was, for the one journey that leaves this screen and
   * comes back: a cover tile opening its picture (#1455).
   *
   * `App.vue` mounts the view under `v-else-if` with no `<KeepAlive>`, so the
   * return is a fresh mount — the scroller starts at 0 and the view's local
   * `cursorId` starts empty. The SELECTION is in this store and survives, so
   * without this the reader came back to a pill counting a card that is a
   * screen and a half up, with the cursor on the first row of the grid rather
   * than on the card whose picture they had just been looking at.
   *
   * Here rather than in the view for exactly that reason: it has to outlive
   * the component. `{cursorId, scrollTop}`, or null when nothing is parked.
   */
  const parkedPlace = ref(null);

  /** Remember where the reader is, on the way out to a picture. */
  function park(place) {
    parkedPlace.value = place;
  }

  /** Take back what was parked, once. A second read gets nothing. */
  function unpark() {
    const place = parkedPlace.value;
    parkedPlace.value = null;
    return place;
  }

  /**
   * The bulk verb that is running, or `""`.
   *
   * **One at a time, and the surfaces read it.** Every verb below writes once
   * per key and each write runs a whole `read_grid()` on the server, so a
   * selection of forty is forty serial round trips with nothing on screen
   * saying so — and a second press part-way through re-issues writes against
   * state the first press has already changed. That is not merely wasted: the
   * second pass 404s on a file already trashed,
   * and the refusal count then reports a FAILURE for an operation that
   * succeeded.
   *
   * Named rather than a boolean, so a surface can show the spinner on the
   * control that was actually pressed (`WorkflowTab` already does this with a
   * local `busy` of its own shape).
   */
  const verbBusy = ref("");
  /**
   * The cards the grid draws, after the four narrowing filters.
   *
   * **Hide one-offs and Show hidden are NOT here**: they are the server's,
   * decided per workflow when it reads the grid. The four below only ever
   * remove a card.
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
    // A copy: `cards` is the fetch order and a sort in place would make the
    // next resort depend on the last one.
    const copy = [...filteredCards.value];
    if (sortKey.value === "name") return copy.sort(byName);
    const value = SORT_VALUES[sortKey.value] ?? SORT_VALUES.rating;
    return copy.sort((a, b) => value(b) - value(a));
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
   * The Checkpoint list names each workflow's base card's checkpoint, which is
   * what `checkpointModel` reads off the card.
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
   * Read the grid again, dropping whatever read is still on the wire.
   *
   * The epoch bump is what stops an older read landing after this one, and
   * `loading` is cleared because the old epoch's `finally` will not clear it —
   * `fetchCards` early-returns to nothing while it is set.
   */
  function refetch() {
    epoch += 1;
    loading.value = false;
    return fetchCards();
  }

  /**
   * Forget what was read and read the grid again, after something OUTSIDE this
   * view changed what the cards say (a ghost purge in Settings › Privacy).
   *
   * **Only re-read when the grid has been read**, so a purge made by somebody
   * who has never opened Workflows fires no request.
   */
  function invalidate() {
    // Only a store that has been read: bumping the epoch under a first read
    // still on the wire would discard it with nothing to replace it.
    if (!loaded.value) return;
    epoch += 1;
    loading.value = false;
    fetchCards();
  }

  // ── The selection's own verbs (v1.12 F3, #1455) ───────────────────────
  //
  // The grid's selection bar and the rail's Workflow tab offer the same bulk
  // verbs, so they live here and not in either surface.
  //
  // Every one of them re-reads the grid: hiding, merging, splitting and
  // deleting all change what the cards SAY, and none of that can be
  // re-derived from a response that only names what was written.

  /** The selected cards; an id the grid no longer lists is dropped. */
  const selectedCards = computed(() =>
    selectedKeys.value
      .map((id) => cards.value.find((entry) => entry.id === id))
      .filter(Boolean),
  );

  /**
   * Run one write per id, then re-read the grid ONCE.
   *
   * **Every key is attempted and the count of refusals comes back.** A plain
   * loop that threw on the first refusal left the ones before it written on
   * the server, still drawn and still selected, under one sentence saying
   * none of it worked — `WorkflowTab.hideSelected` learned that first and this
   * is that lesson moved somewhere both surfaces reach.
   *
   * Serial, not `Promise.all`: each write runs a whole `read_grid()` on the
   * server, and the hub is single-writer.
   *
   * @returns {Promise<{done: number, refused: number}>}
   */
  async function writeEach(verb, keys, write) {
    verbBusy.value = verb;
    let refused = 0;
    let firstError = null;
    try {
      for (const key of keys) {
        try {
          await write(key);
        } catch (err) {
          refused += 1;
          firstError = firstError ?? err;
        }
      }
      if (firstError)
        console.warn("[workflows] some writes were refused", firstError);
      await refetch();
    } finally {
      // In a `finally`, so a throw from `fetchCards` cannot leave every verb
      // on this screen disabled for the rest of the session.
      verbBusy.value = "";
    }
    return { done: keys.length - refused, refused };
  }

  /**
   * Merge every selected workflow into one (#1623). The first selected is the
   * cover: its name, notes, defaults, pins and inputs are what the merged
   * workflow keeps.
   *
   * The selection moves to the merged workflow: the ids it named are gone.
   */
  async function mergeSelected() {
    // Off `selectedCards`, as `deleteSelected` is: an id the grid can no
    // longer resolve would refuse the whole merge. Selection order is kept,
    // so the first selected is still the cover.
    const ids = selectedCards.value.map((card) => card.id);
    if (ids.length < 2 || verbBusy.value) return false;
    verbBusy.value = "merge";
    try {
      const body = await mergeWorkflows(ids);
      await refetch();
      selectedKeys.value = body?.id ? [body.id] : [];
      return true;
    } catch (err) {
      console.warn("[workflows] could not merge the selection", ids, err);
      error.value = errorMessage(err, "Could not merge those workflows.");
      return false;
    } finally {
      verbBusy.value = "";
    }
  }

  /**
   * Split one topology out of workflow `id` into a workflow of its own.
   *
   * Answers the new workflow's id, which becomes the selection, or null.
   */
  async function splitOut(id, topology) {
    if (!id || !topology || verbBusy.value) return null;
    verbBusy.value = "split";
    try {
      const body = await splitWorkflow(id, topology);
      await refetch();
      if (body?.id) selectedKeys.value = [body.id];
      return body?.id ?? null;
    } catch (err) {
      console.warn(`[workflows] could not split ${topology} out of ${id}`, err);
      error.value = errorMessage(err, "Could not split that workflow.");
      return null;
    } finally {
      verbBusy.value = "";
    }
  }

  /**
   * Hide, or unhide, every selected card.
   *
   * Both directions through one function because they are one route and one
   * gesture — the bar's button says which, from whether everything selected is
   * already hidden. Hiding takes the cards off the grid, so the selection goes
   * with them; unhiding leaves them there and the selection stands.
   */
  async function hideSelected(hidden = true) {
    const keys = [...selectedKeys.value];
    if (!keys.length || verbBusy.value) return { done: 0, refused: 0 };
    const result = await writeEach("hide", keys, (key) =>
      patchWorkflowCard(key, { hidden }),
    );
    if (hidden && !result.refused) clearSelection();
    return result;
  }

  /** The card Run acts on: the one selected card, or null. */
  const runnableCard = computed(() =>
    selectedKeys.value.length === 1 ? (selectedCards.value[0] ?? null) : null,
  );

  /** Give one card a name of the owner's own. `null` clears it. */
  async function renameCard(key, name) {
    if (verbBusy.value) return false;
    verbBusy.value = "rename";
    try {
      await patchWorkflowCard(key, { name: name || null });
      await refetch();
      return true;
    } catch (err) {
      console.warn(`[workflows] could not rename ${key}`, err);
      error.value = errorMessage(err, "Could not rename that workflow.");
      return false;
    } finally {
      verbBusy.value = "";
    }
  }

  /**
   * Copy one card's workflow into the user's folder, under a free name.
   *
   * The copy is a card of its own, so the grid is re-read; the answer's
   * `workflow_id` is where it landed and is handed back for the notice.
   */
  async function duplicateCard(key) {
    if (verbBusy.value) return null;
    verbBusy.value = "duplicate";
    try {
      const body = await duplicateWorkflow(key);
      await refetch();
      return body;
    } catch (err) {
      console.warn(`[workflows] could not duplicate ${key}`, err);
      error.value = errorMessage(err, "Could not duplicate that workflow.");
      return null;
    } finally {
      verbBusy.value = "";
    }
  }

  /**
   * Write a copy of one card with model files replaced, as a card of its own.
   *
   * `duplicateCard` with a body: the grid is re-read rather than patched, and
   * the answer is handed back for the notice. `null` on failure, with the
   * reason in `error`.
   */
  async function cloneCardWithModels(key, body) {
    if (verbBusy.value) return null;
    verbBusy.value = "clone-with-models";
    try {
      const answer = await cloneWorkflowWithModels(key, body);
      await refetch();
      return answer;
    } catch (err) {
      console.warn(`[workflows] could not clone ${key} with new models`, err);
      error.value = errorMessage(err, "Could not clone that workflow.");
      return null;
    } finally {
      verbBusy.value = "";
    }
  }

  /**
   * Send every selected card's workflow FILE to the system trash.
   *
   * The cards and their pictures stay: this deletes the file that runs them.
   * A card the library knows only from its pictures has none and the route
   * 409s, which is why the bar offers this only when every selected card is
   * `imported`.
   */
  async function deleteSelected() {
    // Off `selectedCards`, not `selectedKeys`: those are the cards the bar's
    // `imported` gate actually vetted, and a key the grid can no longer
    // resolve was never in that check. The one verb here that touches bytes
    // acts on exactly what was looked at.
    const keys = selectedCards.value.map((card) => card.id);
    if (!keys.length || verbBusy.value) return { done: 0, refused: 0 };
    return writeEach("delete", keys, (key) => deleteWorkflowFile(key));
  }

  /** Replace the selection, or toggle one id into it (Ctrl/Cmd). */
  function select(key, { additive = false } = {}) {
    if (!additive) {
      selectedKeys.value = [key];
      return;
    }
    selectedKeys.value = selectedKeys.value.includes(key)
      ? selectedKeys.value.filter((entry) => entry !== key)
      : [...selectedKeys.value, key];
  }

  /** Select a run of ids (Shift); the caller knows the drawn order. */
  function selectRange(keys) {
    selectedKeys.value = [...keys];
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
   * A partial patch, like `setView` on the retired shelf: a filter row knows
   * its own key and nothing about its neighbours' current values.
   */
  function setFilters(changes) {
    const before = filters.value;
    const next = { ...before, ...changes };
    filters.value = next;
    // **The selection goes with what left the screen.** The selection bar and
    // the rail's bulk verbs act on `selectedKeys`, so a filter that takes a
    // card away would otherwise leave "3 workflows selected" over a grid
    // drawing one — and Hide or Merge would act on cards the reader cannot see.
    if (selectedKeys.value.length) {
      const onScreen = new Set(filteredCards.value.map((card) => card.id));
      selectedKeys.value = selectedKeys.value.filter((key) =>
        onScreen.has(key),
      );
    }
    if (
      next.hideOneOffs !== before.hideOneOffs ||
      next.showHidden !== before.showHidden
    ) {
      refetch();
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
    selectedKeys.value = [];
    parkedPlace.value = null;
  }

  onScopeDispose(onSessionReset(reset));

  /**
   * Bumped whenever a saved recipe is written from anywhere (v1.12 F6).
   *
   * The Recipes tab watches the selected card, which is not enough: saving
   * from the Run popup opened *from that tab* changes the list behind a dialog
   * the selection never moved off, so the tab stayed stale until the owner
   * clicked away and back. The backend announces this as `workflows_changed`
   * with reason "recipes"; nothing in the client listens to that stream yet,
   * and this is the in-process half of the same signal until something does.
   */
  const recipesEpoch = ref(0);

  /** Say a recipe was saved, renamed or deleted. */
  function notedRecipesChanged() {
    recipesEpoch.value += 1;
  }

  /**
   * A picture the Recipes tab asked to open, or null.
   *
   * The tab is mounted by `App.vue` beside the grid, not inside
   * `WorkflowsView`, and only the view knows where the reader is - so the tab
   * asks here and the view opens it, parking its place as a cover click does.
   */
  const pictureToOpen = ref(null);

  /** Ask the Workflows view to open one picture in the lightbox. */
  function requestOpenPicture(pictureId) {
    pictureToOpen.value = pictureId;
  }

  return {
    recipesEpoch,
    notedRecipesChanged,
    pictureToOpen,
    requestOpenPicture,
    cards,
    oneOffs,
    hidden,
    loading,
    loaded,
    error,
    sortKey,
    filters,
    selectedKeys,
    filteredCards,
    sortedCards,
    filterChips,
    filterOfLabel,
    filterOptions,
    selectedCards,
    runnableCard,
    parkedPlace,
    park,
    unpark,
    verbBusy,
    fetchCards,
    refetch,
    invalidate,
    mergeSelected,
    splitOut,
    hideSelected,
    renameCard,
    duplicateCard,
    cloneCardWithModels,
    deleteSelected,
    select,
    selectRange,
    clearSelection,
    setSortKey,
    setFilters,
    clearFilters,
    reset,
  };
});
