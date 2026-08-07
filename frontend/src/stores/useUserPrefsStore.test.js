import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

const isReadOnly = ref(false);
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  get isReadOnly() {
    return isReadOnly;
  },
}));

vi.mock("../api/config", () => ({
  patchUserConfig: vi.fn(),
}));

import { patchUserConfig } from "../api/config";
import { useUserPrefsStore } from "./useUserPrefsStore";

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  patchUserConfig.mockReset();
});

describe("useUserPrefsStore consent persistence", () => {
  it("persists the whole consent choice in one request", async () => {
    patchUserConfig.mockResolvedValue({ status: "success" });
    const prefs = useUserPrefsStore();
    const decision = {
      check_for_updates: true,
      telemetry_send_install_id: true,
      telemetry_consent_prompted: true,
    };

    expect(await prefs.saveTelemetry(decision)).toBe(true);

    expect(patchUserConfig).toHaveBeenCalledTimes(1);
    expect(patchUserConfig).toHaveBeenCalledWith(decision);
    expect(prefs.checkForUpdates).toBe(true);
    expect(prefs.telemetrySendInstallId).toBe(true);
    expect(prefs.telemetryConsentPrompted).toBe(true);
  });

  it("applies none of the choice locally when the request fails", async () => {
    patchUserConfig.mockRejectedValue(new Error("offline"));
    const prefs = useUserPrefsStore();

    expect(
      await prefs.saveTelemetry({
        check_for_updates: true,
        telemetry_send_install_id: true,
        telemetry_consent_prompted: true,
      }),
    ).toBe(false);

    expect(prefs.checkForUpdates).toBe(null);
    expect(prefs.telemetrySendInstallId).toBe(false);
    expect(prefs.telemetryConsentPrompted).toBe(false);
  });
});
