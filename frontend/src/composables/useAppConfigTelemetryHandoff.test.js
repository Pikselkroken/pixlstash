// Where the privacy question gets asked on desktop.
//
// The shell asks it in the startup screen, before the app loads, and parks the
// answer for us. Applying that answer is what stops the in-app dialog asking a
// second time - the failure this replaces is a modal arriving over a library
// the user is already looking at.
//
// Three paths, and the third is the one that is easy to get wrong: a desktop
// launch where the question was never asked in the shell (an older shell, or an
// upgrade) must hand the window BACK to the startup screen rather than fall
// through to the dialog.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../api/config", () => ({
  getUserConfig: vi.fn(),
  patchUserConfig: vi.fn(async () => ({})),
}));

vi.mock("../utils/apiClient", () => ({
  isReadOnly: { value: false },
}));

import { getUserConfig } from "../api/config";
import { useAppConfig } from "./useAppConfig";
import { useUserPrefsStore } from "../stores/useUserPrefsStore";

// A config from a library whose owner has never answered the question.
const UNANSWERED = {
  sort: "date",
  thumbnail: 256,
  check_for_updates: null,
  telemetry_send_install_id: false,
  telemetry_consent_prompted: false,
};

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  getUserConfig.mockResolvedValue({ data: UNANSWERED });
  delete window.pixlstashDesktop;
});

describe("the desktop privacy handoff", () => {
  it("applies the answer the startup screen parked, and does not ask again", async () => {
    const takePendingTelemetry = vi.fn(async () => ({
      check_for_updates: true,
      telemetry_send_install_id: true,
      telemetry_consent_prompted: true,
    }));
    const askStartupQuestion = vi.fn();
    window.pixlstashDesktop = { takePendingTelemetry, askStartupQuestion };
    const onTelemetryConsentRequired = vi.fn();
    const prefs = useUserPrefsStore();
    const saveTelemetry = vi
      .spyOn(prefs, "saveTelemetry")
      .mockResolvedValue(true);

    const { fetchConfig } = useAppConfig({ onTelemetryConsentRequired });
    await fetchConfig();

    expect(takePendingTelemetry).toHaveBeenCalledTimes(1);
    expect(saveTelemetry).toHaveBeenCalledWith(
      expect.objectContaining({ telemetry_consent_prompted: true }),
    );
    expect(onTelemetryConsentRequired).not.toHaveBeenCalled();
    expect(askStartupQuestion).not.toHaveBeenCalled();
  });

  it("hands the question back to the startup screen when nothing was parked", async () => {
    const askStartupQuestion = vi.fn(async () => true);
    window.pixlstashDesktop = {
      takePendingTelemetry: vi.fn(async () => null),
      askStartupQuestion,
    };
    const onTelemetryConsentRequired = vi.fn();

    const { fetchConfig } = useAppConfig({ onTelemetryConsentRequired });
    await fetchConfig();

    expect(askStartupQuestion).toHaveBeenCalledWith("privacy");
    expect(onTelemetryConsentRequired).not.toHaveBeenCalled();
  });

  it("still asks in-app in a browser, which has no startup screen", async () => {
    const onTelemetryConsentRequired = vi.fn();

    const { fetchConfig } = useAppConfig({ onTelemetryConsentRequired });
    await fetchConfig();

    expect(onTelemetryConsentRequired).toHaveBeenCalledWith({
      isUpgrade: false,
    });
  });
});
