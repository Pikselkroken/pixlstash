// Host names that can only mean "the machine this browser runs on".
const LOOPBACK_HOSTS = ["localhost", "127.0.0.1", "[::1]", "::1"];

/**
 * Whether the PixlStash server runs on the machine this page is shown on: the
 * desktop shell, or a page loaded over loopback. Gates controls that act on
 * that machine's desktop (Open with); the server enforces loopback on those
 * routes regardless, so this only keeps a dead control from LAN viewers.
 */
export function serverIsOnThisMachine() {
  if (typeof window === "undefined") return false;
  if (window.pixlstashDesktop) return true;
  return LOOPBACK_HOSTS.includes(
    String(window.location?.hostname || "").toLowerCase(),
  );
}
