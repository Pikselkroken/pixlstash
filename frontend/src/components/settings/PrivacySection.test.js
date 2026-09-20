// Settings › Privacy: the ghost retention position and its two counted purges.
//
// The purge is the destructive half, so what is pinned is that a press alone
// destroys nothing (the confirm does), that each button reaches its own route,
// and that the count on the button is re-read from the server afterwards.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VCard: { template: "<div><slot /></div>" },
  VDialog: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: '<div v-if="modelValue"><slot /></div>',
  },
  VIcon: { template: "<i />" },
  VProgressCircular: { template: "<i />" },
  VSwitch: { template: "<input />" },
}));

vi.mock("../../stores/useUserPrefsStore", () => ({
  useUserPrefsStore: () => ({ checkForUpdates: false }),
}));
vi.mock("../../api/config", () => ({ patchUserConfig: vi.fn() }));
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
  for (const fn of Object.values(api)) fn.mockReset();
  workflows.invalidate.mockReset();
  api.getGhostRetention.mockResolvedValue(payload());
  api.purgePictureGhosts.mockResolvedValue({ ghosts_erased: 12 });
  api.purgeModelGhosts.mockResolvedValue({ names_forgotten: 3 });
});

describe("ghost retention", () => {
  it("shows the three positions and a count on each purge", async () => {
    const wrapper = await mountPane();
    for (const label of ["Off", "Covered only", "On"]) {
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
    await button(wrapper, "Off").trigger("click");
    await flushPromises();
    expect(api.setGhostRetention).toHaveBeenCalledWith("off");
  });

  it("puts the saved position back when the save fails", async () => {
    api.setGhostRetention.mockRejectedValue(new Error("offline"));
    const wrapper = await mountPane();
    await button(wrapper, "On").trigger("click");
    await flushPromises();
    expect(button(wrapper, "Covered only").attributes("aria-checked")).toBe(
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
    await button(wrapper, "Off").trigger("click");
    expect(button(wrapper, "On").attributes("disabled")).toBeUndefined();
    await button(wrapper, "Covered only").trigger("click");
    await button(wrapper, "On").trigger("click");
    finish(payload({ workflow_ghost_retention: "off" }));
    await flushPromises();
    expect(api.setGhostRetention.mock.calls).toEqual([["off"], ["on"]]);
    expect(button(wrapper, "On").attributes("aria-checked")).toBe("true");
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
