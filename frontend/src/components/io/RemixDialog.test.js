// RemixDialog — mode availability, the honest unavailable reasons, and scope.
//
// The load-bearing behaviour is that "Same workflow, new seed" is offered ONLY
// when the source file actually carries an executable graph AND the server's
// pre-flight passed, and that when it is not offered the row still says WHY,
// in visible text, with the three causes worded differently: no embedded
// workflow, ComfyUI is missing something, ComfyUI could not be reached. A
// hover-only reason would be unreachable by keyboard and touch users, so the
// reason must be in the DOM, not in a title attribute.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick, h } from "vue";

const listWorkflows = vi.fn();
const getPictureRecipe = vi.fn();
const runImageToImage = vi.fn();
const runRecipe = vi.fn();

vi.mock("../../api/comfyui", () => ({
  listWorkflows: (...a) => listWorkflows(...a),
  getPictureRecipe: (...a) => getPictureRecipe(...a),
  runImageToImage: (...a) => runImageToImage(...a),
  runRecipe: (...a) => runRecipe(...a),
}));

// The dialog and the App* widgets it embeds import Vuetify components
// directly, which pull in CSS vitest cannot load; replace them with stubs.
vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
}));

import RemixDialog from "./RemixDialog.vue";

const VIcon = {
  name: "v-icon",
  setup:
    (_props, { slots }) =>
    () =>
      h("i", { class: "v-icon" }, slots.default?.()),
};

// AppDialog teleports through v-dialog; render its slots inline instead so the
// body and footer are in the wrapper's own tree.
const AppDialogStub = {
  name: "AppDialog",
  props: ["open", "title", "subtitle", "width", "persistent"],
  setup:
    (props, { slots }) =>
    () =>
      props.open
        ? h("div", { class: "dlg" }, [slots.default?.(), slots.footer?.()])
        : null,
};

const AppButtonStub = {
  name: "AppButton",
  props: ["variant", "iconLeft", "disabled"],
  emits: ["click"],
  setup:
    (props, { slots, emit }) =>
    () =>
      h(
        "button",
        {
          class: "app-btn",
          disabled: props.disabled,
          onClick: () => emit("click"),
        },
        slots.default?.(),
      ),
};

const globalOpts = {
  stubs: { "v-icon": VIcon, AppDialog: AppDialogStub, AppButton: AppButtonStub },
};

const IMAGE = { id: 42, file_name: "cat.png", description: "a cat on a mat" };

const CLEAN_RECIPE = {
  available: true,
  reason: null,
  summary: "API Workflow · 12 nodes",
  positive_prompt: "a cat, masterpiece",
  models: ["sd_xl_base_1.0.safetensors"],
  loras: [],
  node_count: 12,
  seed_inputs: [
    { node_id: "3", class_type: "KSampler", field: "seed", value: 1 },
  ],
  preflight: {
    ok: true,
    checked: true,
    missing_node_classes: [],
    missing_models: [],
    missing_input_images: [],
    unchecked_fields: 0,
  },
};

function mountDialog(props = {}) {
  return mount(RemixDialog, {
    props: { open: true, image: IMAGE, ...props },
    global: globalOpts,
  });
}

/** Let both the workflow list and the recipe check resolve. */
async function settle(w) {
  await nextTick();
  await nextTick();
  await nextTick();
  await nextTick();
  return w;
}

function modeRows(w) {
  return w.findAll('[role="radio"].remix-mode');
}

function rowFor(w, title) {
  return modeRows(w).find((r) => r.text().includes(title));
}

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
  listWorkflows.mockResolvedValue({
    workflows: [
      {
        name: "Edit.json",
        display_name: "Edit",
        valid: true,
        workflow_type: "i2i",
        missing_placeholders: [],
      },
      {
        name: "T2I.json",
        display_name: "T2I",
        valid: true,
        workflow_type: "t2i",
        missing_placeholders: ["{{image_path}}"],
      },
    ],
  });
  getPictureRecipe.mockResolvedValue(CLEAN_RECIPE);
  runImageToImage.mockResolvedValue({ prompts: [{ prompt_id: "p1" }] });
  runRecipe.mockResolvedValue({ prompts: [{ prompt_id: "p2" }] });
});

describe("RemixDialog mode availability", () => {
  it("offers recipe mode and defaults to it when the pre-flight is clean", async () => {
    const w = await settle(mountDialog());
    const recipe = rowFor(w, "Same workflow, new seed");
    expect(recipe.attributes("aria-disabled")).toBe("false");
    expect(recipe.attributes("aria-checked")).toBe("true");
  });

  it("falls back to template mode and says why when no graph is embedded", async () => {
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    const w = await settle(mountDialog());
    const recipe = rowFor(w, "Same workflow, new seed");
    expect(recipe.attributes("aria-disabled")).toBe("true");
    expect(recipe.attributes("aria-checked")).toBe("false");
    expect(recipe.text()).toContain("No executable workflow embedded");
    expect(rowFor(w, "Pick a template").attributes("aria-checked")).toBe("true");
  });

  it("names what ComfyUI is missing when the pre-flight fails", async () => {
    getPictureRecipe.mockResolvedValue({
      ...CLEAN_RECIPE,
      preflight: {
        ok: false,
        checked: true,
        missing_node_classes: ["UltimateSDUpscale"],
        missing_models: [{ value: "4x-UltraSharp.pth" }],
        missing_input_images: [],
        unchecked_fields: 0,
      },
    });
    const w = await settle(mountDialog());
    const recipe = rowFor(w, "Same workflow, new seed");
    expect(recipe.attributes("aria-disabled")).toBe("true");
    expect(recipe.text()).toContain("UltimateSDUpscale");
    expect(recipe.text()).toContain("4x-UltraSharp.pth");
  });

  it("words 'could not check' differently from 'checked and broken'", async () => {
    getPictureRecipe.mockResolvedValue({
      ...CLEAN_RECIPE,
      preflight: { ok: true, checked: false, error: "unreachable" },
    });
    const w = await settle(mountDialog());
    const recipe = rowFor(w, "Same workflow, new seed");
    expect(recipe.text()).toContain("Could not reach ComfyUI");
    expect(recipe.text()).not.toContain("missing");
  });

  it("refuses recipe mode for a graph with no random seed", async () => {
    getPictureRecipe.mockResolvedValue({
      ...CLEAN_RECIPE,
      available: false,
      reason: "no_seed_input",
      seed_inputs: [],
    });
    const w = await settle(mountDialog());
    expect(rowFor(w, "Same workflow, new seed").text()).toContain(
      "identical image",
    );
  });

  it("keeps the unavailable row keyboard-reachable rather than removing it", async () => {
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    const w = await settle(mountDialog());
    // Shown-disabled, not hidden: an absence is not recoverable, a stated
    // cause is. And `aria-disabled` (not `disabled`) keeps it traversable.
    expect(modeRows(w)).toHaveLength(2);
    const recipe = rowFor(w, "Same workflow, new seed");
    expect(recipe.attributes("disabled")).toBeUndefined();
  });

  it("does not switch mode when an unavailable row is activated", async () => {
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    const w = await settle(mountDialog());
    await rowFor(w, "Same workflow, new seed").trigger("click");
    await nextTick();
    expect(rowFor(w, "Pick a template").attributes("aria-checked")).toBe("true");
  });
});

describe("RemixDialog prompt prefill", () => {
  it("prefills from the image description and marks its provenance", async () => {
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    const w = await settle(mountDialog());
    expect(w.find("textarea").element.value).toBe("a cat on a mat");
    expect(w.find(".remix-provenance").text()).toContain(
      "from image description",
    );
  });

  it("swaps the provenance note for a reset button once edited", async () => {
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    const w = await settle(mountDialog());
    await w.find("textarea").setValue("make it snowing");
    await nextTick();
    expect(w.find(".remix-provenance").exists()).toBe(false);
    await w.find(".remix-link").trigger("click");
    await nextTick();
    expect(w.find("textarea").element.value).toBe("a cat on a mat");
  });

  it("ignores a pending-description sentinel rather than prefilling it", async () => {
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    const w = await settle(
      mountDialog({ image: { ...IMAGE, description: "__description::joycaption" } }),
    );
    expect(w.find("textarea").element.value).toBe("");
  });
});

describe("RemixDialog scope", () => {
  it("says nothing about scope for a single selection", async () => {
    const w = await settle(mountDialog({ selectedImageIds: [42] }));
    expect(w.find(".remix-scope").exists()).toBe(false);
  });

  it("discloses that a wider selection is not included, and routes to batch", async () => {
    const w = await settle(mountDialog({ selectedImageIds: [42, 43, 44] }));
    const scope = w.find(".remix-scope");
    expect(scope.text()).toContain("2 other selected images are not included");
    await scope.find("button").trigger("click");
    expect(w.emitted("use-batch")).toBeTruthy();
  });
});

describe("RemixDialog submit", () => {
  it("replays the recipe and hands the prompt ids off, then closes", async () => {
    const w = await settle(mountDialog({ clientId: "tab-1" }));
    await w.find(".app-btn:last-child").trigger("click");
    await settle(w);
    expect(runRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ picture_id: 42, seed_mode: "random" }),
      expect.anything(),
    );
    expect(w.emitted("run")[0][0]).toMatchObject({
      pictureId: 42,
      prompts: [{ prompt_id: "p2" }],
    });
    expect(w.emitted("close")).toBeTruthy();
  });

  it("runs the chosen template with the prompt in template mode", async () => {
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    const w = await settle(mountDialog());
    await w.find("textarea").setValue("make it snowing");
    await w.find(".app-btn:last-child").trigger("click");
    await settle(w);
    expect(runImageToImage).toHaveBeenCalledWith(
      expect.objectContaining({
        picture_ids: [42],
        workflow_name: "Edit.json",
        caption: "make it snowing",
      }),
      expect.anything(),
    );
  });

  it("only lists image-to-image templates", async () => {
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    const w = await settle(mountDialog());
    const options = w.findAll("option").map((o) => o.text());
    expect(options).toEqual(["Edit"]);
  });

  it("keeps the dialog and its inputs on a submit failure", async () => {
    runRecipe.mockRejectedValue({
      response: { data: { detail: "Your ComfyUI is missing: Foo" } },
    });
    const w = await settle(mountDialog());
    await w.find(".app-btn:last-child").trigger("click");
    await settle(w);
    expect(w.emitted("close")).toBeFalsy();
    const alert = w.find('[role="alert"]');
    expect(alert.text()).toContain("Your ComfyUI is missing: Foo");
  });
});
