// The duplicate queue's in-row stack expansion (design decision D4 in
// `docs/design/mixed-stacks-and-stack-units.md`).
//
// A deck in a queue row stands for a whole existing stack, and the row shows
// exactly one picture of it. Pressing the count badge opens the rest as a
// full-width band below that row's columns. This composable owns the state that
// makes the band safe to have:
//
//   * **At most one expansion in the whole queue, and it lives on the FOCUSED
//     row.** Not a preference. `DuplicateQueue` sizes both of its scroll
//     spacers from one uniform `rowPitchPx` measured across two collapsed
//     rows, so a second variable-height row breaks that arithmetic. Keeping it
//     to the focused row makes the expansion a single known offset below a
//     single known index, and moving the focus collapses it
//     (`keepOnlyOn`) — which is exactly why `StackExpansionStrip` was never
//     mounted in the virtualised grid.
//   * **The members are LAZY.** The queue payload sizes each stack and names
//     its leader eagerly (`groups[].stacks`); the members themselves are a
//     separate read, because inlining a 40-member stack behind a row with room
//     for none is what the queue's never-render-whole rule forbids.
//
// It deliberately holds no cache across rows. One band exists at a time and it
// collapses the moment the cursor moves, so a cache would only ever be a way to
// show a stack that has changed since it was read; a re-open is one request the
// user asked for.

import { computed, ref } from "vue";

import { listStackMembers, MAX_STACK_MEMBER_PAGE } from "../api/dedup";
import { candidateId, groupUnits } from "../utils/dedup";

/**
 * The queue's single row expansion.
 *
 * @param {Object} [deps]
 * @param {function(number|string, Object): Promise<Object>} [deps.fetchMembers]
 *   the member read, injected so the state machine can be exercised without a
 *   network layer. Defaults to `listStackMembers`.
 * @returns {Object} the state the row renders and the four gestures that move
 *   it: `toggle`, `toggleForGroup` (the `E` key), `retry` and `keepOnlyOn`.
 */
export function useDedupRowExpansion({ fetchMembers = listStackMembers } = {}) {
  /** The signature of the row the band is open on; empty when none is. */
  const openSignature = ref("");
  /** The stack the band is showing, or null. */
  const openStackId = ref(null);
  const members = ref([]);
  const loading = ref(false);
  /** The rejection, when the last read failed. */
  const readError = ref(null);

  /**
   * Discards a response that outlived its own open.
   *
   * A band can be closed, reopened on another stack, or collapsed by a focus
   * move while its read is in flight; without this, the late response would
   * draw one stack's members under another stack's badge.
   */
  let requestToken = 0;

  /**
   * A stack with no members is a failed read, not an empty stack: the route
   * answers 404 rather than serving an empty membership, so an empty list here
   * always means something went wrong on the way.
   */
  const failed = computed(
    () => Boolean(readError.value) || (!loading.value && !members.value.length),
  );

  /**
   * The stack this row has open, or null.
   * @param {string} signature
   * @returns {number|string|null}
   */
  function stackIdFor(signature) {
    if (!openSignature.value || openSignature.value !== signature) return null;
    return openStackId.value;
  }

  /** Close the band and abandon any read still in flight. */
  function collapse() {
    requestToken += 1;
    openSignature.value = "";
    openStackId.value = null;
    members.value = [];
    loading.value = false;
    readError.value = null;
  }

  /**
   * Hold the "one expansion, on the focused row" invariant.
   *
   * Called with the focused group's signature whenever the cursor moves. It is
   * stated as "collapse unless it is still the focused row" rather than as a
   * plain collapse-on-move BECAUSE the gestures arrive in the other order: the
   * badge on an unfocused row emits `focus` and then `toggle-expansion`, so a
   * handler that collapsed on every focus change would close the band the same
   * click had just opened.
   *
   * @param {string} signature - the focused group's signature, or "".
   */
  function keepOnlyOn(signature) {
    if (openSignature.value && openSignature.value !== signature) collapse();
  }

  /**
   * Read one stack's members into the band.
   *
   * @param {number|string} stackId
   * @returns {Promise<void>}
   */
  async function load(stackId) {
    const token = (requestToken += 1);
    loading.value = true;
    readError.value = null;
    members.value = [];
    try {
      // One page, at the server's own ceiling. A stack deeper than that shows
      // its first `MAX_STACK_MEMBER_PAGE` members under a badge that still
      // states the true depth, which is the honest degradation: this band is a
      // look, not an inventory.
      const data = await fetchMembers(stackId, {
        limit: MAX_STACK_MEMBER_PAGE,
      });
      if (token !== requestToken) return;
      const served = Array.isArray(data?.members) ? data.members : [];
      members.value = served.map((member) => ({
        id: candidateId(member),
        thumbnail_version: member?.thumbnail_version,
      }));
      loading.value = false;
    } catch (err) {
      console.warn(
        `[dedup] failed to read the members of stack ${stackId} for the queue row's expansion`,
        err,
      );
      if (token !== requestToken) return;
      members.value = [];
      readError.value = err ?? new Error("stack member read failed");
      loading.value = false;
    }
  }

  /**
   * Open one deck's members, closing whichever band was open.
   *
   * @param {string} signature - the row the deck is in.
   * @param {number|string} stackId
   * @returns {boolean} whether the band is now open.
   */
  function toggle(signature, stackId) {
    if (
      signature === undefined ||
      signature === null ||
      stackId === undefined ||
      stackId === null
    ) {
      return false;
    }
    if (String(stackIdFor(signature)) === String(stackId)) {
      collapse();
      return false;
    }
    collapse();
    openSignature.value = signature;
    openStackId.value = stackId;
    load(stackId);
    return true;
  }

  /**
   * What the `E` key does: toggle the focused group's expansion.
   *
   * Opening picks the group's FIRST deck, which is the first tile in the strip
   * that has anything to disclose. A group with no deck has nothing to open, so
   * the key reports that rather than pretending.
   *
   * @param {Object} group - the focused group.
   * @returns {{open: boolean, stackId: (number|string|null)}|null} null when
   *   the group holds no deck at all.
   */
  function toggleForGroup(group) {
    const signature = group?.signature;
    if (!signature) return null;
    const held = stackIdFor(signature);
    if (held !== null) {
      collapse();
      return { open: false, stackId: held };
    }
    const deck = groupUnits(group).find((unit) => unit.kind === "deck");
    if (!deck) return null;
    toggle(signature, deck.stackId);
    return { open: true, stackId: deck.stackId };
  }

  /** Read the open stack's members again after a failure. */
  function retry() {
    if (openStackId.value === null) return;
    load(openStackId.value);
  }

  return {
    openSignature,
    openStackId,
    members,
    loading,
    failed,
    stackIdFor,
    toggle,
    toggleForGroup,
    retry,
    collapse,
    keepOnlyOn,
  };
}
