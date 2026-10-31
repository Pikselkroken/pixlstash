// Settings › Privacy: the ghost retention position and its two counted purges.
//
// The purge is the destructive half, so what is pinned is that a press alone
// destroys nothing (the confirm does), that each button reaches its own route,
// and that the count on the button is re-read from the server afterwards.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", () => ({
  VCard: { template: "<div><slot /></div>" },
  VDialog: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: '<div v-if="modelValue"><slot /></div>',
  },
  VIcon: { template: "<i />" },
  VProgressCircular: { template: "<i />" },
  VSwitch: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
  },
}));

const config = vi.hoisted(() => ({ patchUserConfig: vi.fn() }));
vi.mock("../../api/config", () => config);
const workflows = vi.hoisted(() => ({ invalidate: vi.fn() }));
vi.mock("../../stores/useWorkflowsStore", () => ({
  useWorkflowsStore: () => workflows,
}));
vi.mock("../../api/telemetry", () => ({
  getInstallId: vi.fn().mockResolvedValue({ available: false }),
  recreateInstallId: vi.fn(),
}));

const api = vi.hoisted(() => ({
  getGhostRetention: vi.fn(),
  setGhostRetention: vi.fn(),
  purgePictureGhosts: vi.fn(),
  purgeModelGhosts: vi.fn(),
}));
vi.mock("../../api/serverConfig", () => api);

import PrivacySection from "./PrivacySection.vue";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";

function payload(overrides = {}) {
  return {
    workflow_ghost_retention: "covered",
    workflow_ghost_retention_choices: ["off", "covered", "on"],
    picture_ghosts: 12,
    model_ghosts: 3,
    ...overrides,
  };
}

async function mountPane() {
  const wrapper = mount(PrivacySection, { props: { open: true } });
  await flushPromises();
  return wrapper;
}

function button(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().trim() === text);
}

beforeEach(() => {
  setActivePinia(createPinia());
  config.patchUserConfig.mockReset();
  for (const fn of Object.values(api)) fn.mockReset();
  workflows.invalidate.mockReset();
  api.getGhostRetention.mockResolvedValue(payload());
  api.purgePictureGhosts.mockResolvedValue({ ghosts_erased: 12 });
  api.purgeModelGhosts.mockResolvedValue({ names_forgotten: 3 });
});

describe("ghost retention", () => {
  it("shows the three positions and a count on each purge", async () => {
    const wrapper = await mountPane();
    for (const label of ["Never", "Partially", "Always"]) {
      expect(button(wrapper, label)).toBeTruthy();
    }
    expect(button(wrapper, "Purge 12")).toBeTruthy();
    expect(button(wrapper, "Forget 3")).toBeTruthy();
  });

  it("saves a position through the PATCH", async () => {
    api.setGhostRetention.mockResolvedValue(
      payload({ workflow_ghost_retention: "off" }),
    );
    const wrapper = await mountPane();
    await button(wrapper, "Never").trigger("click");
    await flushPromises();
    expect(api.setGhostRetention).toHaveBeenCalledWith("off");
  });

  it("puts the saved position back when the save fails", async () => {
    api.setGhostRetention.mockRejectedValue(new Error("offline"));
    const wrapper = await mountPane();
    await button(wrapper, "Always").trigger("click");
    await flushPromises();
    expect(button(wrapper, "Partially").attributes("aria-checked")).toBe(
      "true",
    );
    expect(wrapper.find("[role='alert']").text()).toContain(
      "previous choice was kept",
    );
  });

  it("never disables the control mid-save, and sends only the newest pick", async () => {
    let finish;
    api.setGhostRetention.mockImplementationOnce(
      () => new Promise((resolve) => (finish = resolve)),
    );
    api.setGhostRetention.mockResolvedValue(
      payload({ workflow_ghost_retention: "on" }),
    );
    const wrapper = await mountPane();
    await button(wrapper, "Never").trigger("click");
    expect(button(wrapper, "Always").attributes("disabled")).toBeUndefined();
    await button(wrapper, "Partially").trigger("click");
    await button(wrapper, "Always").trigger("click");
    finish(payload({ workflow_ghost_retention: "off" }));
    await flushPromises();
    expect(api.setGhostRetention.mock.calls).toEqual([["off"], ["on"]]);
    expect(button(wrapper, "Always").attributes("aria-checked")).toBe("true");
  });

  it("purges picture ghosts only after the confirm, then re-reads the count", async () => {
    const wrapper = await mountPane();
    await button(wrapper, "Purge 12").trigger("click");
    expect(api.purgePictureGhosts).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("Purge 12 picture ghosts?");

    api.getGhostRetention.mockResolvedValue(payload({ picture_ghosts: 0 }));
    await button(wrapper, "Purge 12 ghosts").trigger("click");
    await flushPromises();
    expect(api.purgePictureGhosts).toHaveBeenCalledTimes(1);
    expect(workflows.invalidate).toHaveBeenCalledTimes(1);
    expect(api.purgeModelGhosts).not.toHaveBeenCalled();
    expect(button(wrapper, "None kept").attributes("disabled")).toBeDefined();
  });

  it("forgets model names through their own route", async () => {
    const wrapper = await mountPane();
    await button(wrapper, "Forget 3").trigger("click");
    expect(wrapper.text()).toContain("Forget 3 model names?");
    await button(wrapper, "Forget 3 names").trigger("click");
    await flushPromises();
    expect(api.purgeModelGhosts).toHaveBeenCalledWith(3);
    expect(api.purgePictureGhosts).not.toHaveBeenCalled();
  });

  it("keeps the section and says why when a purge and its re-read both fail", async () => {
    const wrapper = await mountPane();
    api.purgeModelGhosts.mockRejectedValue({ response: { status: 409 } });
    api.getGhostRetention.mockRejectedValue(new Error("offline"));
    await button(wrapper, "Forget 3").trigger("click");
    await button(wrapper, "Forget 3 names").trigger("click");
    await flushPromises();
    expect(workflows.invalidate).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("Model ghosts");
    expect(wrapper.find("[role='alert']").text()).toContain(
      "nothing was purged",
    );
  });

  it("draws no ghost controls when the server has no hub", async () => {
    api.getGhostRetention.mockResolvedValue(
      payload({ picture_ghosts: null, model_ghosts: null }),
    );
    const wrapper = await mountPane();
    expect(wrapper.text()).not.toContain("Picture ghosts");
    expect(wrapper.text()).not.toContain("Model ghosts");
  });
});

describe("shared copies", () => {
  const state = (wrapper, way) =>
    wrapper.find(`[data-way="${way}"] .pv__state`).text();

  it("strips on every way out until told otherwise", async () => {
    const wrapper = await mountPane();
    expect(["copy", "drag", "share"].map((w) => state(wrapper, w))).toEqual([
      "Strips",
      "Strips",
      "Strips",
    ]);
  });

  it("reads each way out from the user config on its own", async () => {
    useUserPrefsStore().hydrateKeepWorkflow({ keep_workflow_drag: true });
    const wrapper = await mountPane();
    expect(["copy", "drag", "share"].map((w) => state(wrapper, w))).toEqual([
      "Strips",
      "Keeps",
      "Strips",
    ]);
  });

  it("saves only the row that was switched", async () => {
    config.patchUserConfig.mockResolvedValue({});
    const wrapper = await mountPane();
    await wrapper.find('[data-way="copy"] input').setValue(true);
    await flushPromises();
    expect(config.patchUserConfig).toHaveBeenCalledWith({
      keep_workflow_copy: true,
    });
    expect(state(wrapper, "copy")).toBe("Keeps");
    expect(state(wrapper, "drag")).toBe("Strips");
  });

  it("keeps saying Strips when the save fails", async () => {
    config.patchUserConfig.mockRejectedValue(new Error("offline"));
    const wrapper = await mountPane();
    await wrapper.find('[data-way="share"] input').setValue(true);
    await flushPromises();
    expect(state(wrapper, "share")).toBe("Strips");
    expect(useUserPrefsStore().keepWorkflow.share).toBe(false);
    expect(wrapper.find("[role='alert']").text()).toContain(
      "previous choice was kept",
    );
  });
});
