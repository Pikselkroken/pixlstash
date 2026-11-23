// "Start a cluster from these…" in the two selection menus (#2738): the same
// row in both, greyed with its reason outside one person's view.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false),
    onSessionReset: () => () => {},
    sessionContext: ref(null),
  };
});

vi.mock("../../api/snapshots", () => ({
  hashCompareSnapshot: vi.fn(),
}));

import SelectionMenu from "./SelectionMenu.vue";
import ImageGridContextMenu from "../widgets/ImageGridContextMenu.vue";
import { seedClusterReason, seedPersonId } from "../../utils/seedCluster";

const stubs = {
  Tooltip: { props: ["text"], template: '<span class="tip">{{ text }}</span>' },
  "v-icon": { template: "<i><slot /></i>" },
  teleport: true,
  AddToEntityControl: true,
};

const MENUS = [
  {
    name: "SelectionMenu.vue",
    mountMenu: (props) =>
      mount(SelectionMenu, {
        props: { open: true, selectedCount: 2, selectedImageIds: ["10", "11"], ...props },
        global: { stubs },
      }),
  },
  {
    name: "ImageGridContextMenu.vue",
    mountMenu: (props) =>
      mount(ImageGridContextMenu, {
        props: {
          visible: true,
          selectedImageIds: ["10", "11"],
          scrapheapPicturesId: "SCRAPHEAP",
          selectedCharacter: "7",
          backendUrl: "http://x",
          ...props,
        },
        global: { stubs },
      }),
  },
];

async function entry(wrapper) {
  await wrapper.find('.ctx-submenu-wrap[data-group="find"]').trigger("mouseenter");
  return wrapper.find('[data-testid="start-cluster"]');
}

beforeEach(() => setActivePinia(createPinia()));

describe.each(MENUS)("$name", ({ mountMenu }) => {
  it("offers it in a person's view, named for them, and sends it", async () => {
    const wrapper = mountMenu({ seedClusterReason: null, seedPersonName: "Wren" });
    const row = await entry(wrapper);
    expect(row.text()).toContain("Start a cluster from these…");
    expect(row.find(".tip").text()).toBe("Group Wren's faces that look like these");
    expect(row.attributes("disabled")).toBeUndefined();
    expect(row.find(".ctx-meta").exists()).toBe(false);
    await row.trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted("start-cluster")).toHaveLength(1);
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("greys it with the reason as its tip", async () => {
    const reason = "Open a person to start a cluster from these";
    const row = await entry(mountMenu({ seedClusterReason: reason }));
    expect(row.attributes("disabled")).toBeDefined();
    expect(row.find(".tip").text()).toBe(reason);
    expect(row.find(".ctx-meta").text()).toBe("In a person");
  });
});

describe("seedCluster", () => {
  it("is a person's view only", () => {
    expect(seedPersonId("7")).toBe(7);
    expect(seedPersonId(7)).toBe(7);
    for (const view of ["ALL", "UNASSIGNED", "SCRAPHEAP", null, undefined, ""]) {
      expect(seedPersonId(view)).toBeNull();
    }
  });

  it("is greyed read-only, outside a person, and with nothing selected", () => {
    expect(seedClusterReason({ personId: 7, readOnly: false, count: 2 })).toBeNull();
    expect(seedClusterReason({ personId: 7, readOnly: true, count: 2 })).toBe(
      "Face clusters need owner access",
    );
    expect(seedClusterReason({ personId: null, readOnly: false, count: 2 })).toBe(
      "Open a person to start a cluster from these",
    );
    expect(seedClusterReason({ personId: 7, readOnly: false, count: 0 })).toBe(
      "Select pictures first",
    );
  });
});
