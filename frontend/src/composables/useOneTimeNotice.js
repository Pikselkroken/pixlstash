// A notice that is shown once and then never again.
//
// Distinct from `useNoticeStore` (transient, in-memory, re-raisable) and from
// `useScopedNotice` (lifetime-scoped within one session): this one is a
// migration nudge. It has to survive a reload, because its whole job is to
// explain, exactly once, that something the user used to reach one way now
// lives somewhere else.
//
// It is client-side on purpose. "Have I already told this person where the
// duplicates went" is a per-browser fact about a UI they are looking at, not a
// library setting worth a round trip and a server column. A user on a second
// machine seeing the pointer once more is the correct failure mode; a user
// whose only machine forgot it and never shows it again is not.
//
// Storage failures (private mode, disabled storage, quota) fall back to
// "unseen": showing a migration notice one extra time is harmless, and
// swallowing the failure silently would hide a broken storage layer.

import { ref } from "vue";

/** Every key this composable writes is namespaced like the rest of the app. */
export const ONE_TIME_NOTICE_PREFIX = "pixlstash:seen:";

/**
 * Read one flag, treating any storage failure as "not yet seen".
 * @param {string} key - the full storage key.
 * @returns {boolean}
 */
function readSeen(key) {
  try {
    return window.localStorage.getItem(key) === "1";
  } catch (err) {
    console.warn(
      `[notice] could not read the one-time notice flag ${key}; showing it again`,
      err,
    );
    return false;
  }
}

/**
 * Write one flag, warning rather than throwing when storage refuses.
 * @param {string} key - the full storage key.
 */
function writeSeen(key) {
  try {
    window.localStorage.setItem(key, "1");
  } catch (err) {
    console.warn(
      `[notice] could not persist the one-time notice flag ${key}; it will show again`,
      err,
    );
  }
}

/**
 * Track whether a one-time notice has already been shown.
 *
 * @param {string} name - a short stable name, namespaced automatically.
 * @returns {{ visible: import("vue").Ref<boolean>, dismiss: function(): void,
 *   reset: function(): void, storageKey: string }}
 *   `visible` is false from the first render once the notice has been
 *   dismissed; `reset` exists for tests and for a future "show me the tour
 *   again" affordance.
 */
export function useOneTimeNotice(name) {
  const storageKey = `${ONE_TIME_NOTICE_PREFIX}${name}`;
  const visible = ref(!readSeen(storageKey));

  /** Hide the notice and remember that it has been seen. */
  function dismiss() {
    if (!visible.value) return;
    visible.value = false;
    writeSeen(storageKey);
  }

  /** Forget the dismissal, so the notice shows again. */
  function reset() {
    try {
      window.localStorage.removeItem(storageKey);
    } catch (err) {
      console.warn(
        `[notice] could not clear the one-time notice flag ${storageKey}`,
        err,
      );
    }
    visible.value = true;
  }

  return { visible, dismiss, reset, storageKey };
}
