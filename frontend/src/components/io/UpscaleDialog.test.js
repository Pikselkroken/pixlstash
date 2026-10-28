// Upscale… (#1868): what the dialog promises before the owner sends it - the
// pictures, the size each goes to, a warning on a picture its run already
// upscaled - that it sends the one remembered factor and model the Run popup
// uses, and that it offers RealESRGAN to a shelf without one.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const preflightUpscale = vi.fn();
const upscalePictures = vi.fn();
const listAdapters = vi.fn();
const startModelDownload = vi.fn();

vi.mock("../../api/workflows", () => ({
  preflightUpscale: (...args) => preflightUpscale(...args),
  upscalePictures: (...args) => upscalePictures(...args),
}));
vi.mock("../../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
}));

vi.mock("../../api/modelDownloads", () => ({
  startModelDownload: (...args) => startModelDownload(...args),
  cancelModelDownload: vi.fn(),
}));

import UpscaleDialog from "./UpscaleDialog.vue";
import { useUpscaleStore } from "../../stores/useUpscaleStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";

const AppDialogStub = {
  name: "AppDialog",
  props: { open: Boolean, title: String, subtitle: String, size: String },
  emits: ["close", "accept"],
  template: `<div class="dlg"><h2 class="dlg-title">{{ title }}</h2>
    <span class="dlg-subtitle">{{ subtitle }}</span><slot />
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
  upscalePictures.mockResolvedValue({
    status: "success",
    runs: 3,
    prompts: [],
  });
});

describe("UpscaleDialog", () => {
  it("names the count and the size the pictures go to", async () => {
    preflightUpscale.mockResolvedValue({
      pictures: [picture(1), picture(2), picture(3)],
    });
    const { wrapper } = await open([1, 2, 3]);
    expect(wrapper.find(".dlg-title").text()).toBe("Upscale 3 pictures");
    // In the title row, not under the factor.
    expect(wrapper.find(".dlg-subtitle").text()).toBe("→ 2048 × 2048");
    expect(wrapper.find("[data-testid=upscale-target]").exists()).toBe(false);
    const thumbs = wrapper.findAll("[data-testid=upscale-thumbs] img");
    expect(thumbs.map((img) => img.attributes("src"))).toEqual([
      expect.stringContaining("/pictures/thumbnails/1.webp"),
      expect.stringContaining("/pictures/thumbnails/2.webp"),
      expect.stringContaining("/pictures/thumbnails/3.webp"),
    ]);
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

  it("hands its runs to the grid to follow, as a Run popup does", async () => {
    // Followed with the one picture: the lightbox on it opens the result over
    // it, before / after.
    const prompts = [{ run_id: "r1", prompt_id: "p1" }];
    upscalePictures.mockResolvedValue({ status: "success", runs: 1, prompts });
    preflightUpscale.mockResolvedValue({ pictures: [picture(7)] });
    const { wrapper } = await open([7]);
    await wrapper.find("[data-testid=upscale-submit]").trigger("click");
    await flushPromises();
    expect(useRunDialogStore().startedRun).toEqual({
      prompts,
      pictureIds: [7],
      pictureId: 7,
    });
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

  it("shows four thumbnails and a count past five pictures", async () => {
    const ids = [1, 2, 3, 4, 5, 6, 7];
    preflightUpscale.mockResolvedValue({
      pictures: ids.map((id) => picture(id)),
    });
    const { wrapper } = await open(ids);
    expect(wrapper.findAll("[data-testid=upscale-thumbs] img")).toHaveLength(4);
    expect(wrapper.find(".upd-more").text()).toBe("+3");
  });

  it("leaves the size out of the title when the pictures differ", async () => {
    preflightUpscale.mockResolvedValue({
      pictures: [picture(1), { ...picture(2), width: 512 }],
    });
    const { wrapper } = await open([1, 2]);
    expect(wrapper.find(".dlg-subtitle").text()).toBe("");
  });

  it("offers RealESRGAN to a shelf without it, and picks it once it lands", async () => {
    preflightUpscale.mockResolvedValue({ pictures: [picture(7)] });
    startModelDownload.mockResolvedValue({
      id: "d1",
      file: "RealESRGAN_x4plus.safetensors",
      status: "running",
    });
    const { wrapper, store } = await open([7]);
    expect(store.model.sha256).toBe(UPSCALER);
    await wrapper
      .find("[data-testid=upscale-get-model] button")
      .trigger("click");
    await flushPromises();
    expect(startModelDownload).toHaveBeenCalledWith(
      "RealESRGAN_x4plus.safetensors",
    );
    // Running: said, and not offered a second time.
    expect(
      wrapper.find("[data-testid=upscale-get-model] button").exists(),
    ).toBe(false);

    const esrgan = "c".repeat(64);
    listAdapters.mockResolvedValue([
      { sha256: UPSCALER, filename: "4x-UltraSharp.pth" },
      { sha256: esrgan, filename: "RealESRGAN_x4plus.safetensors" },
    ]);
    useTasksStore().downloadsLanded += 1;
    await flushPromises();
    expect(wrapper.find("[data-testid=upscale-get-model]").exists()).toBe(
      false,
    );
    expect(store.model.sha256).toBe(esrgan);
  });

  it("does not offer RealESRGAN to a shelf that has it", async () => {
    listAdapters.mockResolvedValue([
      { sha256: UPSCALER, filename: "RealESRGAN_x2.pth" },
    ]);
    preflightUpscale.mockResolvedValue({ pictures: [picture(7)] });
    const { wrapper } = await open([7]);
    expect(wrapper.find("[data-testid=upscale-get-model]").exists()).toBe(
      false,
    );
  });
});
