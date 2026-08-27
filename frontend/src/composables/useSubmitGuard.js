// One in-flight submit at a time, plus the pending flag its button wears.
//
// The bug this exists for (#647): a create form's button stays live while its
// POST is in flight, so a double-click - or an impatient second click while the
// server is busy captioning an import - sends the request twice and the library
// gains two identical people, sets, or folders. The window is invisible to the
// user and widest exactly when the server is slowest, which is when they are
// most likely to click again.
//
// Two halves, and both are needed:
//
//   - `pending` is what the button binds, so the second click never happens.
//   - `run` refuses a re-entrant call anyway, because the button is not the only
//     way in. Every one of these forms also submits on Enter (an `@enter` on the
//     name field, a `@keydown.enter`, a Ctrl+Enter document listener), and key
//     auto-repeat fires those faster than any disabled attribute can be painted.
//     Guarding the handler covers both doors; guarding only the button covers
//     one.
//
// It deliberately does NOT catch. A guard that swallowed the rejection would
// hide the failure these forms already report through `useNoticeStore` or an
// inline error line. `pending` clears in `finally`, so a failed submit re-enables
// its button and the user can retry - which is the other half of what #647 asks
// for.

import { ref } from "vue";

/**
 * Wrap an async submit handler so it cannot overlap itself.
 *
 * @template {(...args: any[]) => any} T
 * @param {T} handler - the submit work. May be sync or async; its return value
 *   is passed through. Errors propagate to the caller unchanged.
 * @returns {{ pending: import("vue").Ref<boolean>,
 *   run: (...args: Parameters<T>) => Promise<Awaited<ReturnType<T>> | undefined> }}
 *   Bind `pending` to the submit button's `:disabled` / `:loading`, and call
 *   `run` wherever the handler used to be called. A call made while one is
 *   already in flight is ignored and resolves to `undefined`.
 */
export function useSubmitGuard(handler) {
  const pending = ref(false);

  async function run(...args) {
    if (pending.value) return undefined;
    pending.value = true;
    try {
      return await handler(...args);
    } finally {
      pending.value = false;
    }
  }

  return { pending, run };
}
