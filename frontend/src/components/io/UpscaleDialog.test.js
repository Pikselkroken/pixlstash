// Upscale… (#1868): what the dialog promises before the owner sends it - the
// size each picture goes to, a warning on a picture its run already upscaled -
// and that it sends the one remembered factor and model the Run popup uses.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const preflightUpscale = vi.fn();
const upscalePictures = vi.fn();
const listAdapters = vi.fn();

vi.mock("../../api/workflows", () => ({
  preflightUpscale: (...args) => preflightUpscale(...args),
  upscalePictures: (...args) => upscalePictures(...args),
}));
vi.mock("../../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
}));

import UpscaleDialog from "./UpscaleDialog.vue";
import { useUpscaleStore } from "../../stores/useUpscaleStore";
import { useNoticeStore } from "../../stores/useNoticeStore";

const AppDialogStub = {
  name: "AppDialog",
  props: { open: Boolean, title: String, size: String },
  emits: ["close", "accept"],
  template: `<div class="dlg"><h2 class="dlg-title">{{ title }}</h2><slot />
    <div class="dlg-footer"><slot name="footer" /></div></div>`,
};

const UPSCALER = "b".repeat(64);

function picture(id, upscaled = false) {
  return { picture_id: id, width: 1024, height: 1024, upscaled };
}

async function open(ids) {
  const store = useUpscaleStore();
  store.openFor(ids);
  const wrapper = mount(UpscaleDialog, {
    global: { stubs: { "v-icon": true, AppDialog: AppDialogStub } },
  });
  await flushPromises();
  return { wrapper, store };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  window.localStorage.removeItem("pixlstash:upscale");
  listAdapters.mockResolvedValue([
    { sha256: UPSCALER, filename: "4x-UltraSharp.pth" },
  ]);
  upscalePictures.mockResolvedValue({ status: "success", runs: 3, prompts: [] });
});

describe("UpscaleDialog", () => {
  it("names the count and the size the pictures go to", async () => {
    preflightUpscale.mockResolvedValue({
      pictures: [picture(1), picture(2), picture(3)],
    });
    const { wrapper } = await open([1, 2, 3]);
    expect(wrapper.find(".dlg-title").text()).toBe("Upscale 3 pictures");
    expect(wrapper.find("[data-testid=upscale-target]").text()).toBe(
      "→ 2048 × 2048",
    );
    expect(wrapper.find("[data-testid=upscale-again]").exists()).toBe(false);
  });

  it("warns, without skipping it, about a picture its run upscaled", async () => {
    preflightUpscale.mockResolvedValue({
      pictures: [picture(1), picture(2, true), picture(3)],
    });
    const { wrapper } = await open([1, 2, 3]);
    expect(wrapper.find("[data-testid=upscale-again]").text()).toBe(
      "1 of these was already upscaled in its run and would go to 2048 × 2048.",
    );
    await wrapper.find("[data-testid=upscale-submit]").trigger("click");
    await flushPromises();
    expect(upscalePictures).toHaveBeenCalledWith([1, 2, 3], {
      factor: 2,
      model_sha256: UPSCALER,
    });
  });

  it("sends the factor picked and closes with a receipt", async () => {
    preflightUpscale.mockResolvedValue({ pictures: [picture(7)] });
    const { wrapper, store } = await open([7]);
    expect(wrapper.find(".dlg-title").text()).toBe("Upscale picture");
    await wrapper.findAll(".seg__opt").at(0).trigger("click");
    await wrapper.find("[data-testid=upscale-submit]").trigger("click");
    await flushPromises();
    expect(upscalePictures).toHaveBeenCalledWith([7], {
      factor: 1.5,
      model_sha256: UPSCALER,
    });
    expect(store.pictureIds).toBe(null);
    expect(useNoticeStore().notices.map((n) => n.text)).toContain(
      "Started 1 upscale in ComfyUI.",
    );
  });

  it("stays open and says why when the upscale is refused", async () => {
    preflightUpscale.mockResolvedValue({ pictures: [picture(7)] });
    upscalePictures.mockRejectedValue({
      response: { status: 503, data: { detail: "ComfyUI is not running." } },
    });
    const { wrapper, store } = await open([7]);
    await wrapper.find("[data-testid=upscale-submit]").trigger("click");
    await flushPromises();
    expect(store.pictureIds).toEqual([7]);
    const [notice] = useNoticeStore().notices;
    expect(notice.level).toBe("error");
    expect(notice.text).toContain("ComfyUI is not running.");
  });
});
