// The stats rail's Tasks tab, and the deep link that selects it.
//
// Both were invisible to the gate before: the tab's body could be deleted from
// this rail outright and 4600 tests stayed green, and the "View progress"
// action that selects it was wired through a component ref that only ever
// existed on the routes the grid owns.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const getPictureStats = vi.fn();
vi.mock("../../api/pictures", () => ({
  getPictureStats: (...args) => getPictureStats(...args),
}));

import StatsSidebar from "./StatsSidebar.vue";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-progress-circular": true,
      Tooltip: true,
      StatsHistogram: true,
    },
  },
};

async function flush(wrapper) {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  useSidebarStore().statsOpen = true;
  // Enough of a stats body that the Tags tab renders rather than erroring.
  getPictureStats.mockReset().mockResolvedValue({
    total: 0,
    tagged: 0,
    untagged: 0,
    avg_tags_per_image: 0,
    top_tags: [],
  });
});

describe("the stats rail's Tasks tab", () => {
  it("offers Tasks last and shows the task manager under it", async () => {
    const wrapper = mount(StatsSidebar, globalOpts);
    await flush(wrapper);
    const band = wrapper.findAll("button.inspector-tab");
    expect(band.map((t) => t.text())).toEqual(["Tags", "Pictures", "Tasks"]);

    await band[2].trigger("click");
    await flush(wrapper);
    expect(wrapper.text()).toContain("No active tasks");
    // Mounted, so it is the panel asking for the fast poll cadence.
    expect(useTasksStore().tasksTabOpen).toBe(true);

    await wrapper.findAll("button.inspector-tab")[0].trigger("click");
    await flush(wrapper);
    expect(wrapper.text()).not.toContain("No active tasks");
    expect(useTasksStore().tasksTabOpen).toBe(false);
  });

  it("follows the store's deep link onto the Tasks tab", async () => {
    // What the "View progress" notice action and the thumbnail banner do. The
    // request goes through the store because /workflows has a different
    // inspector in this rail, and a link that named this component reached
    // the tab everywhere except the screen runs are started from.
    const sidebarStore = useSidebarStore();
    sidebarStore.statsOpen = false;
    const wrapper = mount(StatsSidebar, globalOpts);
    await flush(wrapper);
    expect(wrapper.text()).not.toContain("No active tasks");

    sidebarStore.showTasksTab();
    await flush(wrapper);
    expect(sidebarStore.statsOpen).toBe(true);
    expect(wrapper.text()).toContain("No active tasks");
  });
});
