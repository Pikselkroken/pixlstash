// useScopedNotice.js - retire a notice when the thing it talks about is gone.
//
// Most notices report an event ("Import finished") and are true forever, so a
// timeout is the only dismissal they need. A few report a *state* of the current
// context instead - "3 of the selected pictures are in locked sets" - and those
// have a second, harder deadline: the sentence stops being true the moment the
// selection, the view, or the lock changes. A notice with an action is sticky by
// spec (§6 rule 1, so the action stays reachable), which means nothing else will
// ever take it down. It sits there describing a selection the user has already
// moved on from.
//
// This composable is the missing half of that contract (notice-surface.md §9.6).
// The owner supplies a `signature` getter over everything the message asserts;
// when the signature changes, the notice family is dismissed.
//
// The ordering problem, and why `arm()` is deferred:
//
//   The operation that produces one of these notices usually mutates the very
//   state the signature watches - the bulk delete narrows the selection to the
//   frozen survivors and *then* reports what it skipped. A watcher armed at push
//   time would therefore fire on the pusher's own mutation and dismiss the card
//   before it was ever read. So `arm()` records the signature in `nextTick`,
//   after the flush that mutation scheduled: the baseline is the state the user
//   is left looking at, and only a change from THAT invalidates.

import { watch, nextTick } from "vue";
import { useNoticeStore } from "../stores/useNoticeStore";

/**
 * Bind a family of notice keys to the context they describe.
 *
 * @param {string[]} keys - the coalescing keys this scope owns. All of them are
 *   dismissed together: a card and its follow-up go stale at the same instant.
 * @param {() => string} signature - getter returning a value that changes
 *   whenever the message could stop being true. Compared with `!==`, so return
 *   a primitive (a joined string is the usual shape).
 * @returns {{arm: Function, invalidate: Function}} `arm()` after pushing;
 *   `invalidate()` to retire the family by hand.
 */
export function useScopedNotice(keys, signature) {
  const noticeStore = useNoticeStore();

  // The signature the live notices were pushed against. `null` means nothing is
  // live, so a context change has nothing to invalidate. `PENDING` means a push
  // just happened and its baseline has not settled yet - see `arm()`.
  const PENDING = Symbol("pending");
  let armedSignature = null;
  // Bumped by every arm/invalidate so a deferred baseline that has been
  // overtaken (a second push, or a manual invalidate) is dropped rather than
  // re-arming a family that is no longer on screen.
  let armToken = 0;

  /** Retire the whole family now. Safe to call when nothing is live. */
  function invalidate() {
    armToken += 1;
    armedSignature = null;
    for (const key of keys) noticeStore.dismissByKey(key);
  }

  /**
   * Mark the family live against the context as it settles. Call once per push;
   * calling it again simply re-baselines (a repeat is about the current state).
   */
  function arm() {
    // Synchronously, so the watcher job the pusher's own mutation queued sees
    // PENDING and stands down. Without this, the SECOND locked delete in a row
    // is silent: its context change is still queued when the refreshed card is
    // pushed, and that pending job would dismiss the card it never described.
    armedSignature = PENDING;
    const token = (armToken += 1);
    nextTick(() => {
      if (token === armToken) armedSignature = signature();
    });
  }

  watch(signature, (next) => {
    if (armedSignature === null || armedSignature === PENDING) return;
    if (next === armedSignature) return;
    invalidate();
  });

  return { arm, invalidate };
}
