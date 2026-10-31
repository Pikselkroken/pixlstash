// The notice badge (#2417): a privacy fact the sentence does not carry, read
// out by its label.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i><slot /></i>" },
}));
vi.mock("./AppBarButton.vue", () => ({ default: { template: "<button />" } }));
vi.mock("./Tooltip.vue", () => ({
  default: { props: ["text"], template: '<span class="tip">{{ text }}</span>' },
}));

import NoticeHost from "./NoticeHost.vue";
import { useNoticeStore } from "../../stores/useNoticeStore";

beforeEach(() => setActivePinia(createPinia()));

describe("NoticeHost badge", () => {
  it("shows the badge with its label, only on the notice that has one", async () => {
    const store = useNoticeStore();
    store.success("Copied 2 files.", {
      timeout: 0,
      badge: { icon: "mdi-shield-alert-outline", label: "Workflow kept" },
    });
    store.info("Plain", { timeout: 0 });
    const wrapper = mount(NoticeHost);
    await wrapper.vm.$nextTick();

    const badges = wrapper.findAll(".notice-badge");
    expect(badges).toHaveLength(1);
    expect(badges[0].attributes("aria-label")).toBe("Workflow kept");
    expect(badges[0].text()).toContain("mdi-shield-alert-outline");
    expect(badges[0].find(".tip").text()).toBe("Workflow kept");
    expect(wrapper.findAll(".notice-card")[0].find(".notice-badge").exists()).toBe(
      true,
    );
  });
});
