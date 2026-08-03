import { notifySessionReset } from "./apiClient";

/**
 * A full restore replaces the authentication database underneath this tab.
 * No response read before the swap is valid afterward, so synchronously clear
 * session-scoped stores and let app bootstrap establish a fresh session.
 */
export function reloadAfterFullRestore() {
  notifySessionReset("full restore");
  window.location.reload();
}
