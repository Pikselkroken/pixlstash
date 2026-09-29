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

async function saveWith(wrapper, whole_face_crop) {
  wrapper.vm.formParams.whole_face_crop = whole_face_crop;
  await wrapper.vm.save();
  await flushPromises();
}

describe("TaggerPluginSettingsPanel: whole-face re-check offer", () => {
  beforeEach(() => {
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

    expect(patchUserConfig).toHaveBeenCalledOnce();
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
