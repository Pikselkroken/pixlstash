// Export recipe (v1.12 F6): the consent step, and the one thing that must not
// silently go missing.
//
// The dialog's whole job is to print what the file gives away BEFORE it is
// written. The list is the server's own `shares` and is never composed here:
// a dialog that summarised it would go on saying "your prompt" the day the
// export started carrying something else. So the assertion is that every line
// the server sent reaches the screen, and that nothing is written until the
// owner presses the button.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const exportSavedRecipe = vi.fn();
const exportWorkflow = vi.fn();
vi.mock("../../api/recipes", () => ({
  exportSavedRecipe: (...args) => exportSavedRecipe(...args),
}));
vi.mock("../../api/workflows", () => ({
  exportWorkflow: (...args) => exportWorkflow(...args),
}));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import { useNoticeStore } from "../../stores/useNoticeStore";
import ExportRecipeDialog from "./ExportRecipeDialog.vue";

const KEY = "a".repeat(64);

const SHARES = [
  "the name you gave it, 'Rainy tram platform'",
  "the prompt you wrote",
  "2 LoRA file name(s): film-grain-35mm.safetensors, mira_v2.safetensors",
  "1 parameter setting(s)",
  "a model name this machine no longer holds, which you may have asked "
    + "PixlStash to forget: mira_v2.safetensors",
];

function render() {
  return mount(ExportRecipeDialog, {
    props: { open: true, recipeId: 4 },
    global: { stubs: { teleport: true } },
  });
}

/** What the anchor the dialog clicks was told to download. */
let downloads;

describe("ExportRecipeDialog", () => {
  beforeEach(() => {
    // The workflow export reports what the scrub took through the notices.
    setActivePinia(createPinia());
    vi.clearAllMocks();
    downloads = [];
    exportSavedRecipe.mockResolvedValue({
      filename: "rainy-tram-platform.json",
      recipe: { name: "Rainy tram platform", workflow_key: KEY },
      shares: SHARES,
    });
    exportWorkflow.mockResolvedValue({
      filename: "cinematic-portrait.json",
      workflow: { 4: { class_type: "KSampler" } },
      removed: ["2 prompts", "1 LoRA slot"],
    });
    // jsdom has no download, so the anchor is watched rather than driven.
    global.URL.createObjectURL = vi.fn(() => "blob:x");
    global.URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function record() {
        downloads.push(this.download);
      },
    );
  });

  it("prints every line the server said the file shares", async () => {
    const wrapper = render();
    await flushPromises();
    expect(exportSavedRecipe).toHaveBeenCalledWith(4);
    const printed = wrapper.findAll(".exr-share").map((row) => row.text());
    expect(printed).toEqual(SHARES);
    expect(wrapper.text()).toContain(
      "This file lets anyone make pictures like yours",
    );
  });

  it("offers the workflow as the share that keeps the look back", async () => {
    const wrapper = render();
    await flushPromises();
    expect(wrapper.text()).toContain(
      "leaves the prompt blank and the recipe LoRA slots empty",
    );
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Export workflow"))
      .trigger("click");
    await flushPromises();

    expect(exportWorkflow).toHaveBeenCalledWith(KEY);
    expect(downloads).toEqual(["cinematic-portrait.json"]);
    expect(wrapper.emitted("close")).toBeTruthy();
    // The safe alternative must not be the one that goes out silently: this
    // dialog exists to say what a file does and does not carry.
    const said = useNoticeStore().notices.map((n) => n.text).join(" ");
    expect(said).toContain("2 prompts");
    expect(said).toContain("1 LoRA slot");
  });

  it("writes nothing until the owner presses Export recipe", async () => {
    const wrapper = render();
    await flushPromises();
    expect(downloads).toEqual([]);

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Export recipe"))
      .trigger("click");
    await flushPromises();
    expect(downloads).toEqual(["rainy-tram-platform.json"]);
  });

  it("says so rather than listing nothing when the read fails", async () => {
    exportSavedRecipe.mockRejectedValue(new Error("gone"));
    const wrapper = render();
    await flushPromises();
    expect(wrapper.find(".exr-share").exists()).toBe(false);
    expect(wrapper.find("[role=alert]").exists()).toBe(true);
  });

  it("never lists one recipe over another recipe's file", async () => {
    // The dialog is reused for whichever row the ⋯ menu names, so a slow read
    // for the first can land after the second has been asked for. Listing it
    // would make the button write a file the list does not describe.
    let answerFirst;
    exportSavedRecipe.mockImplementationOnce(
      () => new Promise((resolve) => (answerFirst = resolve)),
    );
    exportSavedRecipe.mockImplementationOnce(async () => ({
      filename: "harbour-fog.json",
      recipe: { name: "Harbour fog", workflow_key: KEY },
      shares: ["the prompt you wrote"],
    }));

    const wrapper = render();
    await wrapper.setProps({ recipeId: 5 });
    await flushPromises();

    answerFirst({
      filename: "rainy-tram-platform.json",
      recipe: { name: "Rainy tram platform", workflow_key: KEY },
      shares: SHARES,
    });
    await flushPromises();

    const printed = wrapper.findAll(".exr-share").map((row) => row.text());
    expect(printed).toEqual(["the prompt you wrote"]);
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Export recipe"))
      .trigger("click");
    await flushPromises();
    expect(downloads).toEqual(["harbour-fog.json"]);
  });
});
