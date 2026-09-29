// Switching "Whole-face close-up" on offers to re-check the library's faces
// (#1662): only on the off-to-on save, only after the save has landed, and the
// answer never decides whether the setting is stored.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

const patchUserConfig = vi.fn();
const retagFaceCrops = vi.fn();
const confirm = vi.fn();
const notice = { success: vi.fn(), error: vi.fn() };

vi.mock("../../api/config", () => ({
  patchUserConfig: (...a) => patchUserConfig(...a),
}));
vi.mock("../../api/taggers", () => ({
  getLabelThresholds: vi.fn(async () => []),
  retagFaceCrops: (...a) => retagFaceCrops(...a),
}));
vi.mock("../../composables/useConfirm", () => ({
  useConfirm: () => ({ confirm: (...a) => confirm(...a) }),
}));
vi.mock("../../stores/useNoticeStore", () => ({
  useNoticeStore: () => notice,
}));

import TaggerPluginSettingsPanel from "./TaggerPluginSettingsPanel.vue";

const PLUGIN = {
  name: "pixlstash_tagger",
  display_name: "PixlStash Tagger",
  parameter_schema: [
    { name: "whole_face_crop", type: "bool", default: false },
  ],
};

const AppDialogStub = {
  props: { open: Boolean, title: String },
  template: `<div><slot /><slot name="footer" /></div>`,
};

function mountPanel(params, plugin = PLUGIN) {
  return mount(TaggerPluginSettingsPanel, {
    props: { plugin, settings: { plugins: { [plugin.name]: { params } } } },
    global: {
      stubs: {
        AppDialog: AppDialogStub,
        TaggerParametersUI: true,
        AppButton: { template: `<button><slot /></button>` },
      },
    },
  });
}

const form = (wrapper) => wrapper.findComponent({ name: "TaggerParametersUI" });

/** Edit the form as the user would, then let the auto-save debounce run. */
async function edit(wrapper, values) {
  form(wrapper).vm.$emit("update:modelValue", {
    ...wrapper.vm.formParams,
    ...values,
  });
  await vi.advanceTimersByTimeAsync(500);
  await flushPromises();
}

const saveWith = (wrapper, whole_face_crop) => edit(wrapper, { whole_face_crop });

describe("TaggerPluginSettingsPanel: whole-face re-check offer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    patchUserConfig.mockReset().mockResolvedValue({});
    retagFaceCrops.mockReset().mockResolvedValue({ count: 42 });
    confirm.mockReset().mockResolvedValue(false);
    notice.success.mockReset();
    notice.error.mockReset();
  });

  it("offers the re-check with the count when the setting goes off to on", async () => {
    const wrapper = mountPanel({ whole_face_crop: false });
    await saveWith(wrapper, true);

    expect(patchUserConfig).toHaveBeenCalledOnce();
    expect(retagFaceCrops).toHaveBeenCalledWith({ dryRun: true });
    expect(confirm).toHaveBeenCalledOnce();
    expect(confirm.mock.calls[0][0].title).toContain("42");
    // "Not now": nothing is queued, and the setting was saved regardless.
    expect(retagFaceCrops).toHaveBeenCalledTimes(1);
    expect(notice.success).not.toHaveBeenCalled();
  });

  it("queues the re-check when accepted", async () => {
    confirm.mockResolvedValue(true);
    const wrapper = mountPanel({});
    await saveWith(wrapper, true);

    expect(retagFaceCrops).toHaveBeenCalledTimes(2);
    expect(retagFaceCrops).toHaveBeenLastCalledWith();
    expect(notice.success.mock.calls[0][0]).toContain("42");
  });

  it("reports what the retag queued, not the dry run's count", async () => {
    confirm.mockResolvedValue(true);
    retagFaceCrops
      .mockResolvedValueOnce({ count: 42 })
      .mockResolvedValueOnce({ count: 40 });
    const wrapper = mountPanel({ whole_face_crop: false });
    await saveWith(wrapper, true);

    expect(notice.success.mock.calls[0][0]).toContain("Re-checking 40 ");
  });

  it("says so when the re-check cannot be started", async () => {
    confirm.mockResolvedValue(true);
    retagFaceCrops
      .mockResolvedValueOnce({ count: 42 })
      .mockRejectedValueOnce(new Error("offline"));
    const wrapper = mountPanel({ whole_face_crop: false });
    await saveWith(wrapper, true);

    expect(patchUserConfig).toHaveBeenCalledOnce();
    expect(notice.error).toHaveBeenCalledOnce();
    expect(notice.success).not.toHaveBeenCalled();
  });

  it.each([
    ["stays on", true, true],
    ["goes on to off", true, false],
    ["stays off", false, false],
  ])("does not offer when the setting %s", async (_label, before, after) => {
    const wrapper = mountPanel({ whole_face_crop: before });
    await saveWith(wrapper, after);

    // An unchanged setting is not even saved.
    expect(patchUserConfig).toHaveBeenCalledTimes(before === after ? 0 : 1);
    expect(retagFaceCrops).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("does not offer for another plugin with a same-named setting", async () => {
    const wrapper = mountPanel(
      { whole_face_crop: false },
      { ...PLUGIN, name: "someone_elses_tagger" },
    );
    await saveWith(wrapper, true);

    expect(retagFaceCrops).not.toHaveBeenCalled();
  });

  it("does not ask when no picture has a face", async () => {
    retagFaceCrops.mockResolvedValue({ count: 0 });
    const wrapper = mountPanel({ whole_face_crop: false });
    await saveWith(wrapper, true);

    expect(confirm).not.toHaveBeenCalled();
  });

  it("does not offer when the save fails", async () => {
    patchUserConfig.mockRejectedValue(new Error("nope"));
    const wrapper = mountPanel({ whole_face_crop: false });
    await saveWith(wrapper, true);

    expect(retagFaceCrops).not.toHaveBeenCalled();
  });
});

describe("TaggerPluginSettingsPanel: saves as you go", () => {
  const OFFSET_PLUGIN = {
    name: "pixlstash_tagger",
    parameter_schema: [
      { name: "threshold_offset", type: "number", default: 0 },
      { name: "whole_face_crop", type: "bool", default: false },
    ],
  };

  beforeEach(() => {
    vi.useFakeTimers();
    patchUserConfig.mockReset().mockResolvedValue({});
    retagFaceCrops.mockReset().mockResolvedValue({ count: 0 });
  });

  const saved = () =>
    patchUserConfig.mock.calls.map(
      (c) => c[0].tagger_settings.plugins.pixlstash_tagger.params,
    );

  it("coalesces typing into one save after the pause", async () => {
    const wrapper = mountPanel({ threshold_offset: 0 }, OFFSET_PLUGIN);
    form(wrapper).vm.$emit("update:modelValue", {
      threshold_offset: 0.1,
      whole_face_crop: false,
    });
    await vi.advanceTimersByTimeAsync(200);
    form(wrapper).vm.$emit("update:modelValue", {
      threshold_offset: 0.12,
      whole_face_crop: false,
    });
    await vi.advanceTimersByTimeAsync(499);
    expect(patchUserConfig).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await flushPromises();

    expect(saved()).toEqual([{ threshold_offset: 0.12, whole_face_crop: false }]);
    expect(wrapper.text()).toContain("Saved.");
    expect(wrapper.findAll("button").map((b) => b.text())).not.toContain(
      "Save",
    );
  });

  it("does not save a number box cleared mid-edit", async () => {
    const wrapper = mountPanel({ threshold_offset: 0.1 }, OFFSET_PLUGIN);
    await edit(wrapper, { threshold_offset: null });

    expect(patchUserConfig).not.toHaveBeenCalled();
  });

  it("still saves the other fields while a number box is left empty", async () => {
    const wrapper = mountPanel(
      { threshold_offset: 0.1, whole_face_crop: false },
      OFFSET_PLUGIN,
    );
    await edit(wrapper, { threshold_offset: null });
    await edit(wrapper, { whole_face_crop: true });

    // The empty box keeps its stored value rather than blocking the save.
    expect(saved()).toEqual([{ threshold_offset: 0.1, whole_face_crop: true }]);
  });


  it("resets to defaults and saves that", async () => {
    const wrapper = mountPanel(
      { threshold_offset: 0.3, whole_face_crop: false },
      OFFSET_PLUGIN,
    );
    const reset = wrapper
      .findAll("button")
      .find((b) => b.text() === "Reset to defaults");
    await reset.trigger("click");
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();

    expect(saved()).toEqual([{ threshold_offset: 0, whole_face_crop: false }]);
  });

  it("saves a pending edit when Settings closes inside the pause", async () => {
    const wrapper = mountPanel({ threshold_offset: 0 }, OFFSET_PLUGIN);
    form(wrapper).vm.$emit("update:modelValue", {
      threshold_offset: 0.2,
      whole_face_crop: false,
    });
    wrapper.unmount();
    await flushPromises();

    expect(saved()).toEqual([{ threshold_offset: 0.2, whole_face_crop: false }]);
  });

  // The save echoes back through `settings`; that must not overwrite what was
  // typed while the save was in flight.
  it("keeps an edit typed while the previous save was in flight", async () => {
    let finish;
    patchUserConfig.mockImplementationOnce(
      () => new Promise((resolve) => (finish = resolve)),
    );
    const wrapper = mountPanel({ threshold_offset: 0 }, OFFSET_PLUGIN);
    await edit(wrapper, { threshold_offset: 0.1 });
    form(wrapper).vm.$emit("update:modelValue", {
      threshold_offset: 0.15,
      whole_face_crop: false,
    });
    finish({});
    await flushPromises();
    const echoed = wrapper.emitted("update:settings")[0][0];
    await wrapper.setProps({ settings: echoed });

    expect(wrapper.vm.formParams.threshold_offset).toBe(0.15);
    await vi.advanceTimersByTimeAsync(500);
    await flushPromises();
    expect(saved().at(-1).threshold_offset).toBe(0.15);
  });

  it("saves an edit whose pause ends while the last save is still in flight", async () => {
    let finish;
    patchUserConfig.mockImplementationOnce(
      () => new Promise((resolve) => (finish = resolve)),
    );
    const wrapper = mountPanel({ threshold_offset: 0 }, OFFSET_PLUGIN);
    await edit(wrapper, { threshold_offset: 0.1 });
    await edit(wrapper, { threshold_offset: 0.2 });
    expect(patchUserConfig).toHaveBeenCalledOnce();
    finish({});
    await flushPromises();

    expect(saved().map((p) => p.threshold_offset)).toEqual([0.1, 0.2]);
  });

  // The re-save runs before the first save's echo reaches `settings`.
  it("offers the re-check once when a re-save follows the switch", async () => {
    retagFaceCrops.mockResolvedValue({ count: 3 });
    const confirmMock = confirm.mockReset().mockResolvedValue(false);
    let finish;
    patchUserConfig.mockImplementationOnce(
      () => new Promise((resolve) => (finish = resolve)),
    );
    const wrapper = mountPanel(
      { threshold_offset: 0, whole_face_crop: false },
      OFFSET_PLUGIN,
    );
    await edit(wrapper, { whole_face_crop: true });
    await edit(wrapper, { threshold_offset: 0.1 });
    finish({});
    await flushPromises();

    expect(saved().length).toBe(2);
    expect(confirmMock).toHaveBeenCalledOnce();
  });
});
