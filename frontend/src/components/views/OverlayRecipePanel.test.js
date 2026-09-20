// The lightbox's Recipe section (#1313): what it draws, and where it sends you.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

// Mocked rather than given a real router: the panel is mounted alone, and a
// real one would have to carry every app route to resolve two names.
const nav = vi.hoisted(() => ({ push: null }));
vi.mock("vue-router", async () => {
  const { vi: vitest } = await import("vitest");
  nav.push = vitest.fn();
  return { useRouter: () => ({ push: nav.push }) };
});

// A real ref: the panel reads `isReadOnly` to tell a share-link reader's
// refusal apart from an unconfigured machine's (#1406).
const isReadOnly = vi.hoisted(() => ({ value: false }));
vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  isReadOnly,
}));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/api/v1/pictures/thumbnails/${id}.webp`,
}));
const copyText = vi.hoisted(() => vi.fn(async () => true));
vi.mock("../../utils/clipboard", () => ({ copyText }));
const getPictureWorkflow = vi.hoisted(() => vi.fn());
vi.mock("../../api/comfyui", () => ({ getPictureWorkflow }));

import OverlayRecipePanel from "./OverlayRecipePanel.vue";

const RECIPE = {
  summary: "API Workflow · 12 nodes",
  isApiFormat: true,
  positive_prompt: "a castle on a hill",
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
  // One settings reading for the whole app: `extract_recipe_extras`' dict,
  // which the Remix dialog reads too.
  settings: {
    steps: 20,
    cfg: 2,
    sampler_name: "euler",
    scheduler: "sgm_uniform",
    width: 832,
    height: 1216,
  },
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
    props: {
      recipe: RECIPE,
      pictureId: 7,
      // The default is a working machine, so a test that says nothing about
      // the machine is testing the picture, not the setup.
      comfyuiConfigured: true,
      ...props,
    },
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

const GRAPH = { 4: { class_type: "CheckpointLoaderSimple" } };

beforeEach(() => {
  nav.push.mockClear();
  copyText.mockClear();
  getPictureWorkflow.mockReset();
  getPictureWorkflow.mockResolvedValue({
    workflow: GRAPH,
    is_api_format: false,
  });
});

/** Open the workflow box and let its lazy read land. */
async function openGraph(wrapper) {
  const box = wrapper.find(".recipe-details");
  box.element.open = true;
  await box.trigger("toggle");
  await flushPromises();
  return box;
}

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
  // workflow into ComfyUI and saving the JSON are both real uses of it. It is
  // fetched only when the box is opened: it is the one large thing here, and
  // the recipe read runs on every filmstrip step.
  it("reads the graph only when the box is opened", async () => {
    const wrapper = render();
    expect(getPictureWorkflow).not.toHaveBeenCalled();
    const box = await openGraph(wrapper);
    expect(getPictureWorkflow).toHaveBeenCalledWith(7);
    expect(box.find("textarea").element.value).toBe(
      JSON.stringify(GRAPH, null, 2),
    );
  });

  // An API graph is not what the ComfyUI editor opens, so copying one to paste
  // back offers something that cannot work. The Metadata panel this box came
  // from guarded it the same way.
  it("offers Copy for the editor's format only", async () => {
    const editorFormat = await openGraph(render());
    const copy = editorFormat
      .findAll(".recipe-sec-act")
      .find((b) => b.text().includes("Copy"));
    expect(copy).toBeDefined();
    await copy.trigger("click");
    expect(copyText).toHaveBeenCalledWith(JSON.stringify(GRAPH, null, 2));

    getPictureWorkflow.mockResolvedValue({
      workflow: GRAPH,
      is_api_format: true,
    });
    const apiFormat = await openGraph(render());
    expect(
      apiFormat
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
    // Sampler in the design's order, Size built from width and height, and
    // Seed and Negative as rows of the same grid rather than sections.
    expect(settingsOf(render())).toEqual([
      ["Steps", "20"],
      ["CFG", "2"],
      ["Sampler", "euler"],
      ["Scheduler", "sgm_uniform"],
      ["Size", "832×1216"],
      ["Seed", RECIPE.seedText],
      ["Negative", RECIPE.negativePrompt],
    ]);
  });

  // The same field carries A1111's own vocabulary for an A1111 picture, so the
  // rows are rendered from whatever keys arrive rather than by branching on the
  // source - which is the point of there being one settings contract.
  it("renders an A1111 picture's settings through the same grid", () => {
    const rows = Object.fromEntries(
      settingsOf(
        render({
          recipe: {
            ...RECIPE,
            source: "a1111",
            settings: {
              steps: 25,
              sampler: "Euler a",
              cfg_scale: 7,
              size: "512x768",
            },
          },
        }),
      ),
    );
    expect(rows).toMatchObject({
      Steps: "25",
      CFG: "7",
      Sampler: "Euler a",
      Size: "512x768",
    });
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
  //
  // `aria-disabled` and NOT the native attribute (#1406): a natively-disabled
  // button is out of the tab order, so a keyboard reader could never reach the
  // reason `aria-describedby` points at.
  it("keeps the run button visible but disabled when it cannot run", () => {
    const wrapper = render();
    const button = wrapper.find(".recipe-run");
    expect(button.exists()).toBe(true);
    expect(button.attributes("aria-disabled")).toBe("true");
    expect(button.attributes("disabled")).toBeUndefined();
  });

  it("says why it cannot run, in prose and as the button's description", () => {
    const wrapper = render();
    const reason = wrapper.find(".recipe-run-reason");
    expect(reason.exists()).toBe(true);
    expect(wrapper.find(".recipe-run").attributes("aria-describedby")).toBe(
      reason.attributes("id"),
    );
  });

  // The whole point of the four reasons is that they are NOT interchangeable:
  // each sends the reader somewhere different, and three of them would be a
  // lie in the other two's situations.
  it("blames the machine only when the machine is the problem", () => {
    const wrapper = render({ comfyuiConfigured: false });
    expect(wrapper.find(".recipe-run-reason").text()).toContain(
      "ComfyUI is not connected",
    );
  });

  it("blames the session, not the machine, on a read-only view", () => {
    // A share link CAN read a recipe - the route is picture-scoped - so this
    // is a real reader looking at a real recipe on a machine that may well
    // have ComfyUI running. Telling them it is not connected is false, and
    // sends them to a settings screen they cannot open.
    isReadOnly.value = true;
    try {
      const wrapper = render({ comfyuiConfigured: true });
      const text = wrapper.find(".recipe-run-reason").text();
      expect(text).toContain("read-only");
      expect(text).not.toContain("ComfyUI is not connected");
      expect(wrapper.find(".recipe-run").attributes("aria-disabled")).toBe(
        "true",
      );
    } finally {
      isReadOnly.value = false;
    }
  });

  it("disables the input action for a read-only view, as the menus do", () => {
    // Present but dead, which is what both context-menu copies of this action
    // do (`:disabled="isReadOnly"`). Hiding it here and disabling it there
    // would make one action behave two ways.
    isReadOnly.value = true;
    try {
      const wrapper = render({ comfyuiConfigured: true });
      const button = wrapper
        .findAll("button")
        .find((b) => b.text().includes("Run another workflow"));
      expect(button).toBeDefined();
      expect(button.attributes("aria-disabled")).toBe("true");
    } finally {
      isReadOnly.value = false;
    }
  });

  it("does not act when the dead input action is clicked anyway", async () => {
    isReadOnly.value = true;
    try {
      const wrapper = render({ comfyuiConfigured: true });
      const button = wrapper
        .findAll("button")
        .find((b) => b.text().includes("Run another workflow"));
      await button.trigger("click");
      expect(wrapper.emitted("use-as-input")).toBeUndefined();
    } finally {
      isReadOnly.value = false;
    }
  });

  it("describes each button by its own reason when the two differ", () => {
    // A read-only reader looking at an A1111 picture is refused twice, for two
    // different reasons: pointing both buttons at one sentence would describe
    // one of them with the other's problem.
    isReadOnly.value = true;
    try {
      const wrapper = render({
        recipe: { ...RECIPE, source: "a1111", available: false },
        comfyuiConfigured: true,
      });
      const run = wrapper.find(".recipe-run");
      const input = wrapper
        .findAll("button")
        .find((b) => b.text().includes("Run another workflow"));
      const runDesc = run.attributes("aria-describedby");
      const inputDesc = input.attributes("aria-describedby");
      expect(runDesc).toBeTruthy();
      expect(inputDesc).toBeTruthy();
      expect(inputDesc).not.toBe(runDesc);
      expect(wrapper.find(`#${runDesc}`).text()).toContain("A1111 or Forge");
      expect(wrapper.find(`#${inputDesc}`).text()).toContain("read-only");
    } finally {
      isReadOnly.value = false;
    }
  });

  it("shares one sentence when the two refusals coincide", () => {
    // Read-only, ordinary ComfyUI picture: both buttons are refused for the
    // same reason, and it is printed once.
    isReadOnly.value = true;
    try {
      const wrapper = render({ comfyuiConfigured: true });
      const input = wrapper
        .findAll("button")
        .find((b) => b.text().includes("Run another workflow"));
      expect(input.attributes("aria-describedby")).toBe(
        wrapper.find(".recipe-run").attributes("aria-describedby"),
      );
      expect(wrapper.findAll(".recipe-run-reason")).toHaveLength(1);
    } finally {
      isReadOnly.value = false;
    }
  });

  it("hides the input action with no ComfyUI to run anything on", () => {
    const wrapper = render({ comfyuiConfigured: false });
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Run another workflow")),
    ).toBe(false);
  });

  it("asks for a run when it can", async () => {
    const wrapper = render({ canGenerateVariants: true });
    const button = wrapper.find(".recipe-run");
    expect(button.attributes("aria-disabled")).toBeUndefined();
    expect(wrapper.find(".recipe-run-reason").exists()).toBe(false);
    await button.trigger("click");
    expect(wrapper.emitted("run")).toHaveLength(1);
  });

  // An A1111 picture fills the whole tab and still cannot be replayed: there
  // is no graph for ComfyUI to run. The reason has to say that rather than
  // "ComfyUI is not connected", which would send the reader to the settings.
  it("refuses an A1111 picture for its own reason, with ComfyUI connected", async () => {
    const wrapper = render({
      recipe: { ...RECIPE, source: "a1111", available: false, reason: "a1111" },
      canGenerateVariants: true,
    });
    const button = wrapper.find(".recipe-run");
    expect(button.attributes("aria-disabled")).toBe("true");
    expect(wrapper.find(".recipe-run-reason").text()).toContain(
      "A1111 or Forge",
    );
    await button.trigger("click");
    expect(wrapper.emitted("run")).toBeUndefined();
  });

  it("names the graph's own reason when the server gave one", () => {
    const wrapper = render({
      recipe: { ...RECIPE, available: false, reason: "no_seed_input" },
      canGenerateVariants: true,
    });
    expect(wrapper.find(".recipe-run-reason").text()).toContain(
      "no seed to change",
    );
  });

  // ── The editor graph ──────────────────────────────────────────────────
  //
  // A picture that carries ComfyUI's editor graph and not the API one the
  // server ran. PixlStash rebuilds it to run it; a rebuild that could not be
  // exact is refused, and the refusal has to say which node stopped it, or the
  // reader is sent nowhere.

  const EDITOR_REFUSAL = {
    ...RECIPE,
    available: false,
    reason: "editor_graph",
    summary: "Editor Workflow · 4 nodes · 3 links",
    conversionProblems: [
      "this ComfyUI has no node class 'SomeCustomPackNode'",
      "KSampler (node 3) carries 5 widget values, and its inputs account for 4",
    ],
  };

  it("explains an editor graph that could not be rebuilt", () => {
    const wrapper = render({
      recipe: EDITOR_REFUSAL,
      canGenerateVariants: true,
    });
    expect(wrapper.find(".recipe-run-reason").text()).toContain(
      "could not rebuild",
    );
    // Not the fallback sentence: an unmapped reason reads "cannot be run
    // again", which would say nothing about what to go and fix.
    expect(wrapper.find(".recipe-run-reason").text()).not.toContain(
      "This picture's recipe cannot be run again",
    );
  });

  it("lists each thing that stopped the rebuild", () => {
    const wrapper = render({
      recipe: EDITOR_REFUSAL,
      canGenerateVariants: true,
    });
    const problems = wrapper
      .findAll(".recipe-run-problems li")
      .map((li) => li.text());
    expect(problems).toEqual(EDITOR_REFUSAL.conversionProblems);
  });

  it("shows the recipe itself even though it cannot be run", () => {
    // The distinction the whole path exists for: this picture WAS made in
    // ComfyUI, so the prompt and the models are there to read.
    const wrapper = render({ recipe: EDITOR_REFUSAL });
    expect(wrapper.find(".recipe-workflow").text()).toBe(
      "Editor Workflow · 4 nodes · 3 links",
    );
    expect(wrapper.find(".recipe-prompt").text()).toBe(RECIPE.positive_prompt);
  });

  it("says the negative prompt was not read, never that there is none", () => {
    // "none" is a claim about the workflow. This workflow was not read, so
    // making it would be telling the reader something PixlStash does not know.
    const wrapper = render({ recipe: { ...EDITOR_REFUSAL, negativePrompt: null } });
    const negative = settingsOf(wrapper).find(([label]) => label === "Negative");
    expect(negative).toBeDefined();
    expect(negative[1]).toBe("not read");
  });

  it("still says none on a graph it DID read", () => {
    // The control: over-reporting "not read" would hide a real fact, which is
    // its own regression.
    const wrapper = render({ recipe: { ...RECIPE, negativePrompt: null } });
    const negative = settingsOf(wrapper).find(([label]) => label === "Negative");
    expect(negative[1]).toBe("none");
  });

  it("keeps the seed it did read off a refused editor graph", () => {
    // The seed comes off the editor graph directly, so it is known even when
    // the rebuild failed - blanket "not read" would throw that away.
    const wrapper = render({ recipe: EDITOR_REFUSAL });
    const seed = settingsOf(wrapper).find(([label]) => label === "Seed");
    expect(seed[1]).toBe(RECIPE.seedText);
  });

  it("prints no problem list for every other recipe", () => {
    const wrapper = render({ recipe: RECIPE, canGenerateVariants: true });
    expect(wrapper.find(".recipe-run-problems").exists()).toBe(false);
  });

  it("offers the picture as a workflow's input, whatever Run… says", async () => {
    const wrapper = render({
      recipe: { ...RECIPE, source: "a1111", available: false, reason: "a1111" },
      comfyuiConfigured: true,
    });
    const useAsInput = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run another workflow"));
    expect(useAsInput).toBeDefined();
    await useAsInput.trigger("click");
    expect(wrapper.emitted("use-as-input")).toHaveLength(1);
  });
});
