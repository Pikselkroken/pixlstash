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
const getPictureMetadata = vi.fn();

vi.mock("../../api/comfyui", () => ({
  listWorkflows: (...a) => listWorkflows(...a),
  getPictureRecipe: (...a) => getPictureRecipe(...a),
  runImageToImage: (...a) => runImageToImage(...a),
  runRecipe: (...a) => runRecipe(...a),
}));

vi.mock("../../api/pictures", () => ({
  getPictureMetadata: (...a) => getPictureMetadata(...a),
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
  node_classes: [
    "CheckpointLoaderSimple",
    "CLIPTextEncode",
    "KSampler",
    "SaveImage",
  ],
  source_is_imported: false,
  source_label: null,
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

/** The pre-flight could not run: ComfyUI was unreachable. */
const UNCHECKED_RECIPE = {
  ...CLEAN_RECIPE,
  preflight: { ok: true, checked: false, error: "unreachable" },
};

const IMPORTED_RECIPE = {
  ...CLEAN_RECIPE,
  source_is_imported: true,
  source_label: "Watched folder",
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
  getPictureMetadata.mockResolvedValue({});
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

// ── R3 (CWE-829) ────────────────────────────────────────────────────────────
// The replayed graph is file metadata: whoever made the image authored it, and
// it runs on the owner's ComfyUI. Three controls make that a decision rather
// than an accident, and each is asserted in BOTH directions, because a gate
// that also fires on the clean path trains the user to click through it.

describe("RemixDialog node-class disclosure", () => {
  it("names the node classes that will run, not just how many", async () => {
    const w = await settle(mountDialog());
    const dl = w.find(".remix-recipe");
    expect(dl.text()).toContain("Node types");
    expect(dl.find(".remix-recipe-nodes").text()).toContain("KSampler");
    expect(dl.find(".remix-recipe-nodes").text()).toContain("CheckpointLoaderSimple");
  });

  it("truncates a long list and expands it in place", async () => {
    const many = Array.from({ length: 15 }, (_, i) => `NodeClass${i}`);
    getPictureRecipe.mockResolvedValue({ ...CLEAN_RECIPE, node_classes: many });
    const w = await settle(mountDialog());
    const nodes = w.find(".remix-recipe-nodes");
    expect(nodes.text()).toContain("+3 more");
    expect(nodes.text()).not.toContain("NodeClass14");
    await nodes.find("button").trigger("click");
    await nextTick();
    expect(w.find(".remix-recipe-nodes").text()).toContain("NodeClass14");
    expect(w.find(".remix-recipe-nodes").text()).not.toContain("more");
  });

  it("keeps the disclosure shut for a checked, locally generated recipe", async () => {
    // Progressive disclosure is right when the default is known safe. The
    // routine re-roll stays a two-click flow.
    const w = await settle(mountDialog());
    expect(w.find(".remix-disclosure").attributes("open")).toBeUndefined();
  });

  it("opens the disclosure unprompted when the source is imported", async () => {
    getPictureRecipe.mockResolvedValue(IMPORTED_RECIPE);
    const w = await settle(mountDialog());
    expect(w.find(".remix-disclosure").attributes("open")).toBeDefined();
  });
});

describe("RemixDialog imported-source warning", () => {
  it("says the workflow came from outside, and by which route", async () => {
    getPictureRecipe.mockResolvedValue(IMPORTED_RECIPE);
    const w = await settle(mountDialog());
    const alert = w.find("#remix-alert-imported");
    expect(alert.exists()).toBe(true);
    expect(alert.text()).toContain("imported, not generated here");
    expect(w.find(".remix-recipe").text()).toContain("Watched folder");
  });

  it("informs without gating: no checkbox, Generate still enabled", async () => {
    // Gating a state most of a library is in is exactly what turns an
    // acknowledgement into a reflex, and it would drag the rare unchecked
    // gate down with it.
    getPictureRecipe.mockResolvedValue(IMPORTED_RECIPE);
    const w = await settle(mountDialog());
    expect(w.find(".remix-ack").exists()).toBe(false);
    expect(w.find(".app-btn:last-child").attributes("disabled")).toBeUndefined();
  });

  it("says nothing when the picture was generated here", async () => {
    const w = await settle(mountDialog());
    expect(w.find("#remix-alert-imported").exists()).toBe(false);
  });
});

describe("RemixDialog unchecked pre-flight", () => {
  it("does not preselect recipe mode when the graph was never inspected", async () => {
    getPictureRecipe.mockResolvedValue(UNCHECKED_RECIPE);
    const w = await settle(mountDialog());
    expect(rowFor(w, "Pick a template").attributes("aria-checked")).toBe("true");
    expect(rowFor(w, "Same workflow, new seed").attributes("aria-checked")).toBe(
      "false",
    );
  });

  it("ignores a sticky recipe preference rather than landing in the override", async () => {
    sessionStorage.setItem("comfyui_remix_mode", "recipe");
    getPictureRecipe.mockResolvedValue(UNCHECKED_RECIPE);
    const w = await settle(mountDialog());
    expect(rowFor(w, "Pick a template").attributes("aria-checked")).toBe("true");
  });

  it("keeps the row selectable: aria-disabled would be a lie and would hide the override", async () => {
    getPictureRecipe.mockResolvedValue(UNCHECKED_RECIPE);
    const w = await settle(mountDialog());
    const row = rowFor(w, "Same workflow, new seed");
    expect(row.attributes("aria-disabled")).toBe("false");
    expect(row.classes()).toContain("remix-mode--caution");
    expect(row.classes()).not.toContain("remix-mode--off");
    await row.trigger("click");
    await nextTick();
    expect(rowFor(w, "Same workflow, new seed").attributes("aria-checked")).toBe(
      "true",
    );
  });

  it("refuses to run until the override is ticked", async () => {
    getPictureRecipe.mockResolvedValue(UNCHECKED_RECIPE);
    const w = await settle(mountDialog());
    await rowFor(w, "Same workflow, new seed").trigger("click");
    await nextTick();
    expect(w.find("#remix-alert-unchecked").exists()).toBe(true);
    expect(w.find(".app-btn:last-child").attributes("disabled")).toBeDefined();

    await w.find(".remix-ack-box").setValue(true);
    await nextTick();
    expect(w.find(".app-btn:last-child").attributes("disabled")).toBeUndefined();
  });

  it("sends allow_unchecked only for the run the user acknowledged", async () => {
    getPictureRecipe.mockResolvedValue(UNCHECKED_RECIPE);
    const w = await settle(mountDialog());
    await rowFor(w, "Same workflow, new seed").trigger("click");
    await nextTick();
    await w.find(".remix-ack-box").setValue(true);
    await nextTick();
    await w.find(".app-btn:last-child").trigger("click");
    await settle(w);
    expect(runRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ picture_id: 42, allow_unchecked: true }),
      expect.anything(),
    );
  });

  it("never sends allow_unchecked on a clean pre-flight", async () => {
    const w = await settle(mountDialog());
    await w.find(".app-btn:last-child").trigger("click");
    await settle(w);
    expect(runRecipe.mock.calls[0][0].allow_unchecked).toBeUndefined();
  });

  it("forgets the acknowledgement when the dialog is reopened", async () => {
    getPictureRecipe.mockResolvedValue(UNCHECKED_RECIPE);
    const w = await settle(mountDialog());
    await rowFor(w, "Same workflow, new seed").trigger("click");
    await nextTick();
    await w.find(".remix-ack-box").setValue(true);
    await nextTick();

    await w.setProps({ open: false });
    await w.setProps({ open: true });
    await settle(w);
    await rowFor(w, "Same workflow, new seed").trigger("click");
    await nextTick();
    expect(w.find(".remix-ack-box").element.checked).toBe(false);
    expect(w.find(".app-btn:last-child").attributes("disabled")).toBeDefined();
  });

  it("offers Check again first, and clears the gate when ComfyUI answers", async () => {
    getPictureRecipe.mockResolvedValue(UNCHECKED_RECIPE);
    const w = await settle(mountDialog());
    await rowFor(w, "Same workflow, new seed").trigger("click");
    await nextTick();

    getPictureRecipe.mockResolvedValue(CLEAN_RECIPE);
    await w.find("#remix-alert-unchecked button").trigger("click");
    await settle(w);
    expect(w.find("#remix-alert-unchecked").exists()).toBe(false);
    expect(w.find(".remix-ack").exists()).toBe(false);
    expect(w.find(".app-btn:last-child").attributes("disabled")).toBeUndefined();
  });

  it("drops a tick that a re-check made stale", async () => {
    // An acknowledgement approves the graph the user was shown. Surviving a
    // re-check would make it approve a state they never saw.
    getPictureRecipe.mockResolvedValue(UNCHECKED_RECIPE);
    const w = await settle(mountDialog());
    await rowFor(w, "Same workflow, new seed").trigger("click");
    await nextTick();
    await w.find(".remix-ack-box").setValue(true);
    await nextTick();

    await w.find("#remix-alert-unchecked button").trigger("click");
    await settle(w);
    expect(w.find(".remix-ack-box").element.checked).toBe(false);
    expect(w.find(".app-btn:last-child").attributes("disabled")).toBeDefined();
  });

  it("gives a failed pre-flight no override at all", async () => {
    // Over-blocking and under-blocking are both regressions: a pre-flight that
    // RAN and found a missing node pack is not a consent question.
    getPictureRecipe.mockResolvedValue({
      ...CLEAN_RECIPE,
      preflight: {
        ok: false,
        checked: true,
        missing_node_classes: ["UltimateSDUpscale"],
        missing_models: [],
        missing_input_images: [],
        unchecked_fields: 0,
      },
    });
    const w = await settle(mountDialog());
    const row = rowFor(w, "Same workflow, new seed");
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.classes()).toContain("remix-mode--off");
    expect(w.find(".remix-ack").exists()).toBe(false);
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

  it("fetches the description when the grid row does not carry one", async () => {
    // The grid LISTING has no description field, so the prop is routinely
    // undefined for pictures that plainly have a description.
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    getPictureMetadata.mockResolvedValue({ description: "a cat on a mat" });
    const w = await settle(
      mountDialog({ image: { id: 42, file_name: "cat.png" } }),
    );
    expect(getPictureMetadata).toHaveBeenCalledWith(42, expect.anything());
    expect(w.find("textarea").element.value).toBe("a cat on a mat");
    expect(w.find(".remix-provenance").text()).toContain("from image description");
  });

  it("does not fetch when the prop already provides a description", async () => {
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    await settle(mountDialog());
    expect(getPictureMetadata).not.toHaveBeenCalled();
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

describe("RemixDialog seeds", () => {
  function segButtons(w) {
    return w.findAll(".remix-seg-btn");
  }

  /** Button labels with the stubbed v-icon's "mdi-…" text stripped. */
  function segLabels(w) {
    return segButtons(w).map((b) => b.text().replace(/mdi-\S+\s*/, ""));
  }

  function segButton(w, label) {
    return segButtons(w).find((b) => b.text().includes(label));
  }

  it("offers Incremented only where an original seed exists to increment", async () => {
    const w = await settle(mountDialog());
    expect(segLabels(w)).toEqual(["Random", "Incremented", "Fixed"]);
  });

  it("does not offer Incremented in template mode", async () => {
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    const w = await settle(mountDialog());
    expect(segLabels(w)).toEqual(["Random", "Fixed"]);
  });

  it("defaults Fixed to the original seed and flags it until changed", async () => {
    const w = await settle(mountDialog());
    await segButton(w, "Fixed").trigger("click");
    await nextTick();
    const input = w.find('input[aria-label="Seed value"]');
    expect(Number(input.element.value)).toBe(1);
    expect(w.find(".remix-seed-note--warn").text()).toContain("same as original");

    await input.setValue(2);
    await nextTick();
    expect(w.find(".remix-seed-note--warn").exists()).toBe(false);
  });

  it("submits Incremented as a fixed seed at original + delta", async () => {
    const w = await settle(mountDialog());
    await segButton(w, "Incremented").trigger("click");
    await nextTick();
    await w.find('input[aria-label="Delta from the original seed"]').setValue(5);
    await nextTick();
    expect(w.find(".remix-seed-note").text()).toContain("= 6");

    await w.find(".app-btn:last-child").trigger("click");
    await settle(w);
    expect(runRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ seed_mode: "fixed", seed: 6 }),
      expect.anything(),
    );
  });

  it("accepts a negative delta", async () => {
    getPictureRecipe.mockResolvedValue({
      ...CLEAN_RECIPE,
      seed: 100,
    });
    const w = await settle(mountDialog());
    await segButton(w, "Incremented").trigger("click");
    await nextTick();
    await w.find('input[aria-label="Delta from the original seed"]').setValue(-3);
    await nextTick();
    await w.find(".app-btn:last-child").trigger("click");
    await settle(w);
    expect(runRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ seed_mode: "fixed", seed: 97 }),
      expect.anything(),
    );
  });

  it("drops a sticky Incremented preference where it cannot be honoured", async () => {
    sessionStorage.setItem("comfyui_remix_seed_mode", "incremented");
    getPictureRecipe.mockResolvedValue({
      available: false,
      reason: "no_prompt_chunk",
    });
    const w = await settle(mountDialog());
    expect(segButton(w, "Random").attributes("aria-checked")).toBe("true");
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
