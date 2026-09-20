// The task manager's body, now that two inspectors mount it.
//
// The rows themselves moved out of StatsSidebar untouched, so what is worth
// asserting is what the move made possible to get wrong:
//
// * the fast poll cadence, which used to follow the selected tab and now
//   follows this component being mounted. Nothing asserted it before, and it
//   is the one behaviour the extraction deliberately changed;
// * that the panel renders each kind of entry at all, in a file that is not
//   also about tags or about workflows.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import TasksPanel, { tasksTabFor } from "./TasksPanel.vue";
import { useTasksStore } from "../../stores/useTasksStore";

const globalOpts = { global: { stubs: { "v-icon": true, Tooltip: true } } };

function textOf(wrapper) {
  return wrapper.text().replace(/\s+/g, " ");
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("the tab the hosts declare", () => {
  // Both inspectors take their descriptor from here, so this is the only place
  // its wording is decided — and the only place it can be wrong. Asserting the
  // tooltip as a hand-written prop on AppInspector tested the band, not this.
  it("counts in words, and says one task in the singular", () => {
    const tasksStore = useTasksStore();
    expect(tasksTabFor(tasksStore)).toEqual({
      value: "tasks",
      label: "Tasks",
      icon: "mdi-timeline-clock-outline",
    });

    tasksStore.setComfyuiRun("run-1", { label: "One" });
    expect(tasksTabFor(tasksStore)).toMatchObject({
      busy: true,
      busyTooltip: "1 active task",
    });

    tasksStore.setComfyuiRun("run-2", { label: "Two" });
    expect(tasksTabFor(tasksStore)).toMatchObject({
      busy: true,
      busyTooltip: "2 active tasks",
    });
  });
});

describe("the task manager's poll cadence", () => {
  // Mounted IS "the tab is on screen": an inspector unmounts its body when the
  // rail collapses, so this follows what is visible rather than which tab was
  // last picked. Nothing else in the app calls setTasksTabOpen.
  it("asks for the fast cadence while it is mounted, and gives it back", () => {
    const tasksStore = useTasksStore();
    const spy = vi.spyOn(tasksStore, "setTasksTabOpen");
    expect(tasksStore.tasksTabOpen).toBe(false);

    const wrapper = mount(TasksPanel, globalOpts);
    expect(spy).toHaveBeenCalledWith(true);
    expect(tasksStore.tasksTabOpen).toBe(true);

    wrapper.unmount();
    expect(spy).toHaveBeenLastCalledWith(false);
    expect(tasksStore.tasksTabOpen).toBe(false);
  });

  // The store counts panels rather than holding a flag: one host's unmount
  // must not drop the cadence back to idle under another host that is still
  // showing the panel. Not reachable while the two inspectors are `v-if` /
  // `v-else` siblings; the panel is shared on purpose, so it is asserted.
  it("stays fast while any panel is still mounted", () => {
    const tasksStore = useTasksStore();
    const first = mount(TasksPanel, globalOpts);
    const second = mount(TasksPanel, globalOpts);
    expect(tasksStore.tasksTabOpen).toBe(true);

    first.unmount();
    expect(tasksStore.tasksTabOpen).toBe(true);

    second.unmount();
    expect(tasksStore.tasksTabOpen).toBe(false);
  });
});

describe("what the task manager shows", () => {
  it("says so when there is nothing running", () => {
    const wrapper = mount(TasksPanel, globalOpts);
    expect(textOf(wrapper)).toContain("No active tasks");
    expect(wrapper.find(".tm-worker-row").exists()).toBe(false);
  });

  it("draws a ComfyUI run with its own progress and an abort", async () => {
    const tasksStore = useTasksStore();
    tasksStore.setComfyuiRun("run-1", {
      status: "running",
      percent: 40,
      message: "sampling step 4",
      label: "Cinematic portrait",
    });
    const wrapper = mount(TasksPanel, globalOpts);
    const text = textOf(wrapper);
    expect(text).not.toContain("No active tasks");
    expect(text).toContain("Cinematic portrait");
    expect(text).toContain("sampling step 4");
    expect(wrapper.find(".tm-comfy-fill").attributes("style")).toContain(
      "width: 40%",
    );

    const abort = wrapper.find('button[aria-label="Abort ComfyUI run"]');
    expect(abort.exists()).toBe(true);
    const aborted = vi.spyOn(tasksStore, "abortComfyuiRun");
    await abort.trigger("click");
    expect(aborted).toHaveBeenCalledWith("run-1");
  });

  it("gives an import row the FLIP target the import dialog flies into", () => {
    const tasksStore = useTasksStore();
    tasksStore.setImportRun("import-7", {
      status: "running",
      percent: 25,
      current: 5,
      total: 20,
      label: "Importing pictures",
      message: "Importing on the server…",
    });
    const wrapper = mount(TasksPanel, globalOpts);
    // ImageImporter finds the row by this attribute; a renamed one would land
    // the chip nowhere and the flight would read as "the import went missing".
    expect(wrapper.find('[data-import-task-row="import-7"]').exists()).toBe(
      true,
    );
    expect(textOf(wrapper)).toContain("5 / 20");
  });

  it("reads the machine out, and says n/a rather than a wrong number", () => {
    const tasksStore = useTasksStore();
    tasksStore.systemUsage = {
      cpu_percent_all_cores: 42,
      ram_used_gb: 12.3,
      ram_total_gb: 64,
      ram_percent: 19.2,
    };
    const wrapper = mount(TasksPanel, globalOpts);
    const items = wrapper
      .findAll(".tm-system-item")
      .map((i) => [
        i.find(".tm-system-label").text(),
        i.find(".tm-system-value").text(),
      ]);
    // No VRAM reading at all is a fact about the machine, not a zero.
    expect(items).toEqual([
      ["CPU", "42%"],
      ["RAM", "12.3 GB / 64.0 GB (19%)"],
      ["VRAM", "n/a"],
    ]);
  });
});
