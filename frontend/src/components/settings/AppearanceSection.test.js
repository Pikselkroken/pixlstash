// The Tag chip size control (#2139): three steps, bound to the tag shortcuts
// store, which saves the choice with the user's settings.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("vuetify/components", () => ({
  VSwitch: { name: "VSwitch", template: "<div />" },
}));
vi.mock("../../utils/apiClient", () => ({
  isReadOnly: ref(false),
  sessionContext: ref(null),
  onSessionReset: () => () => {},
  newOperationBatchId: () => "batch",
}));
vi.mock("../../api/pictures", () => ({
  clearGuestScoreSession: vi.fn(),
}));
vi.mock("../../api/config", () => ({
  patchUserConfig: vi.fn().mockResolvedValue({}),
}));

import { patchUserConfig } from "../../api/config";
import { useTagClipboardStore } from "../../stores/useTagClipboardStore";
import AppearanceSection from "./AppearanceSection.vue";

describe("AppearanceSection: Tag chip size", () => {
  let pinia;

  beforeEach(() => {
    vi.clearAllMocks();
    pinia = createPinia();
    setActivePinia(pinia);
  });

  function mountSection() {
    return mount(AppearanceSection, {
      global: {
        plugins: [pinia],
        stubs: {
          AppSelect: true,
          AppButton: true,
          SettingsSliderRow: true,
          Segmented: false,
        },
      },
    });
  }

  it("offers Compact, Comfortable and Large, with the current size on", async () => {
    const wrapper = mountSection();
    const chip = wrapper.find('[aria-label="Chip size"]');
    expect(chip.findAll("button").map((b) => b.text())).toEqual([
      "Compact",
      "Comfortable",
      "Large",
    ]);
    expect(
      chip.find(".seg__opt--on").text(),
    ).toBe("Comfortable");
  });

  it("picking a size sets it on the store and saves it", async () => {
    const wrapper = mountSection();
    const store = useTagClipboardStore();
    const large = wrapper
      .find('[aria-label="Chip size"]')
      .findAll("button")
      .find((b) => b.text() === "Large");
    await large.trigger("click");
    await flushPromises();

    expect(store.chipSize).toBe("large");
    expect(patchUserConfig).toHaveBeenCalledWith({ tag_chip_size: "large" });
  });
});
