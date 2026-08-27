// The server's own explanation for a refusal, for the 70-odd places that show
// one.
//
// FastAPI answers a refusal with `detail`, and the app's habit was to inline
// `err?.response?.data?.detail || err?.message || "…"` at every catch site.
// That chain is wrong whenever `detail` is not a string: the backend serves the
// locked-set refusal as `{code, action, sets, picture_ids}` precisely so the
// client can write its own copy, and `object || fallback` takes the OBJECT -
// which reaches the user as "[object Object]", with the one refusal they could
// have acted on being the one that breaks.
//
// So the extraction lives here, once, and knows the difference.

import { isLockedRefusal, lockedSetsSentence } from "./dedup";

/**
 * The server's sentence for a rejection, or "" when it did not give one.
 *
 * Always a string. A structured locked-set refusal becomes the sentence naming
 * the sets; any other non-string detail becomes "", so the caller's own
 * fallback is what the user reads.
 *
 * @param {*} err - the rejection a catch block received.
 * @returns {string}
 */
export function errorDetail(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return detail.trim();
  if (isLockedRefusal(err)) return lockedSetsSentence(detail?.sets);
  return "";
}

/**
 * The sentence to show the user for a failed request.
 *
 * The server's reason if it gave one, then the transport's message, then the
 * caller's own copy. Pass a fallback that says what failed - `err.message` on
 * its own is "Request failed with status code 500".
 *
 * @param {*} err
 * @param {string} fallback
 * @returns {string}
 */
export function errorMessage(err, fallback) {
  return errorDetail(err) || err?.message || fallback;
}
