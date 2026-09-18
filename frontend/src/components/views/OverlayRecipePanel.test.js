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
const copyText = vi.hoisted(() => vi.fn(async () => true));
vi.mock("../../utils/clipboard", () => ({ copyText }));

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
  negativePrompt: "blurry, watermark",
  seedText: "18446744073709551615",
  settings: [
    { label: "steps", value: 20, node: "KSampler" },
    { label: "cfg", value: 2, node: "KSampler" },
    { label: "sampler_name", value: "euler", node: "KSampler" },
    { label: "scheduler", value: "sgm_uniform", node: "KSampler" },
    { label: "width", value: 832, node: "EmptyLatentImage" },
    { label: "height", value: 1216, node: "EmptyLatentImage" },
  ],
  inputs: [
    {
      node_ref: "7",
      position: 0,
      pixel_sha: "a".repeat(64),
      input_picture_id: 42,
    },
    {
      node_ref: "8",
      position: 0,
      pixel_sha: "b".repeat(64),
      input_picture_id: null,
    },
  ],
};

function render(props = {}) {
  return mount(OverlayRecipePanel, {
    props: { recipe: RECIPE, ...props },
    global: {
      stubs: {
        "v-icon": true,
        Tooltip: true,
        // Bound, not merely declared: a stub that swallows `disabled` would
        // make the assertion below pass however the panel behaved.
        AppButton: {
          props: ["disabled"],
          template: '<button :disabled="disabled"><slot/></button>',
        },
      },
    },
  });
}

/** The Settings grid as `{label: value}`, in the order it is drawn. */
function settingsOf(wrapper) {
  return wrapper
    .findAll(".recipe-kv-item")
    .map((row) => [row.find("dt").text(), row.find("dd").text()]);
}

beforeEach(() => {
  nav.push.mockClear();
  copyText.mockClear();
});

describe("OverlayRecipePanel", () => {
  it("says so, rather than drawing an empty tab, with no recipe", () => {
    const wrapper = mount(OverlayRecipePanel, {
      props: { recipe: null },
      global: { stubs: { "v-icon": true, Tooltip: true, AppButton: true } },
    });
    expect(wrapper.find(".recipe-empty").exists()).toBe(true);
    expect(wrapper.find(".recipe-chip").exists()).toBe(false);
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

  it("ticks only the model the recipe named by its digest", () => {
    // The badge is the feature's whole claim: a ticked chip says "this exact
    // file", an unticked one says "a file called that". Asserted per chip, so
    // inverting the condition cannot leave the suite green.
    const badged = render()
      .findAll(".recipe-chip")
      .map((chip) => chip.find(".recipe-chip-badge").exists());
    expect(badged).toEqual([false, true, false]);
  });

  it("copies the prompt from the Prompt heading", async () => {
    const wrapper = render();
    const promptHeading = wrapper
      .findAll(".recipe-sec")
      .find((sec) => sec.text().startsWith("Prompt"));
    await promptHeading.find(".recipe-sec-act").trigger("click");
    expect(copyText).toHaveBeenCalledWith(RECIPE.positive_prompt);
  });

  // The v1.12 design drops the raw graph; it is kept because pasting a
  // workflow into ComfyUI and saving the JSON are both real uses of it.
  it("still offers the workflow JSON, and downloads it in either format", () => {
    const box = render().find(".recipe-details");
    expect(box.exists()).toBe(true);
    expect(box.find("textarea").element.value).toBe(
      JSON.stringify(RECIPE.workflow, null, 2),
    );
    expect(
      box.findAll(".recipe-sec-act").map((b) => b.text().trim()),
    ).toContain("Download");
  });

  // An API graph is not what the ComfyUI editor opens, so copying one to paste
  // back offers something that cannot work. The Metadata panel this box came
  // from guarded it the same way.
  it("offers Copy for the editor's format only", async () => {
    const editorFormat = render({
      recipe: { ...RECIPE, isApiFormat: false },
    });
    const copy = editorFormat
      .find(".recipe-details")
      .findAll(".recipe-sec-act")
      .find((b) => b.text().includes("Copy"));
    expect(copy).toBeDefined();
    await copy.trigger("click");
    expect(copyText).toHaveBeenCalledWith(
      JSON.stringify(RECIPE.workflow, null, 2),
    );

    // RECIPE is API-format, so the same box offers no Copy at all.
    const apiFormat = render();
    expect(
      apiFormat
        .find(".recipe-details")
        .findAll(".recipe-sec-act")
        .some((b) => b.text().includes("Copy")),
    ).toBe(false);
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
      .findAll(".recipe-sec-act")
      .find((b) => b.text().includes("Open"));
    await open.trigger("click");
    expect(nav.push).toHaveBeenCalledWith({
      name: "workflows",
      query: { topology: "f00d" },
    });
  });

  it("writes the settings the way the design draws them", () => {
    // Sampler and Size are each two graph settings written as one value, and
    // Seed and Negative are rows of the same grid rather than sections.
    expect(settingsOf(render())).toEqual([
      ["Steps", "20"],
      ["CFG", "2"],
      ["Sampler", "euler · sgm_uniform"],
      ["Size", "832×1216"],
      ["Seed", "18446744073709551615"],
      ["Negative", "blurry, watermark"],
    ]);
  });

  // The seam the two sides drifted across: `picture_recipe_service._settings`
  // qualifies a label with its node when two nodes disagree, and this panel used
  // to look the bare name up, so a hires-fix graph showed no Steps and no CFG.
  // These are the labels that service actually emits for such a graph - copied
  // from its output, not invented here.
  it("keeps both samplers' settings on a hires-fix graph", () => {
    const rows = settingsOf(
      render({
        recipe: {
          ...RECIPE,
          settings: [
            { label: "steps (KSampler)", value: 20, node: "KSampler" },
            { label: "cfg (KSampler)", value: 7.0, node: "KSampler" },
            { label: "sampler_name", value: "euler", node: "KSampler" },
            { label: "steps (Hires Fix)", value: 8, node: "Hires Fix" },
            { label: "cfg (Hires Fix)", value: 5.0, node: "Hires Fix" },
          ],
        },
      }),
    );
    expect(rows).toEqual([
      ["Steps (KSampler)", "20"],
      ["Steps (Hires Fix)", "8"],
      ["CFG (KSampler)", "7"],
      ["CFG (Hires Fix)", "5"],
      ["Sampler", "euler"],
      ["Seed", RECIPE.seedText],
      ["Negative", RECIPE.negativePrompt],
    ]);
  });

  it("pairs a sampler with its own node's scheduler, not another's", () => {
    const rows = Object.fromEntries(
      settingsOf(
        render({
          recipe: {
            ...RECIPE,
            settings: [
              { label: "sampler_name (Base)", value: "euler", node: "Base" },
              { label: "scheduler (Base)", value: "normal", node: "Base" },
              {
                label: "sampler_name (Refiner)",
                value: "dpmpp_2m",
                node: "Refiner",
              },
              {
                label: "scheduler (Refiner)",
                value: "karras",
                node: "Refiner",
              },
            ],
          },
        }),
      ),
    );
    expect(rows["Sampler (Base)"]).toBe("euler · normal");
    expect(rows["Sampler (Refiner)"]).toBe("dpmpp_2m · karras");
  });

  it("prints a 64-bit seed exactly, which a JS number cannot hold", () => {
    // 18446744073709551615 parsed as a Number renders 18446744073709552000.
    const seed = settingsOf(render()).find(([label]) => label === "Seed")[1];
    expect(seed).toBe(RECIPE.seedText);
  });

  it("says none for a seed or a negative prompt the recipe does not have", () => {
    const rows = Object.fromEntries(
      settingsOf(
        render({ recipe: { ...RECIPE, seedText: null, negativePrompt: null } }),
      ),
    );
    expect(rows.Seed).toBe("none");
    expect(rows.Negative).toBe("none");
  });

  it("leaves out a setting the graph never set", () => {
    const labels = settingsOf(
      render({ recipe: { ...RECIPE, settings: [] } }),
    ).map(([label]) => label);
    // Only the two that always speak, because their absence is itself a fact.
    expect(labels).toEqual(["Seed", "Negative"]);
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

  it("falls back to the gone tile when a thumbnail will not load", async () => {
    // Otherwise the browser's broken-image glyph reads as the panel being
    // broken rather than as the picture being unshowable.
    const wrapper = render();
    await wrapper.findAll(".recipe-input")[0].find("img").trigger("error");
    const tile = wrapper.findAll(".recipe-input")[0];
    expect(tile.find("img").exists()).toBe(false);
    expect(tile.classes()).toContain("recipe-input--gone");
  });

  // The design keeps the run button visible and disabled rather than removing
  // it, with the reason as its tooltip - the same shape it gives an A1111
  // picture, whose recipe cannot be replayed either.
  it("keeps the run button visible but disabled when it cannot run", () => {
    const button = render().find(".recipe-run");
    expect(button.exists()).toBe(true);
    expect(button.attributes("disabled")).toBeDefined();
  });

  it("asks for a run when it can", async () => {
    const wrapper = render({ canGenerateVariants: true });
    const button = wrapper.find(".recipe-run");
    expect(button.attributes("disabled")).toBeUndefined();
    await button.trigger("click");
    expect(wrapper.emitted("generate-variants")).toHaveLength(1);
  });
});
