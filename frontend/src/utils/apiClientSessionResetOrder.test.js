// The order of the two lines that end and begin a session.
//
// `login()` and `logout()` each do two things: notify the session reset, and
// flip `isAuthenticated`. Both notify FIRST, and several unrelated pieces of
// correctness rest on that without saying so - which is exactly why it is
// pinned here rather than left to a comment.
//
// The flag is what mounts and unmounts App.vue (`Root.vue`), so:
//
//   * logout notifying first means App.vue's teardown runs AFTER the reset, so
//     anything the teardown hands to a store (`useUpdatesSocket` releases the
//     ids a tag pass was holding into `useWsStore`) lands in a store that has
//     already been cleared;
//   * login notifying first is therefore the sweep that catches it, before the
//     new session's App.vue exists to read it.
//
// Swap either pair and the "view changed" pill offers the previous session's
// picture ids to whoever logs in next in the same tab - silently, because
// picture ids from another library are still perfectly valid numbers.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

const post = vi.fn();
vi.mock("axios", () => {
  const instance = {
    post: (...args) => post(...args),
    get: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  };
  return { default: { create: () => instance } };
});

import {
  isAuthenticated,
  login,
  logout,
  onSessionReset,
} from "./apiClient";

/** Record what `isAuthenticated` was at the moment each reset fired.
 *  Handlers are called with NO arguments (`notifySessionReset` passes the
 *  reason only to its own log line), so the flag is the whole observation. */
function recordFlagAtReset() {
  const seen = [];
  const stop = onSessionReset(() => seen.push(isAuthenticated.value));
  return { seen, stop };
}

let stopListening;

beforeEach(() => {
  post.mockReset().mockResolvedValue({ data: {} });
  isAuthenticated.value = false;
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  stopListening?.();
  vi.restoreAllMocks();
});

describe("a session transition notifies before it flips the flag", () => {
  it("login resets while the app is still torn down", async () => {
    const { seen, stop } = recordFlagAtReset();
    stopListening = stop;

    await login("me", "example-password");

    expect(seen).toHaveLength(1);
    // The point: false, not true. A subscriber clearing here runs before the
    // incoming session's App.vue is mounted to write anything.
    expect(seen[0]).toBe(false);
    expect(isAuthenticated.value).toBe(true);
  });

  it("logout resets while the app is still mounted", async () => {
    isAuthenticated.value = true;
    const { seen, stop } = recordFlagAtReset();
    stopListening = stop;

    await logout();

    expect(seen).toHaveLength(1);
    // True, not false: App.vue is still up, so its `onUnmounted` has NOT run
    // yet and will write after this. That is why login has to sweep too.
    expect(seen[0]).toBe(true);
    expect(isAuthenticated.value).toBe(false);
  });
});
