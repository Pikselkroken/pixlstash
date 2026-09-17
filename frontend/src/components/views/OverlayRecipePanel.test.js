// The lightbox's Recipe section (#1313): what it draws, and where it sends you.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";

// Mocked rather than given a real router: the panel is mounted alone, and a
// real one would have to carry every app route to resolve two names.
const nav = vi.hoisted(() => ({ push: null }));
vi.mock("vue-router", async () => {
  const { vi: vitest } = await import("vitest");
  nav.push = vitest.fn();
  return { useRouter: () => ({ push: nav.push }) };
});

vi.mock("../../utils/apiClient", () => ({ API_BASE_URL: "/api/v1" }));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/api/v1/pictures/thumbnails/${id}.webp`,
}));
vi.mock("../../utils/clipboard", () => ({ copyText: vi.fn(async () => true) }));

import OverlayRecipePanel from "./OverlayRecipePanel.vue";

const RECIPE = {
  summary: "API Workflow · 12 nodes",
  isApiFormat: true,
  positive_prompt: "a castle on a hill",
  workflow: { 4: { class_type: "CheckpointLoaderSimple" } },
  topologyHash: "f00d",
  modelSlots: [
    {
      name: "realvis.safetensors",
      widget: "ckpt_name",
      strength: null,
      model_id: 3,
      verified: false,
    },
    {
      name: "style.safetensors",
      widget: "lora_sha256",
      strength: 0.8,
      model_id: 7,
      verified: true,
    },
    {
      name: "gone.safetensors",
      widget: "lora_name",
      strength: 0.5,
      model_id: null,
      verified: false,
    },
  ],
  settings: [
    { label: "steps", value: 20, node: "KSampler" },
    { label: "sampler_name", value: "euler", node: "KSampler" },
  ],
  inputs: [
    { node_ref: "7", position: 0, pixel_sha: "a".repeat(64), input_picture_id: 42 },
    { node_ref: "8", position: 0, pixel_sha: "b".repeat(64), input_picture_id: null },
  ],
};

function render(props = {}) {
  return mount(OverlayRecipePanel, {
    props: { recipe: RECIPE, ...props },
    global: { stubs: { "v-icon": true, Tooltip: true } },
  });
}

beforeEach(() => {
  nav.push.mockClear();
});

describe("OverlayRecipePanel", () => {
  it("draws nothing at all for a picture with no recipe", () => {
    const wrapper = mount(OverlayRecipePanel, {
      props: { recipe: null },
      global: { stubs: { "v-icon": true, Tooltip: true } },
    });
    expect(wrapper.find(".sidebar-section--recipe").exists()).toBe(false);
  });

  it("names every model and the strength each LoRA was loaded at", () => {
    const chips = render().findAll(".recipe-chip");
    expect(chips.map((c) => c.find(".recipe-chip-name").text())).toEqual([
      "realvis.safetensors",
      "style.safetensors",
      "gone.safetensors",
    ]);
    // A checkpoint has no strength, so it prints none rather than a zero.
    expect(chips[0].find(".recipe-chip-strength").exists()).toBe(false);
    expect(chips[1].find(".recipe-chip-strength").text()).toBe("0.80");
  });

  it("makes a model on the shelf a link and one that is not inert", () => {
    const chips = render().findAll(".recipe-chip");
    expect(chips[0].element.tagName).toBe("BUTTON");
    expect(chips[2].element.tagName).toBe("SPAN");
  });

  it("opens the model shelf on the model the picture used", async () => {
    await render().findAll(".recipe-chip")[0].trigger("click");
    expect(nav.push).toHaveBeenCalledWith({
      name: "models",
      query: { model: "3" },
    });
  });

  it("opens the Workflows view on this picture's workflow", async () => {
    const wrapper = render();
    const open = wrapper
      .findAll(".recipe-action")
      .find((b) => b.text().includes("Open"));
    await open.trigger("click");
    expect(nav.push).toHaveBeenCalledWith({
      name: "workflows",
      query: { topology: "f00d" },
    });
  });

  it("writes the settings in the reader's words, not the graph's", () => {
    const rows = render().findAll(".recipe-setting");
    expect(rows.map((r) => r.find("dt").text())).toEqual(["Steps", "Sampler"]);
    expect(rows.map((r) => r.find("dd").text())).toEqual(["20", "euler"]);
  });

  it("shows a thumbnail per run input, and marks one that has left", () => {
    const inputs = render().findAll(".recipe-input");
    expect(inputs).toHaveLength(2);
    expect(inputs[0].find("img").attributes("src")).toBe(
      "/api/v1/pictures/thumbnails/42.webp",
    );
    expect(inputs[1].find("img").exists()).toBe(false);
    expect(inputs[1].classes()).toContain("recipe-input--gone");
  });

  it("offers Generate variants only when a run could actually start", async () => {
    expect(render().find(".recipe-action--primary").exists()).toBe(false);
    const wrapper = render({ canGenerateVariants: true });
    await wrapper.find(".recipe-action--primary").trigger("click");
    expect(wrapper.emitted("generate-variants")).toHaveLength(1);
  });
});
