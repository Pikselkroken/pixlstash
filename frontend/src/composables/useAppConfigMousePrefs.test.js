// The two Mouse preferences (#2138) load from the user config and save back
// to it, and a value the client does not know is never taken or sent.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { nextTick } from "vue";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../api/config", () => ({
  getUserConfig: vi.fn(),
  patchUserConfig: vi.fn(async () => ({})),
}));

vi.mock("../utils/apiClient", () => ({
  isReadOnly: { value: false },
  onSessionReset: () => () => {},
}));

import { getUserConfig, patchUserConfig } from "../api/config";
import { useAppConfig } from "./useAppConfig";
import { useUserPrefsStore } from "../stores/useUserPrefsStore";

// Answered, so the telemetry prompt stays out of the way.
const BASE = { telemetry_consent_prompted: true };

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe("mouse preferences", () => {
  it("hydrates both from the server", async () => {
    getUserConfig.mockResolvedValue({
      ...BASE,
      grid_open_gesture: "single",
      viewer_wheel_action: "browse",
    });
    await useAppConfig({}).fetchConfig();
    const prefs = useUserPrefsStore();
    expect(prefs.gridOpenGesture).toBe("single");
    expect(prefs.viewerWheelAction).toBe("browse");
  });

  it("keeps the defaults for a value it does not know", async () => {
    getUserConfig.mockResolvedValue({
      ...BASE,
      grid_open_gesture: "triple",
      viewer_wheel_action: null,
    });
    await useAppConfig({}).fetchConfig();
    const prefs = useUserPrefsStore();
    expect(prefs.gridOpenGesture).toBe("double");
    expect(prefs.viewerWheelAction).toBe("zoom");
  });

  it("saves a change, and never an unknown value", async () => {
    getUserConfig.mockResolvedValue({ ...BASE });
    await useAppConfig({}).fetchConfig();
    const prefs = useUserPrefsStore();
    prefs.viewerWheelAction = "browse";
    await nextTick();
    await nextTick();
    // The patch is a diff against what was loaded; fields the snapshot never
    // recorded ride along, so assert on these two keys only.
    const sent = patchUserConfig.mock.calls[0][0];
    expect(sent.viewer_wheel_action).toBe("browse");
    expect(sent).not.toHaveProperty("grid_open_gesture");
    patchUserConfig.mockClear();
    prefs.gridOpenGesture = "bogus";
    await nextTick();
    await nextTick();
    for (const [patch] of patchUserConfig.mock.calls) {
      expect(patch).not.toHaveProperty("grid_open_gesture");
    }
  });
});
