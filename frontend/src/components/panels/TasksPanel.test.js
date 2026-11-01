// The task manager's body, as the title bar's Tasks pill mounts it.
//
// The rows themselves moved out of StatsSidebar untouched, so what is worth
// asserting is what the move made possible to get wrong:
//
// * the fast poll cadence, which used to follow the selected tab and now
//   follows this component being mounted. Nothing asserted it before, and it
//   is the one behaviour the extraction deliberately changed;
// * that the panel renders each kind of entry at all, in a file that is not
//   also about tags or about workflows.

import { afterEach, describe, it, expect, beforeEach, vi } from "vitest";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { h } from "vue";
import { setActivePinia, createPinia } from "pinia";

const getWorkflowChanges = vi.fn();
const saveWorkflow = vi.fn();
const saveWorkflowAsNew = vi.fn();
const dismissFinishedRun = vi.fn();
const clearFinishedRuns = vi.fn();
// The rest of the module is the Workflows store's, which the panel only reads.
vi.mock("../../api/workflows", async (original) => ({
  ...(await original()),
  getWorkflowChanges: (...args) => getWorkflowChanges(...args),
  saveWorkflow: (...args) => saveWorkflow(...args),
  saveWorkflowAsNew: (...args) => saveWorkflowAsNew(...args),
  dismissFinishedRun: (...args) => dismissFinishedRun(...args),
  clearFinishedRuns: (...args) => clearFinishedRuns(...args),
}));
// Where a run's picture opens: the screen the panel is on.
const nav = vi.hoisted(() => ({
  route: { name: "all-pictures", query: { set: "4" } },
  push: null,
}));
vi.mock("vue-router", () => ({
  useRoute: () => nav.route,
  useRouter: () => ({ push: (...args) => nav.push(...args) }),
}));
const readOnly = vi.hoisted(() => ({ ref: null }));
vi.mock("../../utils/apiClient", async (original) => {
  const { ref: makeRef } = await import("vue");
  readOnly.ref = makeRef(false);
  return { ...(await original()), isReadOnly: readOnly.ref };
});

import TasksPanel from "./TasksPanel.vue";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { abortRun } from "../../api/comfyui";
import RunAnywayDialog from "./RunAnywayDialog.vue";

vi.mock("../../api/comfyui", () => ({ abortRun: vi.fn(async () => ({})) }));
const runLeaseAnyway = vi.fn(async () => ({ status: "success" }));
const cancelLease = vi.fn(async () => ({ status: "success" }));
vi.mock("../../api/resources", () => ({
  runLeaseAnyway: (...args) => runLeaseAnyway(...args),
  cancelLease: (...args) => cancelLease(...args),
  freeComfyui: vi.fn(async () => ({ status: "success", freed_mb: 0 })),
}));

const globalOpts = { global: { stubs: { "v-icon": true, Tooltip: true } } };

function textOf(wrapper) {
  return wrapper.text().replace(/\s+/g, " ");
}

// Every panel a test mounts is taken down again, its watchers with it.
enableAutoUnmount(afterEach);

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  readOnly.ref.value = false;
  getWorkflowChanges.mockReset();
  saveWorkflow.mockReset();
  saveWorkflowAsNew.mockReset();
  dismissFinishedRun.mockReset();
  clearFinishedRuns.mockReset();
  nav.route = { name: "all-pictures", query: { set: "4" } };
  nav.push = vi.fn();
});

describe("the task manager's poll cadence", () => {
  // Mounted IS "the task list is on screen": the popover unmounts it when it
  // closes, so this follows what is visible. Nothing else in the app calls
  // setTasksTabOpen.
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
  // showing the panel. Not reachable with one host; the panel is shareable on
  // purpose, so it is asserted.
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

  // #1862: the runs are what the backend's poll reported and nothing else, so
  // the row is there with no view mounted to follow them - a run another tab
  // or MCP started included.
  it("draws each run the backend reports as a row of its own, the running one pulsing", async () => {
    const tasksStore = useTasksStore();
    tasksStore.comfyuiRuns = [
      { prompt_id: "p-1", label: "Night market", status: "queued", percent: 0 },
      {
        prompt_id: "p-2",
        label: "Cinematic portrait",
        status: "running",
        percent: 40,
      },
      { prompt_id: "p-3", label: "Upscale", status: "queued", percent: 0 },
    ];
    const wrapper = mount(TasksPanel, globalOpts);
    const text = textOf(wrapper);
    expect(text).not.toContain("No active tasks");
    // One row per run, not one row for them all.
    const rows = wrapper.findAll('[data-testid="tm-comfy-row"]');
    expect(rows).toHaveLength(3);
    expect(rows.map((row) => row.find(".tm-name").text())).toEqual([
      "Night market",
      "Cinematic portrait",
      "Upscale",
    ]);
    // Only the run on the GPU pulses; the others are queued, a plain dot.
    expect(rows.map((row) => row.find(".tm-mark").classes("tm-mark--running"))).toEqual([
      false,
      true,
      false,
    ]);

    // The stop asks the backend, not a handler some view registered.
    const stop = wrapper.find('button[aria-label="Stop ComfyUI"]');
    expect(stop.exists()).toBe(true);
    expect(abortRun).not.toHaveBeenCalled();
    await stop.trigger("click");
    expect(abortRun).toHaveBeenCalledTimes(1);
  });

  // A row leaves with its run, and a focused Stop leaves with it: focus falls
  // to <body>, out of the tab order. The host is the title bar's popover, a
  // dialog, so the panel hands the keyboard back to it.
  function inPopover() {
    const tasksStore = useTasksStore();
    tasksStore.comfyuiRuns = [
      { prompt_id: "p-1", label: "Night market", status: "running", percent: 40 },
    ];
    const wrapper = mount(
      {
        render: () =>
          h("div", { role: "dialog", tabindex: "-1" }, [h(TasksPanel)]),
      },
      { ...globalOpts, attachTo: document.body },
    );
    const stop = wrapper.find('button[aria-label="Stop ComfyUI"]').element;
    stop.focus();
    return { tasksStore, wrapper, stop, tab: wrapper.find('[role="dialog"]').element };
  }

  it("hands focus to its popover when the focused control leaves with its row", async () => {
    const { tasksStore, stop, tab } = inPopover();
    tasksStore.comfyuiRuns = [];
    // Chromium fires this as the button is removed; jsdom does not, so the
    // test fires it, before the render that removes the row.
    stop.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    await flushPromises();
    expect(stop.isConnected).toBe(false);
    expect(document.activeElement).toBe(tab);
  });

  it("leaves focus alone when the control is still there", async () => {
    const { stop, tab } = inPopover();
    stop.blur();
    await flushPromises();
    expect(document.activeElement).not.toBe(tab);
  });

  // The panel's own hand-off (a save's receipt line takes focus as its offer
  // leaves) is not focus lost.
  it("leaves focus alone when the row left but focus already went somewhere", async () => {
    const { tasksStore, stop } = inPopover();
    const elsewhere = document.createElement("button");
    document.body.appendChild(elsewhere);
    tasksStore.comfyuiRuns = [];
    stop.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    elsewhere.focus();
    await flushPromises();
    expect(stop.isConnected).toBe(false);
    expect(document.activeElement).toBe(elsewhere);
    elsewhere.remove();
  });

  it("shows a queued run as a row with no figure, and no pulse", () => {
    const tasksStore = useTasksStore();
    tasksStore.comfyuiRuns = [
      { prompt_id: "p-1", label: "Night market", status: "queued", percent: 0 },
    ];
    const wrapper = mount(TasksPanel, globalOpts);
    const row = wrapper.find('[data-testid="tm-comfy-row"]');
    expect(row.find(".tm-name").text()).toBe("Night market");
    expect(row.find(".tm-mark").classes("tm-mark--running")).toBe(false);
    expect(row.find(".tm-fig-text").text()).toBe("");
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
    // The figures are the server's own, so the heading says whose.
    expect(wrapper.find(".tm-system-heading").text()).toBe(
      "PixlStash resource usage",
    );
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

// ── Save what a run changed (#1850) ──────────────────────────────────────
//
// The Run popup's two verbs again, after the pictures are back. What can go
// wrong quietly: an offer drawn from this tab's own memory of a run when the
// server no longer holds one (saved elsewhere, run again), a save sent that
// is not the set the run was made with, and a row that takes the keyboard
// away with it.
describe("the offer to save what a run changed", () => {
  const WF = `auto:${"a".repeat(64)}`;
  const OTHER = `auto:${"b".repeat(64)}`;
  const RAN = {
    models: [{ address: "core:CheckpointLoader/ckpt_name", filename: "juggernautXL.safetensors" }],
    loras: [],
    skip_loras: [{ node_id: "8", field: "lora_name" }],
    stages: {},
    values: [{ slot_label: "KSampler", input_name: "steps", value: 32 }],
  };
  const OPEN = { open: true, reason: null };
  const state = (overrides = {}) => ({
    workflow_id: WF,
    version: 3,
    waiting: null,
    count: 0,
    ran: RAN,
    save: { open: false, reason: "Nothing to save." },
    save_as_new: OPEN,
    ran_save: OPEN,
    ...overrides,
  });
  const record = {
    name: "Portrait, soft window light",
    count: 3,
    text: "Checkpoint, 1 LoRA removed, Steps.",
    promptIds: ["p-1"],
  };
  /** One finished run, as the poll's `finished` lists it. */
  const finishedRun = (overrides = {}) => ({
    id: `run-${WF}`,
    workflow_id: WF,
    label: record.name,
    ended_at: new Date().toISOString(),
    status: "completed",
    message: null,
    made: 2,
    picture_ids: [11, 12],
    offer: true,
    ...overrides,
  });
  const offer = (wrapper, id = WF) => wrapper.find(`[data-offer="${id}"]`);
  const button = (wrapper, text) =>
    wrapper.findAll("button").find((b) => b.text().trim() === text);
  const refusal = (status, detail) => ({ response: { status, data: { detail } } });

  async function mountPanel() {
    const wrapper = mount(TasksPanel, {
      ...globalOpts,
      attachTo: document.body,
    });
    await flushPromises();
    return wrapper;
  }

  /**
   * The poll lists *run* as finished, after a poll that did not: it landed
   * while the app was open, so a run with an offer opens by itself.
   */
  function lands(run) {
    const tasks = useTasksStore();
    if (!tasks.finishedRuns.length) tasks.noteFinished([]);
    tasks.noteFinished([...tasks.finishedRuns, run]);
  }

  /** A changed run of a workflow, queued from the popup and finished. */
  function ranAndFinished(workflowId = WF, name = record.name) {
    const runDialog = useRunDialogStore();
    const id = `run-${workflowId}`;
    runDialog.ranWith(workflowId, { ...record, name, runIds: [id] });
    lands(finishedRun({ id, workflow_id: workflowId, label: name }));
    return runDialog;
  }
  const doneLine = (wrapper) => wrapper.find('[data-testid="tm-offer-done"]');
  const nameInput = (wrapper, id = WF) => offer(wrapper, id).find("input");

  /** Two workflows, each with a changed run that finished: WF's offer first. */
  function twoRuns() {
    ranAndFinished(WF);
    ranAndFinished(OTHER, "Night market");
    return mountPanel();
  }

  /** Leave the next Save out: what this answers is called with lands it. */
  function holdNextSave() {
    let land;
    saveWorkflow.mockImplementation(() => new Promise((resolve) => (land = resolve)));
    return (answer) => land(answer);
  }

  beforeEach(() => {
    getWorkflowChanges.mockResolvedValue(state());
    saveWorkflow.mockResolvedValue({ workflow_id: WF, version: 4 });
  });

  it("asks nothing and offers nothing where no run changed anything", async () => {
    lands(finishedRun({ offer: false }));
    const wrapper = await mountPanel();
    expect(getWorkflowChanges).not.toHaveBeenCalled();
    expect(offer(wrapper).exists()).toBe(false);
    expect(wrapper.find('[data-testid="tm-finished-pill"]').exists()).toBe(false);
    // A receipt: its count, and opened, when it finished.
    expect(textOf(wrapper.find('[data-testid="tm-finished-run"]'))).toContain(
      "Portrait, soft window light 2",
    );
    await wrapper.find('[data-testid="tm-finished-caret"]').trigger("click");
    expect(textOf(wrapper)).toContain("Finished just now.");
  });

  it("waits for the run's pictures, then says what it changed in the band's words", async () => {
    const runDialog = useRunDialogStore();
    runDialog.ranWith(WF, { ...record, runIds: [`run-${WF}`] });
    useTasksStore().noteFinished([]);
    const wrapper = await mountPanel();
    // Queued, not finished: nothing to decide yet.
    expect(getWorkflowChanges).not.toHaveBeenCalled();
    expect(offer(wrapper).exists()).toBe(false);

    lands(finishedRun());
    await flushPromises();
    expect(getWorkflowChanges).toHaveBeenCalledWith(WF);
    // Opened by itself, once, as it landed: its pictures, when, and what.
    expect(textOf(offer(wrapper))).toContain(
      "Ran just now with 3 changes from the workflow: Checkpoint, 1 LoRA removed, Steps.",
    );
    expect(wrapper.findAll('[data-testid="tm-finished-tile"]')).toHaveLength(2);
    expect(wrapper.find('[data-testid="tm-finished-pill"]').text()).toBe("3 changes");
    expect(textOf(offer(wrapper))).toContain("Portrait, soft window light");
    expect(button(wrapper, "Save")).toBeTruthy();
    expect(button(wrapper, "Save as new…")).toBeTruthy();
    // The panel is not empty with an offer in it.
    expect(textOf(wrapper)).not.toContain("No active tasks");
  });

  it("draws nothing the server does not hold, whatever this tab remembers", async () => {
    // Saved from another tab, or the workflow ran again from the grid.
    getWorkflowChanges.mockResolvedValue(state({ ran: null, ran_save: null }));
    ranAndFinished();
    const wrapper = await mountPanel();
    expect(getWorkflowChanges).toHaveBeenCalledWith(WF);
    expect(offer(wrapper).exists()).toBe(false);
    expect(wrapper.find('[data-testid="tm-finished-pill"]').exists()).toBe(false);
  });

  it("offers what it could read when one workflow's answer does not come", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    getWorkflowChanges.mockImplementation((id) =>
      id === WF ? Promise.reject(new Error("Network Error")) : Promise.resolve(state()),
    );
    const wrapper = await twoRuns();
    // Not knowing is not an offer, and it does not cost the other its own.
    expect(offer(wrapper, WF).exists()).toBe(false);
    expect(textOf(offer(wrapper, OTHER))).toContain("Night market");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("comes back folded after a reload, and opened reads with a count", async () => {
    const workflows = useWorkflowsStore();
    workflows.cards = [{ id: WF, name: "Portrait, soft window light" }];
    // The first poll already lists it: nothing landed while the app was open.
    useTasksStore().noteFinished([finishedRun({ label: "Krea 2: Text to Image" })]);
    const wrapper = await mountPanel();
    expect(getWorkflowChanges).toHaveBeenCalledWith(WF);
    expect(offer(wrapper).find(".tm-detail").exists()).toBe(false);
    // Folded, the pill is what says the changes are still unsaved.
    expect(wrapper.find('[data-testid="tm-finished-pill"]').text()).toBe("3 changes");
    await wrapper.find('[data-testid="tm-finished-caret"]').trigger("click");
    // The labels were the popup's: width and height apart, no Size, no names.
    expect(textOf(offer(wrapper))).toContain("Ran just now with 3 changes from the workflow.");
    expect(textOf(offer(wrapper))).not.toContain("Checkpoint");
    expect(offer(wrapper).attributes("aria-label")).toContain(
      "Portrait, soft window light",
    );
  });

  it("words an offer from the record only of the run it is", async () => {
    const runDialog = useRunDialogStore();
    // The popup's record is of a later run of the workflow.
    runDialog.ranWith(WF, { ...record, runIds: ["run-later"] });
    lands(finishedRun());
    const wrapper = await mountPanel();
    expect(textOf(offer(wrapper))).toContain("Ran just now with 3 changes from the workflow.");
    expect(textOf(offer(wrapper))).not.toContain("Checkpoint");
  });

  it("saves exactly what the run was made with, as a save from a run", async () => {
    const runDialog = ranAndFinished();
    const workflows = useWorkflowsStore();
    const reread = vi.spyOn(workflows, "invalidate");
    const wrapper = await mountPanel();
    await button(wrapper, "Save").trigger("click");
    await flushPromises();

    expect(saveWorkflow).toHaveBeenCalledWith(WF, {
      changes: RAN,
      version: 3,
      from_run: true,
    });
    // The offer goes, said where it was, with the keyboard on that line.
    expect(offer(wrapper).exists()).toBe(false);
    const done = doneLine(wrapper);
    expect(done.text()).toBe("Saved to Portrait, soft window light.");
    expect(document.activeElement).toBe(done.element);
    expect(runDialog.ranChanges[WF]).toBeUndefined();
    expect(runDialog.saved).toMatchObject({ workflowId: WF, savedAs: "" });
    expect(reread).toHaveBeenCalled();
  });

  it("does not redraw an offer from an answer asked before the save", async () => {
    ranAndFinished();
    const workflows = useWorkflowsStore();
    const wrapper = await mountPanel();
    const answer = [];
    getWorkflowChanges.mockImplementation(
      () => new Promise((resolve) => answer.push(resolve)),
    );
    // The grid is read again: a second asking, still out when Save is pressed.
    workflows.cards = [{ id: WF, name: record.name }];
    await flushPromises();
    await button(wrapper, "Save").trigger("click");
    await flushPromises();
    // Nothing is asked after the save: the run no longer offers anything.
    expect(answer).toHaveLength(1);
    answer[0](state());
    await flushPromises();
    expect(offer(wrapper).exists()).toBe(false);
    expect(doneLine(wrapper).text()).toBe("Saved to Portrait, soft window light.");
  });

  it("sends one save at a time, whichever Save is pressed while it is out", async () => {
    const land = holdNextSave();
    const wrapper = await twoRuns();
    const save = button(offer(wrapper, WF), "Save");
    // Twice before the button has been drawn busy, then the other row's.
    save.trigger("click");
    save.trigger("click");
    await flushPromises();
    // No verb of either row looks live while it would be dropped.
    const closed = (id, text) => button(offer(wrapper, id), text).attributes("disabled");
    for (const id of [WF, OTHER]) {
      expect(closed(id, "Save")).toBeDefined();
      expect(closed(id, "Save as new…")).toBeDefined();
    }
    await button(offer(wrapper, OTHER), "Save").trigger("click");
    await flushPromises();
    expect(saveWorkflow).toHaveBeenCalledTimes(1);
    expect(saveWorkflow.mock.calls[0][0]).toBe(WF);

    land({ workflow_id: WF, version: 4 });
    await flushPromises();
    expect(offer(wrapper, WF).exists()).toBe(false);
    expect(closed(OTHER, "Save")).toBeUndefined();
    expect(closed(OTHER, "Save as new…")).toBeUndefined();
  });

  it("closes a row's name question while another row's save is out", async () => {
    const land = holdNextSave();
    const wrapper = await twoRuns();
    await button(offer(wrapper, WF), "Save as new…").trigger("click");
    await flushPromises();
    await button(offer(wrapper, OTHER), "Save").trigger("click");
    await flushPromises();

    const asking = offer(wrapper, WF);
    const name = asking.find("input");
    expect(button(asking, "Back").attributes("disabled")).toBeDefined();
    expect(button(asking, "Save as new workflow").attributes("disabled")).toBeDefined();
    expect(name.attributes("readonly")).toBeDefined();
    // The keys wait as the buttons do: no second write, and no leaving.
    await name.trigger("keydown", { key: "Enter" });
    await name.trigger("keydown", { key: "Escape" });
    await flushPromises();
    expect(saveWorkflowAsNew).not.toHaveBeenCalled();
    expect(nameInput(wrapper, WF).exists()).toBe(true);

    // The other row's save landing does not answer this row's question.
    land({ workflow_id: OTHER, version: 4 });
    await flushPromises();
    expect(offer(wrapper, OTHER).exists()).toBe(false);
    expect(name.element.value).toBe("Portrait, soft window light 2");
    expect(button(asking, "Back").attributes("disabled")).toBeUndefined();
    expect(button(asking, "Save as new workflow").attributes("disabled")).toBeUndefined();
    expect(name.attributes("readonly")).toBeUndefined();
  });

  it("prints the server's reason for a closed Save and sends nothing", async () => {
    const reason = "A checkpoint change is saved as a new workflow.";
    getWorkflowChanges.mockResolvedValue(state({ ran_save: { open: false, reason } }));
    ranAndFinished();
    const wrapper = await mountPanel();
    const save = button(wrapper, "Save");
    const why = offer(wrapper).find(".tm-offer-why");
    expect(why.text()).toBe(reason);
    expect(save.attributes("disabled")).toBeUndefined();
    expect(save.attributes("aria-disabled")).toBe("true");
    expect(save.attributes("aria-describedby")).toBe(why.attributes("id"));
    await save.trigger("click");
    await flushPromises();
    expect(saveWorkflow).not.toHaveBeenCalled();
    // Save as new… is always open.
    expect(button(wrapper, "Save as new…").attributes("aria-disabled")).toBeUndefined();
  });

  it("asks for a name for Save as new…, and Back gives the two verbs back", async () => {
    ranAndFinished();
    const wrapper = await mountPanel();
    await button(wrapper, "Save as new…").trigger("click");
    await flushPromises();
    const name = nameInput(wrapper);
    expect(document.activeElement).toBe(name.element);
    expect(name.element.value).toBe("Portrait, soft window light 2");
    expect(name.element.value).not.toMatch(/copy|clone|fixed|edited/i);
    expect(button(wrapper, "Save")).toBeUndefined();

    await button(wrapper, "Back").trigger("click");
    await flushPromises();
    expect(nameInput(wrapper).exists()).toBe(false);
    expect(document.activeElement).toBe(button(wrapper, "Save as new…").element);
    expect(saveWorkflowAsNew).not.toHaveBeenCalled();
  });

  // Two rows, one name question between them. Rows are patched in order, so
  // the row that starts asking is mounted before or after the row that stops,
  // depending on which is higher: the keyboard goes to the asking one's field
  // either way.
  it.each([
    ["an earlier row while a later one is asking", OTHER, WF],
    ["a later row while an earlier one is asking", WF, OTHER],
  ])("puts the keyboard in the name field of %s", async (_name, first, second) => {
    const wrapper = await twoRuns();
    expect(
      wrapper.findAll("[data-offer]").map((row) => row.attributes("data-offer")),
    ).toEqual([WF, OTHER]);
    await button(offer(wrapper, first), "Save as new…").trigger("click");
    await flushPromises();
    expect(document.activeElement).toBe(nameInput(wrapper, first).element);

    await button(offer(wrapper, second), "Save as new…").trigger("click");
    await flushPromises();
    expect(nameInput(wrapper, first).exists()).toBe(false);
    const name = nameInput(wrapper, second).element;
    expect(document.activeElement).toBe(name);
    // Selected, to be typed over.
    expect([name.selectionStart, name.selectionEnd]).toEqual([0, name.value.length]);
  });

  it("takes Enter in the name field as the save, and Escape as Back", async () => {
    saveWorkflowAsNew.mockResolvedValue({ workflow_id: OTHER, new_workflow: true });
    ranAndFinished();
    const wrapper = await mountPanel();
    await button(wrapper, "Save as new…").trigger("click");
    await flushPromises();
    await nameInput(wrapper).trigger("keydown", { key: "Escape" });
    await flushPromises();
    expect(nameInput(wrapper).exists()).toBe(false);
    expect(document.activeElement).toBe(button(wrapper, "Save as new…").element);

    await button(wrapper, "Save as new…").trigger("click");
    await flushPromises();
    const name = nameInput(wrapper);
    // Twice before the field has been drawn busy: one new workflow, not two.
    name.trigger("keydown", { key: "Enter" });
    name.trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(saveWorkflowAsNew).toHaveBeenCalledTimes(1);
    expect(saveWorkflowAsNew).toHaveBeenCalledWith(WF, {
      name: "Portrait, soft window light 2",
      changes: RAN,
      from_run: true,
    });
    expect(doneLine(wrapper).text()).toBe("Saved as Portrait, soft window light 2.");
  });

  it("stores the run's changes as a new workflow under the name given", async () => {
    saveWorkflowAsNew.mockResolvedValue({
      workflow_id: OTHER,
      name: "Portrait on Juggernaut",
      new_workflow: true,
    });
    const runDialog = ranAndFinished();
    const wrapper = await mountPanel();
    await button(wrapper, "Save as new…").trigger("click");
    await flushPromises();
    await nameInput(wrapper).setValue(" Portrait on Juggernaut ");
    await button(wrapper, "Save as new workflow").trigger("click");
    await flushPromises();

    expect(saveWorkflowAsNew).toHaveBeenCalledWith(WF, {
      name: "Portrait on Juggernaut",
      changes: RAN,
      from_run: true,
    });
    expect(saveWorkflow).not.toHaveBeenCalled();
    expect(doneLine(wrapper).text()).toBe("Saved as Portrait on Juggernaut.");
    expect(runDialog.saved).toMatchObject({ workflowId: WF, savedAs: OTHER });
  });

  it("sends no new workflow without a name", async () => {
    ranAndFinished();
    const wrapper = await mountPanel();
    await button(wrapper, "Save as new…").trigger("click");
    await flushPromises();
    await nameInput(wrapper).setValue("  ");
    await button(wrapper, "Save as new workflow").trigger("click");
    await flushPromises();
    expect(saveWorkflowAsNew).not.toHaveBeenCalled();
  });

  it("keeps the offer and prints the server's sentence when a save is refused", async () => {
    saveWorkflow.mockRejectedValue(
      refusal(409, "This workflow got a newer version since these changes were made."),
    );
    const runDialog = ranAndFinished();
    const wrapper = await mountPanel();
    await button(wrapper, "Save").trigger("click");
    await flushPromises();
    expect(offer(wrapper).find('[role="alert"]').text()).toBe(
      "This workflow got a newer version since these changes were made.",
    );
    expect(runDialog.ranChanges[WF]).toBeDefined();
    expect(runDialog.saved).toBeNull();
  });

  it("keeps asking for the name and prints the server's sentence when Save as new is refused", async () => {
    saveWorkflowAsNew.mockRejectedValue(
      refusal(409, "A checkpoint this machine does not have cannot be saved."),
    );
    const runDialog = ranAndFinished();
    const wrapper = await mountPanel();
    await button(wrapper, "Save as new…").trigger("click");
    await flushPromises();
    await nameInput(wrapper).setValue("Portrait on Juggernaut");
    await button(wrapper, "Save as new workflow").trigger("click");
    await flushPromises();
    expect(offer(wrapper).find('[role="alert"]').text()).toBe(
      "A checkpoint this machine does not have cannot be saved.",
    );
    // Still the question, with what was typed, and open to be sent again.
    const name = nameInput(wrapper);
    expect(name.element.value).toBe("Portrait on Juggernaut");
    expect(name.attributes("disabled")).toBeUndefined();
    expect(doneLine(wrapper).exists()).toBe(false);
    expect(runDialog.ranChanges[WF]).toBeDefined();
    expect(runDialog.saved).toBeNull();
  });

  // Enter leaves the keyboard in the name field, so the field must be able
  // to keep it while the write is out. jsdom does not take focus from a field
  // that is disabled; a browser does, so what is asserted is that it never is.
  it("keeps the keyboard in the name field through a Save as new that is refused", async () => {
    let refuse;
    saveWorkflowAsNew.mockImplementation(
      () => new Promise((_resolve, reject) => (refuse = reject)),
    );
    ranAndFinished();
    const wrapper = await mountPanel();
    await button(wrapper, "Save as new…").trigger("click");
    await flushPromises();
    const name = nameInput(wrapper);
    await name.trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(saveWorkflowAsNew).toHaveBeenCalledTimes(1);
    expect(name.attributes("disabled")).toBeUndefined();
    expect(name.attributes("readonly")).toBeDefined();
    expect(document.activeElement).toBe(name.element);
    // Escape waits for the write, as Back does.
    await name.trigger("keydown", { key: "Escape" });
    await flushPromises();
    expect(nameInput(wrapper).exists()).toBe(true);

    refuse(refusal(409, "A checkpoint this machine does not have cannot be saved."));
    await flushPromises();
    // Said by the alert, with the keyboard where the answer is typed.
    expect(offer(wrapper).find('[role="alert"]').text()).toBe(
      "A checkpoint this machine does not have cannot be saved.",
    );
    expect(document.activeElement).toBe(name.element);
    expect(name.attributes("readonly")).toBeUndefined();
  });

  it("says ComfyUI did not answer on a 503", async () => {
    saveWorkflow.mockRejectedValue(refusal(503, "something upstream"));
    ranAndFinished();
    const wrapper = await mountPanel();
    await button(wrapper, "Save").trigger("click");
    await flushPromises();
    expect(offer(wrapper).find('[role="alert"]').text()).toContain(
      "ComfyUI did not answer, so nothing was saved.",
    );
  });

  // The line is the receipt of the last save made here. What it must never
  // do is sit beside something newer as if it were the news.
  it("leaves a save's line beside an offer that was there before it, and a later save writes over it", async () => {
    const wrapper = await twoRuns();
    const lines = () =>
      wrapper.findAll('[data-testid="tm-offer-done"]').map((line) => line.text());
    await button(offer(wrapper, WF), "Save").trigger("click");
    await flushPromises();
    expect(lines()).toEqual(["Saved to Portrait, soft window light."]);
    expect(offer(wrapper, OTHER).exists()).toBe(true);

    await button(offer(wrapper, OTHER), "Save").trigger("click");
    await flushPromises();
    expect(lines()).toEqual(["Saved to Night market."]);
    expect(document.activeElement).toBe(doneLine(wrapper).element);
  });

  it("takes a save's line down when an offer arrives after it, and hands on the keyboard it held", async () => {
    ranAndFinished();
    const wrapper = await mountPanel();
    await button(wrapper, "Save").trigger("click");
    await flushPromises();
    expect(document.activeElement).toBe(doneLine(wrapper).element);

    // Another workflow's run comes back with changes of its own.
    ranAndFinished(OTHER, "Night market");
    await flushPromises();
    expect(offer(wrapper, OTHER).exists()).toBe(true);
    expect(doneLine(wrapper).exists()).toBe(false);
    // Not left on a line that is gone, with the page to tab through again.
    expect(document.activeElement).toBe(button(wrapper, "Save as new…").element);
  });

  it("leaves the keyboard alone when the line it takes down did not hold it", async () => {
    ranAndFinished();
    const wrapper = await mountPanel();
    await button(wrapper, "Save").trigger("click");
    await flushPromises();
    doneLine(wrapper).element.blur();

    ranAndFinished(OTHER, "Night market");
    await flushPromises();
    expect(doneLine(wrapper).exists()).toBe(false);
    expect(document.activeElement).toBe(document.body);
  });

  // A save from a run writes what the run used. What was changed in the
  // Workflow tab after the run is either still waiting there or, where the
  // saved workflow cannot take it, gone. The server says which once, so a
  // receipt that says "Saved" and no more reads as the save having lost it.
  describe("what a save left of the changes made after the run", () => {
    // A model for a loader that is gone, and a LoRA set on one the run removed.
    const NOT_KEPT = {
      models: [{ address: "core:CheckpointLoader/ckpt_name", filename: "dreamshaper.safetensors" }],
      loras: [{ node_id: "8", field: "lora_name", sha256: "c".repeat(64), strength_model: 0.8 }],
      skip_loras: [],
      stages: {},
      values: [],
    };
    const RECEIPT = "Saved to Portrait, soft window light.";
    const LOST =
      "Could not keep 2 changes made after the run: Checkpoint, 1 LoRA changed. The saved workflow no longer has what was changed, so this is gone: not saved, and not left waiting.";
    const waiting = (count) =>
      `Portrait, soft window light still has ${count} waiting in the Workflow tab. They were not part of this save.`;
    const lostLine = (wrapper) => wrapper.find(".tm-offer-lost");

    async function saved(answer) {
      saveWorkflow.mockResolvedValue({ workflow_id: WF, version: 4, ...answer });
      ranAndFinished();
      const wrapper = await mountPanel();
      await button(wrapper, "Save").trigger("click");
      await flushPromises();
      return wrapper;
    }

    it("says how many still wait in the Workflow tab, and that the save was not of them", async () => {
      const wrapper = await saved({ still_waiting: 2, not_kept: null });
      expect(textOf(doneLine(wrapper))).toBe(`${RECEIPT} ${waiting("2 changes")}`);
      expect(lostLine(wrapper).exists()).toBe(false);
    });

    it("names what could not be kept, as lost, and says why", async () => {
      const wrapper = await saved({ still_waiting: 0, not_kept: NOT_KEPT });
      expect(lostLine(wrapper).text()).toBe(LOST);
      expect(textOf(doneLine(wrapper))).toBe(`${RECEIPT} ${LOST}`);
      // Said on the line, so not a second time on a card.
      expect(useNoticeStore().notices).toEqual([]);
    });

    it("says both in the line that takes the keyboard, what was lost first", async () => {
      const wrapper = await saved({ still_waiting: 1, not_kept: NOT_KEPT });
      const done = doneLine(wrapper);
      expect(textOf(done)).toBe(`${RECEIPT} ${LOST} ${waiting("1 change")}`);
      // Read where focus lands: inside the line, not beside it.
      expect(document.activeElement).toBe(done.element);
      expect(done.element.contains(lostLine(wrapper).element)).toBe(true);

      // The line goes as it always did, with all it said, and hands focus on.
      ranAndFinished(OTHER, "Night market");
      await flushPromises();
      expect(doneLine(wrapper).exists()).toBe(false);
      expect(textOf(wrapper)).not.toContain("Could not keep");
      expect(textOf(wrapper)).not.toContain("still has");
      expect(document.activeElement).toBe(button(wrapper, "Save as new…").element);
    });

    it.each([
      ["nothing waits and nothing was lost", { still_waiting: 0, not_kept: null }],
      ["the answer is an older server's", {}],
      ["what was not kept is an empty set", { not_kept: { ...NOT_KEPT, models: [], loras: [] } }],
    ])("leaves the receipt as it was when %s", async (_name, answer) => {
      const wrapper = await saved(answer);
      const done = doneLine(wrapper);
      expect(done.text()).toBe(RECEIPT);
      expect(done.element.children).toHaveLength(0);
      expect(document.activeElement).toBe(done.element);
    });

    it("says after Save as new that they wait on the workflow saved from, not the new one", async () => {
      saveWorkflowAsNew.mockResolvedValue({
        workflow_id: OTHER,
        name: "Portrait on Juggernaut",
        new_workflow: true,
        still_waiting: 2,
        not_kept: null,
      });
      ranAndFinished();
      const wrapper = await mountPanel();
      await button(wrapper, "Save as new…").trigger("click");
      await flushPromises();
      await nameInput(wrapper).setValue("Portrait on Juggernaut");
      await button(wrapper, "Save as new workflow").trigger("click");
      await flushPromises();
      const done = doneLine(wrapper);
      expect(textOf(done)).toBe(`Saved as Portrait on Juggernaut. ${waiting("2 changes")}`);
      expect(document.activeElement).toBe(done.element);
    });

    // The server says what it dropped once, in this answer. A tab closed
    // while the save was out has no line to say it on.
    it("says what was lost on a notice that stays when the tab closed before the save landed", async () => {
      const land = holdNextSave();
      ranAndFinished();
      const wrapper = await mountPanel();
      await button(wrapper, "Save").trigger("click");
      wrapper.unmount();
      land({ workflow_id: WF, version: 4, still_waiting: 1, not_kept: NOT_KEPT });
      await flushPromises();
      expect(useNoticeStore().notices).toMatchObject([
        { level: "warning", text: `${RECEIPT} ${LOST}`, timeout: 0 },
      ]);
    });

    it("raises no notice from a closed tab for a save that lost nothing", async () => {
      const land = holdNextSave();
      ranAndFinished();
      const wrapper = await mountPanel();
      await button(wrapper, "Save").trigger("click");
      wrapper.unmount();
      land({ workflow_id: WF, version: 4, still_waiting: 1, not_kept: null });
      await flushPromises();
      expect(useNoticeStore().notices).toEqual([]);
    });
  });

  it("takes the offer down when the workflow changes somewhere else", async () => {
    ranAndFinished();
    const workflows = useWorkflowsStore();
    const wrapper = await mountPanel();
    expect(offer(wrapper).exists()).toBe(true);
    // A save from another tab is announced, and the grid is read again.
    getWorkflowChanges.mockResolvedValue(state({ ran: null, ran_save: null }));
    workflows.cards = [{ id: WF, name: "Portrait, soft window light" }];
    await flushPromises();
    expect(offer(wrapper).exists()).toBe(false);
  });

  it("offers nothing, and asks nothing, in a read-only session", async () => {
    readOnly.ref.value = true;
    ranAndFinished();
    const wrapper = await mountPanel();
    expect(getWorkflowChanges).not.toHaveBeenCalled();
    expect(offer(wrapper).exists()).toBe(false);
  });
});

// ── The Finished group (#2050) ───────────────────────────────────────────────

describe("the Finished group", () => {
  const WF = `auto:${"c".repeat(64)}`;
  const RAN = { models: [], loras: [], skip_loras: [{ node_id: "8", field: "lora_name" }], stages: {}, values: [] };
  const run = (overrides = {}) => ({
    id: "run-a",
    workflow_id: WF,
    label: "Portrait SDXL",
    ended_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
    status: "completed",
    message: null,
    made: 4,
    picture_ids: [21, 22, 23],
    offer: false,
    ...overrides,
  });
  const rows = (wrapper) => wrapper.findAll('[data-testid="tm-finished-run"]');
  const within = (row, testid) => row.find(`[data-testid="${testid}"]`);

  async function mountWith(list, { landed = false } = {}) {
    const tasks = useTasksStore();
    if (landed) tasks.noteFinished([]);
    tasks.noteFinished(list);
    const wrapper = mount(TasksPanel, { ...globalOpts, attachTo: document.body });
    await flushPromises();
    return wrapper;
  }

  beforeEach(() => {
    getWorkflowChanges.mockResolvedValue({
      workflow_id: WF,
      version: 2,
      ran: RAN,
      ran_save: { open: true, reason: null },
    });
    dismissFinishedRun.mockResolvedValue({ cleared: 1, workflow_id: null });
    clearFinishedRuns.mockResolvedValue({ cleared: 1, workflow_id: null });
  });

  it("lists each finished run on one line, with what it made, and the panel is not idle", async () => {
    const wrapper = await mountWith([run(), run({ id: "run-b", label: "Inpaint fill", made: 1 })]);
    expect(rows(wrapper).map((row) => textOf(row).trim())).toEqual([
      "Portrait SDXL 4",
      "Inpaint fill 1",
    ]);
    expect(rows(wrapper)[0].find(".tm-mark").attributes("data-state")).toBe("done");
    expect(textOf(wrapper)).not.toContain("No active tasks");
    // Folded until asked, then when it ran and the pictures it still has.
    await within(rows(wrapper)[0], "tm-finished-caret").trigger("click");
    expect(textOf(rows(wrapper)[0])).toContain("Finished 2h ago.");
    expect(within(rows(wrapper)[0], "tm-finished-tile").exists()).toBe(true);
    expect(rows(wrapper)[0].findAll('[data-testid="tm-finished-tile"]')).toHaveLength(3);
  });

  it("says a failed run failed, and why, with a mark of its own", async () => {
    const wrapper = await mountWith([
      run({ status: "failed", message: "KSampler: out of memory", made: 0, picture_ids: [] }),
    ]);
    expect(rows(wrapper)[0].find(".tm-mark").attributes("data-state")).toBe("failed");
    await within(rows(wrapper)[0], "tm-finished-caret").trigger("click");
    expect(textOf(rows(wrapper)[0])).toContain("Failed 2h ago: KSampler: out of memory");
    expect(within(rows(wrapper)[0], "tm-finished-tile").exists()).toBe(false);
  });

  it("opens a run with changes by itself once as it lands, and never one already listed", async () => {
    const tasks = useTasksStore();
    const wrapper = await mountWith([run({ id: "run-old", offer: true })]);
    // Listed by the first poll: a reload, so it comes back folded.
    expect(wrapper.find(".tm-detail").exists()).toBe(false);
    tasks.noteFinished([run({ id: "run-new", offer: true }), ...tasks.finishedRuns]);
    tasks.noteFinished([run({ id: "run-plain" }), ...tasks.finishedRuns]);
    await flushPromises();
    expect(wrapper.findAll(".tm-detail")).toHaveLength(1);
    expect(rows(wrapper)[1].find(".tm-detail").exists()).toBe(true);
    // Folded by hand, it stays folded on the next poll.
    await within(rows(wrapper)[1], "tm-finished-caret").trigger("click");
    tasks.noteFinished([...tasks.finishedRuns]);
    await flushPromises();
    expect(wrapper.find(".tm-detail").exists()).toBe(false);
  });

  it("opens a run whose offer arrives a poll after it, and keeps the rows through a failed read", async () => {
    const tasks = useTasksStore();
    const wrapper = await mountWith([], { landed: true });
    // It ended before its request was answered: listed first without the offer.
    tasks.noteFinished([run()]);
    await flushPromises();
    expect(wrapper.find(".tm-detail").exists()).toBe(false);
    tasks.noteFinished([run({ offer: true })]);
    await flushPromises();
    expect(wrapper.find(".tm-detail").exists()).toBe(true);
    // The server could not read them: what is shown stays.
    tasks.noteFinished(null);
    await flushPromises();
    expect(rows(wrapper)).toHaveLength(1);
  });

  it("opens a tile's picture in the lightbox of the grid behind the panel", async () => {
    const wrapper = await mountWith([run()]);
    await within(rows(wrapper)[0], "tm-finished-caret").trigger("click");
    await within(rows(wrapper)[0], "tm-finished-tile").trigger("click");
    expect(nav.push).toHaveBeenCalledWith({ query: { set: "4", overlay: "21" } });
  });

  it("asks the Workflows view to open it there, which parks its place first", async () => {
    nav.route = { name: "workflows", query: {} };
    const workflows = useWorkflowsStore();
    const wrapper = await mountWith([run()]);
    await within(rows(wrapper)[0], "tm-finished-caret").trigger("click");
    await within(rows(wrapper)[0], "tm-finished-tile").trigger("click");
    expect(workflows.pictureToOpen).toBe(21);
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("dismisses a run with changes on the server, which takes its offer with it", async () => {
    dismissFinishedRun.mockResolvedValue({ cleared: 1, workflow_id: WF });
    const runDialog = useRunDialogStore();
    runDialog.ranWith(WF, { name: "Portrait SDXL", count: 1, text: "Steps.", runIds: ["run-a"] });
    const wrapper = await mountWith([run({ offer: true }), run({ id: "run-b", label: "Inpaint fill" })]);
    const dismiss = within(rows(wrapper)[0], "tm-finished-dismiss");
    expect(dismiss.attributes("aria-label")).toBe(
      "Dismiss what the run of Portrait SDXL changed",
    );
    await dismiss.trigger("click");
    await flushPromises();
    expect(dismissFinishedRun).toHaveBeenCalledWith("run-a");
    expect(rows(wrapper).map((row) => row.attributes("data-run"))).toEqual(["run-b"]);
    expect(runDialog.ranChanges[WF]).toBeUndefined();
    // The keyboard goes to the row that took its place.
    expect(document.activeElement).toBe(
      within(rows(wrapper)[0], "tm-finished-caret").element,
    );
  });

  it("keeps a run whose dismiss is refused, and says so", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    dismissFinishedRun.mockRejectedValue(new Error("Network Error"));
    const wrapper = await mountWith([run()]);
    await within(rows(wrapper)[0], "tm-finished-dismiss").trigger("click");
    await flushPromises();
    expect(rows(wrapper)).toHaveLength(1);
    expect(useNoticeStore().notices).toMatchObject([
      { level: "error", text: "The run of Portrait SDXL could not be dismissed." },
    ]);
    warn.mockRestore();
  });

  it("does not clear while a save is out, which keeps the keyboard where it is", async () => {
    saveWorkflow.mockImplementation(() => new Promise(() => {}));
    const wrapper = await mountWith([run({ offer: true }), run({ id: "run-b" })], {
      landed: true,
    });
    const save = wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Save");
    await save.trigger("click");
    await flushPromises();
    const clear = wrapper.find('[data-testid="tm-finished-clear"]');
    expect(clear.attributes("disabled")).toBeDefined();
    await clear.trigger("click");
    await flushPromises();
    expect(clearFinishedRuns).not.toHaveBeenCalled();
    expect(rows(wrapper)).toHaveLength(2);
  });

  it("clears every run but those with unsaved changes", async () => {
    const wrapper = await mountWith([
      run({ id: "run-a", offer: true }),
      run({ id: "run-b", label: "Inpaint fill" }),
      run({ id: "run-c", label: "Flux upscale" }),
    ]);
    await wrapper.find('[data-testid="tm-finished-clear"]').trigger("click");
    await flushPromises();
    expect(clearFinishedRuns).toHaveBeenCalledTimes(1);
    expect(dismissFinishedRun).not.toHaveBeenCalled();
    expect(rows(wrapper).map((row) => row.attributes("data-run"))).toEqual(["run-a"]);
    expect(within(rows(wrapper)[0], "tm-finished-pill").text()).toBe("1 change");
  });
});

// ── The GPU queue: bars, Running and Waiting groups, Run anyway ──────────────

const GPU_DEVICE = {
  uuid: "GPU-1",
  name: "NVIDIA GeForce RTX 4090",
  total_mb: 24576,
  used_mb: 13000,
  free_mb: 11576,
  reserved_mb: 0,
  budget_mb: 10000,
  measurable: true,
  pixlstash: true,
};

function gpuLease(over) {
  return {
    id: "lease-x",
    tenant: "pixlstash",
    label: "Tagging",
    kind: "light",
    state: "granted",
    device: "GPU-1",
    need_mb: 2000,
    ram_mb: 0,
    reserved_mb: 0,
    held_mb: 0,
    peak_mb: null,
    since: "2026-10-22T10:00:00Z",
    worker_type: null,
    run_id: null,
    job_id: null,
    on_cpu: false,
    warnings: [],
    waiting_reason: null,
    ...over,
  };
}

function gpuAnswer(over = {}) {
  return {
    enforcing: true,
    devices: [GPU_DEVICE],
    ram: { total_mb: 65536, available_mb: 40000 },
    holders: [
      { device: "GPU-1", tenant: "comfyui", label: "ComfyUI", used_mb: 9800, measured_by: "nvml", lease_id: null, outside_pixlstash: false },
      { device: "GPU-1", tenant: "pixlstash", label: "PixlStash", used_mb: 2150, measured_by: "torch_reserved", lease_id: null, outside_pixlstash: false },
    ],
    leases: [],
    preempted: [],
    summary: { state: "running", headline: "" },
    ...over,
  };
}

/** Put the store in the state a poll would, without the poll. */
function setGpu(resources, runs = []) {
  const tasksStore = useTasksStore();
  tasksStore.resources = resources;
  tasksStore.comfyuiRuns = runs;
  return tasksStore;
}

describe("the GPU queue in the task manager", () => {
  beforeEach(() => {
    localStorage.clear();
    runLeaseAnyway.mockClear();
    cancelLease.mockClear();
  });

  it("draws one bar per GPU, its free VRAM, and the four-word legend", () => {
    setGpu(gpuAnswer());
    const wrapper = mount(TasksPanel, globalOpts);
    const bar = wrapper.find('[data-testid="tm-gpu"]');
    expect(bar.exists()).toBe(true);
    expect(bar.find(".tm-gpu-name").text()).toBe("NVIDIA GeForce RTX 4090");
    // 24576 MB card, 11576 MB free: the figures in GB at one decimal.
    expect(bar.find(".tm-gpu-figs").text()).toContain("24.0 GB VRAM");
    expect(bar.find(".tm-gpu-figs").text()).toContain("11.3 GB free");
    expect(bar.findAll(".tm-legend li").map((item) => item.text())).toEqual([
      "PixlStash",
      "ComfyUI",
      "AI Toolkit",
      "Other apps",
      "Headroom",
    ]);
    // ComfyUI's 9.8 GB is its own segment, and a spare margin is drawn.
    expect(bar.find(".tm-vram-seg--comfyui").exists()).toBe(true);
    expect(bar.find(".tm-vram-seg--spare").exists()).toBe(true);
  });

  it("draws no bar and no fold on a server that sends no resources", () => {
    setGpu(null);
    const wrapper = mount(TasksPanel, globalOpts);
    expect(wrapper.find('[data-testid="tm-gpu"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="tm-pixlstash"]').exists()).toBe(false);
  });

  it("folds PixlStash's own work behind one row, collapsed, and remembers the caret", async () => {
    setGpu(
      gpuAnswer({
        leases: [
          gpuLease({ id: "a", label: "Tagging", held_mb: 1200 }),
          gpuLease({ id: "b", label: "OCR", held_mb: 600, on_cpu: true }),
        ],
      }),
    );
    const wrapper = mount(TasksPanel, globalOpts);
    const fold = wrapper.find('[data-testid="tm-pixlstash"]');
    expect(fold.find('[data-testid="tm-pixl-count"]').text()).toBe("2");
    expect(fold.find(".tm-fig-text").text()).toBe("1.8 GB");
    // Collapsed by default: no task lines.
    expect(wrapper.findAll(".tm-row--child")).toHaveLength(0);
    expect(fold.find(".tm-caret").attributes("aria-expanded")).toBe("false");

    await fold.find(".tm-caret").trigger("click");
    const children = wrapper.findAll(".tm-row--child");
    expect(children).toHaveLength(2);
    // A task on the CPU says so in its figure column, and is still running.
    expect(children[1].find(".tm-fig-text").text()).toBe("CPU");
    expect(children[1].find(".tm-mark").classes("tm-mark--running")).toBe(true);
    expect(localStorage.getItem("pixlstash:tasks:pixlstash-fold")).toBe("1");
  });

  it("folds every background worker, lease or not, and draws its sparkline only when open", async () => {
    // Between batches a worker holds no lease; it used to fall out of the fold.
    const tasksStore = setGpu(
      gpuAnswer({ leases: [gpuLease({ id: "a", label: "Tags", worker_type: "TagTask", held_mb: 1200 })] }),
    );
    tasksStore.workerSnapshots = {
      ImageEmbeddingTask: { label: "Image embeddings", current: 5, total: 9, running: false, active: true },
      TagTask: { label: "Tags", current: 1, total: 9, running: false, active: true },
    };
    const wrapper = mount(TasksPanel, globalOpts);
    const fold = wrapper.find('[data-testid="tm-pixlstash"]');
    expect(fold.find('[data-testid="tm-pixl-count"]').text()).toBe("2");
    // Both idle between batches: the fold does not pulse for them.
    expect(fold.find(".tm-mark").classes("tm-mark--running")).toBe(false);
    // Collapsed: no worker is listed anywhere, inside the fold or beside it.
    expect(wrapper.findAll(".tm-worker-row")).toHaveLength(0);
    expect(wrapper.findAll("canvas.tm-sparkline")).toHaveLength(0);

    tasksStore.workerSnapshots = {
      ...tasksStore.workerSnapshots,
      TagTask: { ...tasksStore.workerSnapshots.TagTask, running: true },
    };
    await flushPromises();
    expect(fold.find(".tm-mark").classes("tm-mark--running")).toBe(true);

    await fold.find(".tm-caret").trigger("click");
    const rows = fold.findAll(".tm-worker-row");
    expect(rows.map((row) => row.find(".tm-worker-label").text())).toEqual([
      "Image Embeddings",
      "Tags",
    ]);
    expect(rows.every((row) => row.find("canvas.tm-sparkline").exists())).toBe(true);
    // The tagger's lease shows on the tagger's row, and is not a line of its own.
    expect(rows[1].find('[data-testid="tm-worker-figure"]').text()).toBe("1.2 GB");
    expect(rows[0].find('[data-testid="tm-worker-figure"]').exists()).toBe(false);
    expect(wrapper.findAll(".tm-row--child")).toHaveLength(0);
  });

  it("marks a task the backend stopped as stopped, not as lost", async () => {
    setGpu(
      gpuAnswer({
        leases: [gpuLease({ id: "a", label: "Tagging", held_mb: 1200 })],
        preempted: [{ worker_type: "FaceTask", caused_by: "lease-z", since: "2026-10-22T10:01:00Z", text: "Stopped to make room" }],
      }),
    );
    localStorage.setItem("pixlstash:tasks:pixlstash-fold", "1");
    const wrapper = mount(TasksPanel, globalOpts);
    const children = wrapper.findAll(".tm-row--child");
    expect(children.map((child) => child.find(".tm-fig-text").text())).toEqual(["1.2 GB", "stopped"]);
    expect(children[1].find(".tm-mark").attributes("data-state")).toBe("stopped");
  });

  it("offers a read-only session no action on a run, waiting or running", () => {
    readOnly.ref.value = true;
    setGpu(
      gpuAnswer({
        leases: [
          gpuLease({
            id: "lease-b",
            tenant: "comfyui",
            label: "ComfyUI 'Klein inpaint'",
            kind: "heavy",
            state: "waiting",
            device: null,
            need_mb: 14600,
            run_id: "run-b",
            waiting_reason: {
              code: "vram",
              text: "Needs 14.6 GB; 11.5 GB is free.",
              need_mb: 14600,
              free_mb: 11500,
              short_by_mb: 3100,
              position: 1,
              blocked_by: [],
              actions: ["run_anyway", "cancel", "free_comfyui"],
            },
          }),
        ],
      }),
      [{ run_id: "run-b", prompt_id: null, label: "Klein inpaint", status: "waiting", percent: 0 }],
    );
    useTasksStore().comfyuiRuns.push({
      prompt_id: "p-2",
      label: "Cinematic portrait",
      status: "running",
      percent: 40,
    });
    const wrapper = mount(TasksPanel, globalOpts);
    expect(wrapper.findAll('[data-testid="tm-waiting-row"]')).toHaveLength(1);
    expect(wrapper.find('[data-testid="tm-run-anyway"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="tm-cancel-wait"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="tm-free-comfyui"]').exists()).toBe(false);
    expect(wrapper.find('button[aria-label="Stop ComfyUI"]').exists()).toBe(false);
  });

  it("lists the waiting run in the order the queue runs it, with its need and no units", () => {
    setGpu(
      gpuAnswer({
        leases: [
          gpuLease({
            id: "lease-b",
            tenant: "comfyui",
            label: "ComfyUI 'Klein inpaint'",
            kind: "heavy",
            state: "waiting",
            device: null,
            need_mb: 14600,
            run_id: "run-b",
            waiting_reason: {
              code: "vram",
              text: "Needs 14.6 GB; 11.5 GB is free.",
              need_mb: 14600,
              free_mb: 11500,
              short_by_mb: 3100,
              position: 1,
              blocked_by: [],
              actions: ["run_anyway", "cancel"],
            },
          }),
        ],
      }),
      [{ run_id: "run-b", prompt_id: null, label: "Klein inpaint", status: "waiting", percent: 0 }],
    );
    const wrapper = mount(TasksPanel, globalOpts);
    const rows = wrapper.findAll('[data-testid="tm-waiting-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].find(".tm-name").text()).toBe("ComfyUI 'Klein inpaint'");
    expect(rows[0].find(".tm-fig-text").text()).toBe("14.3 GB");
    expect(rows[0].find(".tm-mark--waiting").exists()).toBe(true);
    // The three actions the backend offered, and none it did not.
    expect(wrapper.find('[data-testid="tm-run-anyway"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tm-cancel-wait"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tm-free-comfyui"]').exists()).toBe(false);
    // The run is not also listed under Running.
    expect(wrapper.findAll('[data-testid="tm-comfy-row"]')).toHaveLength(0);
  });

  it("asks before cancelling a waiting AI Toolkit run, and cancels nothing on no", async () => {
    cancelLease.mockClear();
    setGpu(
      gpuAnswer({
        leases: [
          gpuLease({
            id: "lease-t",
            tenant: "aitoolkit",
            label: "Clementine",
            kind: "heavy",
            state: "waiting",
            device: null,
            need_mb: 14600,
            waiting_reason: {
              code: "fifo",
              text: "Waiting behind ComfyUI.",
              need_mb: 14600,
              free_mb: 11500,
              short_by_mb: 3100,
              position: 1,
              blocked_by: [],
              actions: ["cancel"],
            },
          }),
        ],
      }),
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const wrapper = mount(TasksPanel, globalOpts);
    await wrapper.find('[data-testid="tm-cancel-wait"]').trigger("click");
    await flushPromises();
    expect(confirm).toHaveBeenCalledWith(
      'Stop training "Clementine"? The run will stop and cannot be resumed.',
    );
    expect(cancelLease).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    await wrapper.find('[data-testid="tm-cancel-wait"]').trigger("click");
    await flushPromises();
    expect(cancelLease).toHaveBeenCalledWith("lease-t");
  });

  it("asks before Run anyway, and starts the lease only when the dialog is confirmed", async () => {
    setGpu(
      gpuAnswer({
        leases: [
          gpuLease({
            id: "lease-b",
            tenant: "comfyui",
            label: "ComfyUI 'Klein inpaint'",
            kind: "heavy",
            state: "waiting",
            device: null,
            need_mb: 14600,
            run_id: "run-b",
            waiting_reason: { code: "vram", text: "Needs 14.6 GB", need_mb: 14600, free_mb: 11500, short_by_mb: 3100, position: 1, blocked_by: [], actions: ["run_anyway"] },
          }),
        ],
      }),
    );
    // The dialog's own overlay needs Vuetify's defaults; its content is tested
    // in RunAnywayDialog.test.js, so the overlay itself is stubbed here.
    const wrapper = mount(TasksPanel, {
      global: { stubs: { ...globalOpts.global.stubs, AppDialog: true } },
    });
    // Nothing is asked of the backend by merely drawing the row.
    expect(runLeaseAnyway).not.toHaveBeenCalled();
    expect(wrapper.findComponent(RunAnywayDialog).exists()).toBe(false);

    await wrapper.find('[data-testid="tm-run-anyway"]').trigger("click");
    const dialog = wrapper.findComponent(RunAnywayDialog);
    expect(dialog.props("open")).toBe(true);
    expect(dialog.props("needMb")).toBe(14600);
    expect(runLeaseAnyway).not.toHaveBeenCalled();

    dialog.vm.$emit("confirm");
    await flushPromises();
    expect(runLeaseAnyway).toHaveBeenCalledWith("lease-b");
  });

  it("hands focus back to Run anyway when its dialog closes", async () => {
    setGpu(
      gpuAnswer({
        leases: [
          gpuLease({
            id: "lease-b",
            tenant: "comfyui",
            label: "ComfyUI 'Klein inpaint'",
            kind: "heavy",
            state: "waiting",
            device: null,
            need_mb: 14600,
            run_id: "run-b",
            waiting_reason: { code: "vram", text: "Needs 14.6 GB", need_mb: 14600, free_mb: 11500, short_by_mb: 3100, position: 1, blocked_by: [], actions: ["run_anyway"] },
          }),
        ],
      }),
    );
    const wrapper = mount(TasksPanel, {
      global: { stubs: { ...globalOpts.global.stubs, AppDialog: true } },
      attachTo: document.body,
    });
    // Where focus is while the dialog is up.
    const inDialog = document.body.appendChild(document.createElement("button"));
    try {
      const button = wrapper.find('[data-testid="tm-run-anyway"]');
      button.element.focus();
      await button.trigger("click");
      inDialog.focus();
      wrapper.findComponent(RunAnywayDialog).vm.$emit("close");
      await flushPromises();
      expect(document.activeElement).toBe(button.element);
    } finally {
      inDialog.remove();
      wrapper.unmount();
    }
  });
});

describe("the AI Toolkit run in the task manager", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  function trainingLease(over = {}) {
    return {
      id: "lease-train",
      tenant: "aitoolkit",
      label: "LoRA training 'MyChar'",
      kind: "exclusive",
      state: "granted",
      device: "GPU-1",
      need_mb: 14336,
      ram_mb: 0,
      reserved_mb: 14336,
      held_mb: 14336,
      peak_mb: null,
      since: "2026-10-22T10:00:00Z",
      worker_type: null,
      run_id: null,
      job_id: "job-1",
      workflow_id: null,
      on_cpu: false,
      warnings: [],
      waiting_reason: null,
      progress: { step: 2750, total: 4000, loss: 0.123 },
      ...over,
    };
  }

  it("is one row: its name, the step, total and loss it reports, and the VRAM it holds", () => {
    setGpu(gpuAnswer({ leases: [trainingLease()] }));
    const wrapper = mount(TasksPanel, globalOpts);
    const row = wrapper.find('[data-testid="tm-aitoolkit-row"]');
    expect(wrapper.findAll('[data-testid="tm-aitoolkit-row"]')).toHaveLength(1);
    expect(row.find(".tm-name").text()).toContain("LoRA training 'MyChar'");
    expect(row.find('[data-testid="tm-aitoolkit-progress"]').text()).toBe(
      "step 2750/4000 · loss 0.123",
    );
    expect(row.find(".tm-fig-text").text()).toBe("14.0 GB");
    expect(row.find(".tm-mark").classes("tm-mark--running")).toBe(true);
  });

  it("opens the run's page in the shelf's Training runs tab", async () => {
    nav.push = vi.fn();
    setGpu(gpuAnswer({ leases: [trainingLease()] }));
    const wrapper = mount(TasksPanel, globalOpts);
    await wrapper.find('[data-testid="tm-aitoolkit-watch"]').trigger("click");
    expect(nav.push).toHaveBeenCalledWith({
      name: "models-runs",
      query: { run: "job-1" },
    });
  });

  it("shows no step until the run reports a total", () => {
    setGpu(gpuAnswer({ leases: [trainingLease({ progress: null })] }));
    const wrapper = mount(TasksPanel, globalOpts);
    expect(wrapper.find('[data-testid="tm-aitoolkit-row"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tm-aitoolkit-progress"]').exists()).toBe(false);
  });

  it("draws the run once, with no PixlStash fold or worker row of its own", () => {
    setGpu(gpuAnswer({ leases: [trainingLease()] }));
    const wrapper = mount(TasksPanel, globalOpts);
    expect(wrapper.find('[data-testid="tm-pixlstash"]').exists()).toBe(false);
    expect(wrapper.findAll(".tm-worker-row")).toHaveLength(0);
    expect(wrapper.findAll('[data-testid="tm-aitoolkit-row"]')).toHaveLength(1);
  });

  it("is listed under Waiting for the GPU while its lease waits, not as a running row", () => {
    setGpu(gpuAnswer({ leases: [trainingLease({ state: "waiting", progress: null, held_mb: 0 })] }));
    const wrapper = mount(TasksPanel, globalOpts);
    expect(wrapper.find('[data-testid="tm-aitoolkit-row"]').exists()).toBe(false);
    expect(wrapper.findAll('[data-testid="tm-waiting-row"]')).toHaveLength(1);
  });
});
