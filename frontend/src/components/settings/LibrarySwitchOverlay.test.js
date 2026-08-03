import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

vi.mock("vuetify/components", () => ({
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VProgressCircular: { name: "v-progress-circular", template: "<i />" },
}));
vi.mock("../../api/libraries", () => ({ setActiveLibrary: vi.fn() }));
vi.mock("../../utils/reloadPage", () => ({ reloadPage: vi.fn() }));

import LibrarySwitchOverlay from "./LibrarySwitchOverlay.vue";
import { setActiveLibrary } from "../../api/libraries";
import { useLibrarySwitchStore } from "../../stores/useLibrariesStore";

let pinia;

beforeEach(() => {
  vi.clearAllMocks();
  pinia = createPinia();
  setActivePinia(pinia);
});

describe("LibrarySwitchOverlay", () => {
  it("is an assertive persistent surface and Escape cannot dismiss it", async () => {
    let rejectSwitch;
    setActiveLibrary.mockReturnValue(
      new Promise((resolve, reject) => {
        rejectSwitch = reject;
      }),
    );
    const wrapper = mount(LibrarySwitchOverlay, {
      global: { plugins: [pinia] },
    });
    const store = useLibrarySwitchStore();
    const begin = store.begin(
      { uuid: "b", name: "Client work" },
      { uuid: "a", name: "Family Photos" },
      null,
    );
    await nextTick();
    await nextTick();

    const dialog = wrapper.find('[role="alertdialog"]');
    expect(dialog.attributes("aria-live")).toBe("assertive");
    expect(wrapper.text()).toContain("Switching to Client work");
    await dialog.trigger("keydown", { key: "Escape" });
    expect(store.phase).toBe("switching");

    rejectSwitch({ response: { data: { detail: "Drive went away" } } });
    await begin;
  });

  it("failure names both libraries, offers one action, and restores row focus", async () => {
    setActiveLibrary.mockRejectedValue({
      response: { data: { detail: "Drive went away" } },
    });
    const trigger = document.createElement("button");
    document.body.append(trigger);
    const wrapper = mount(LibrarySwitchOverlay, {
      attachTo: document.body,
      global: { plugins: [pinia] },
    });
    const store = useLibrarySwitchStore();

    await store.begin(
      { uuid: "b", name: "Client work" },
      { uuid: "a", name: "Family Photos" },
      trigger,
    );
    await nextTick();

    expect(wrapper.text()).toContain("Could not switch to Client work");
    expect(wrapper.text()).toContain("still using Family Photos");
    expect(wrapper.text()).toContain("Drive went away");
    const buttons = wrapper.findAll("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].text()).toContain("Stay on Family Photos");

    await buttons[0].trigger("click");
    await nextTick();
    expect(store.phase).toBe("idle");
    expect(document.activeElement).toBe(trigger);

    wrapper.unmount();
    trigger.remove();
  });
});
