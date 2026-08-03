import { notifySessionReset } from "./apiClient";

// The restore request and the updates socket live in different parts of the
// component tree. Keep this bit of ownership at module scope so the socket can
// distinguish the tab that dispatched the long-running POST from every other
// open tab. The initiating tab already reloads from RestoreConfirmDialog when
// that POST settles; reloading it on restore_started would abandon the only
// request that can surface a restore failure to its user.
let fullRestoreRequestInFlight = false;
let fullRestoreStateInvalidated = false;

export function beginFullRestoreRequest() {
  fullRestoreRequestInFlight = true;
}

export function endFullRestoreRequest() {
  fullRestoreRequestInFlight = false;
}

export function isFullRestoreRequestInFlight() {
  return fullRestoreRequestInFlight;
}

/** Drop data read under the pre-restore credential before the DB cutover. */
export function prepareForFullRestoreTransition() {
  if (fullRestoreStateInvalidated) return;
  fullRestoreStateInvalidated = true;
  notifySessionReset("full restore");
}

/**
 * A full restore replaces the authentication database underneath this tab.
 * No response read before the swap is valid afterward, so synchronously clear
 * session-scoped stores and let app bootstrap establish a fresh session.
 */
export function reloadAfterFullRestore() {
  prepareForFullRestoreTransition();
  window.location.reload();
}
