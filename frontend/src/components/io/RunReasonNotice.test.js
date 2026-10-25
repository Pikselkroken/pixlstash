// The one notice both Run popups draw, in its two tones.
//
// It carries refusals and, since #1463, one thing that is NOT a refusal: a
// LoRA this ComfyUI does not have, whose loader the run leaves out. Drawing
// that in the error hue under "can't run" would tell the owner the opposite of
// what is about to happen, so what these pin is the wording and the MODIFIER
// the hue hangs off. The hue itself is a token in a scoped stylesheet, which
// jsdom does not apply, so `rrn--notice` is as far as a unit test reaches;
// which colour that class resolves to is `docs/design/notice-surface.md`.

import { afterEach, describe, it, expect, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

vi.mock("vuetify/components", async (importOriginal) => ({
  ...(await importOriginal()),
  VIcon: { name: "VIcon", template: "<i><slot /></i>" },
}));

vi.mock("../../api/comfyui", () => ({
  installNodePack: vi.fn(),
  getNodePack: vi.fn(),
}));

import { getNodePack, installNodePack } from "../../api/comfyui";
import RunReasonNotice from "./RunReasonNotice.vue";

function mountNotice(reason, subject = "Cinematic portrait") {
  return mount(RunReasonNotice, {
    props: { reason, subject },
    global: { stubs: { AppButton: true } },
  });
}

/** The notice's own element. A leading comment makes the root a fragment, so
 *  `wrapper.classes()` is not the one carrying the rail. */
function rail(wrapper) {
  return wrapper.find(".rrn").classes();
}

describe("a refusal", () => {
  const reason = {
    code: "missing_models",
    models: [{ file: "realvisxl.safetensors", folder: "checkpoints" }],
  };

  it("leads with the card and says it cannot run", () => {
    const wrapper = mountNotice(reason);
    expect(wrapper.text()).toContain("Cinematic portrait can't run.");
    expect(wrapper.text()).toContain("realvisxl.safetensors");
    expect(wrapper.text()).toContain("models/checkpoints");
  });

  it("keeps the error rail and its glyph", () => {
    expect(rail(mountNotice(reason))).not.toContain("rrn--notice");
    expect(mountNotice(reason).text()).toContain("mdi-alert-circle-outline");
  });
});

describe("a bypassed LoRA, which is not a refusal", () => {
  const reason = {
    code: "loras_bypassed",
    models: [{ file: "character.safetensors", folder: "loras" }],
  };

  it("says the run goes ahead, and never that it cannot", () => {
    const wrapper = mountNotice(reason);
    expect(wrapper.text()).toContain("the run goes ahead without it");
    expect(wrapper.text()).toContain("look different");
    expect(wrapper.text()).not.toContain("can't run");
    // The card is still named: a mixed batch draws several of these.
    expect(wrapper.text()).toContain("Cinematic portrait");
  });

  it("names the file and where it would have to go to come back", () => {
    const wrapper = mountNotice(reason);
    expect(wrapper.text()).toContain("character.safetensors");
    expect(wrapper.text()).toContain("models/loras");
  });

  it("takes the notice modifier rather than the bare error surface", () => {
    expect(rail(mountNotice(reason))).toContain("rrn--notice");
  });

  it("takes the warning glyph, which is not in a stylesheet", () => {
    // Unlike the hue, this one IS in the markup, so it is worth an assertion.
    expect(mountNotice(reason).text()).toContain("mdi-alert-outline");
  });

  it("counts them when there is more than one", () => {
    const wrapper = mountNotice({
      code: "loras_bypassed",
      models: [
        { file: "a.safetensors", folder: "loras" },
        { file: "b.safetensors", folder: "loras" },
      ],
    });
    expect(wrapper.text()).toContain("2 LoRAs this workflow uses are");
    expect(wrapper.text()).toContain("without them");
  });

  it("offers a download beside the file only where the server can fetch it", () => {
    const wrapper = mount(RunReasonNotice, {
      props: {
        reason: {
          code: "loras_bypassed",
          models: [
            { file: "listed.safetensors", folder: "loras", downloadable: true },
            { file: "own.safetensors", folder: "loras" },
          ],
        },
      },
      global: { stubs: { AppButton: true, ModelDownloadButton: true } },
    });
    const offered = wrapper
      .findAll(".rrn-files li")
      .map((row) => row.find("model-download-button-stub").exists());
    expect(offered).toEqual([true, false]);
  });

  it("offers nothing to press: there is nothing left to fix", () => {
    // The run is happening. A button here would have to either undo the
    // bypass - which puts back a graph ComfyUI refuses - or do nothing.
    expect(mountNotice(reason).find(".rrn-acts").exists()).toBe(false);
  });
});

describe("a replaced seed node, which is not a refusal either", () => {
  const reason = {
    code: "nodes_replaced",
    nodes: [
      { node_id: "9", class_type: "Seed (rgthree)", replacement: "seed" },
      { node_id: "12", class_type: "Seed (rgthree)", replacement: "seed" },
    ],
  };

  it("says PixlStash seeds the run itself, and never that it cannot run", () => {
    const wrapper = mountNotice(reason);
    expect(wrapper.text()).toContain("writes the seed straight into the sampler");
    expect(wrapper.text()).toContain("The run goes ahead without it");
    expect(wrapper.text()).not.toContain("can't run");
  });

  it("names the node's class once, in the sentence rather than as a file", () => {
    const wrapper = mountNotice(reason);
    expect(wrapper.find(".rrn-files").exists()).toBe(false);
    expect(wrapper.text().split("Seed (rgthree)")).toHaveLength(2);
    expect(wrapper.text()).not.toContain("models/");
  });

  it("takes the notice modifier and offers nothing to press", () => {
    const wrapper = mountNotice(reason);
    expect(rail(wrapper)).toContain("rrn--notice");
    expect(wrapper.find(".rrn-acts").exists()).toBe(false);
  });
});

describe("a replaced text node", () => {
  it("says the text is written into what it fed, and nothing about seeds", () => {
    const wrapper = mountNotice({
      code: "nodes_replaced",
      nodes: [{ node_id: "103", class_type: "Text Multiline", replacement: "text" }],
    });
    expect(wrapper.text()).toContain("the text node Text Multiline");
    expect(wrapper.text()).toContain("writes the text straight into what it fed");
    expect(wrapper.text()).not.toContain("seed");
  });

  it("words a seed and a text node each in its own sentence", () => {
    const wrapper = mountNotice({
      code: "nodes_replaced",
      nodes: [
        { node_id: "9", class_type: "Seed (rgthree)", replacement: "seed" },
        { node_id: "103", class_type: "Text Multiline", replacement: "text" },
      ],
    });
    expect(wrapper.text()).toContain("the seed node Seed (rgthree)");
    expect(wrapper.text()).toContain("the text node Text Multiline");
  });
});

describe("a recipe LoRA with no loader, which is not a refusal (#1478)", () => {
  const reason = {
    code: "loras_unplaced",
    workflowId: `auto:${"k".repeat(64)}`,
    loras: [
      {
        filename: "loras/skin-detail-xl.safetensors",
        sha256: "c".repeat(64),
        reason: "this workflow has no third loader",
      },
    ],
  };

  function mountWithButtons(entry) {
    return mount(RunReasonNotice, {
      props: { reason: entry, subject: "SDXL fast" },
      global: {
        stubs: {
          AppButton: { template: "<button @click=\"$emit('click')\"><slot /></button>" },
        },
      },
    });
  }

  it("says it is not applied, names it, and does not say the run cannot", () => {
    const wrapper = mountWithButtons(reason);
    const said = wrapper.text().replace(/\s+/g, " ");
    expect(said).not.toContain("can't run");
    expect(said).toContain(
      "A LoRA this recipe names has no loader to go in on this workflow, so it is not applied.",
    );
    expect(said).toContain("skin-detail-xl.safetensors — this workflow has no third loader");
    expect(rail(wrapper)).toContain("rrn--notice");
  });


  it("says a LoRA the shelf cannot identify is missing, not loaderless", () => {
    const wrapper = mountWithButtons({
      ...reason,
      loras: [
        {
          filename: "loras/Mystery.safetensors",
          sha256: null,
          reason: "Your model shelf cannot identify Mystery.safetensors",
        },
      ],
    });
    const said = wrapper.text().replace(/\s+/g, " ");
    expect(said).toContain(
      "A LoRA this recipe names is missing from your model shelf, so it is not applied.",
    );
    expect(said).not.toContain("no loader");
  });

  it("offers Edit LoRAs… for the card it is about", async () => {
    const wrapper = mountWithButtons(reason);
    const edit = wrapper.findAll("button").find((b) => b.text() === "Edit LoRAs…");
    expect(edit).toBeTruthy();
    await edit.trigger("click");
    expect(wrapper.emitted("edit-loras")?.[0]).toEqual([`auto:${"k".repeat(64)}`]);
  });
});

describe("a LoRA the owner removed, and one that cannot be removed", () => {
  it("reads a requested bypass back as the owner's removal, not as a missing file", () => {
    const wrapper = mountNotice({
      code: "loras_skipped",
      models: [{ file: "mira_v2.safetensors", folder: "loras", requested: true }],
    });
    const said = wrapper.text().replace(/\s+/g, " ");
    expect(said).toContain("Removed from this run: mira_v2.safetensors.");
    expect(said).not.toContain("not in this ComfyUI");
    expect(said).not.toMatch(/skip|delet/i);
    expect(said).not.toContain("can't run");
    expect(rail(wrapper)).toContain("rrn--notice");
  });

  it("refuses a skip nothing can be rewired around, in the server's words", () => {
    const wrapper = mountNotice({
      code: "lora_not_skippable",
      file: "loras/stacked-a.safetensors",
      text: "It sits in a stacker whose other LoRAs are present.",
    });
    const said = wrapper.text().replace(/\s+/g, " ");
    expect(said).toContain("Cinematic portrait can't run.");
    expect(said).toContain(
      "stacked-a.safetensors cannot be removed from this run. It sits in a stacker whose other LoRAs are present.",
    );
    expect(rail(wrapper)).not.toContain("rrn--notice");
  });

  it("names the stage itself when the server gave no sentence", () => {
    const said = mountNotice({ code: "stage_not_skippable", stage: "upscale" })
      .text()
      .replace(/\s+/g, " ");
    expect(said).toContain("The upscale pass cannot be switched off for this run.");
  });

  it("names no stage it does not know, rather than calling it the upscale", () => {
    const said = mountNotice({ code: "stage_not_skippable", stage: "frame_interp" })
      .text()
      .replace(/\s+/g, " ");
    expect(said).toContain(
      "One of this workflow's passes cannot be switched off for this run.",
    );
    expect(said).not.toContain("upscale");
  });

  it("names seed variance when the server gave no sentence", () => {
    const said = mountNotice({ code: "stage_not_skippable", stage: "seed_variance" })
      .text()
      .replace(/\s+/g, " ");
    expect(said).toContain("The seed variance pass cannot be switched off for this run.");
  });

  it("refuses a stage that cannot be switched off, in the server's words", () => {
    const wrapper = mountNotice({
      code: "stage_not_skippable",
      stage: "face_detailer",
      message:
        "The face detailer stage cannot be switched off: node 40 (FaceDetailer) cannot be bypassed.",
    });
    const said = wrapper.text().replace(/\s+/g, " ");
    expect(said).toContain("Cinematic portrait can't run.");
    expect(said).toContain(
      "The face detailer stage cannot be switched off: node 40 (FaceDetailer) cannot be bypassed.",
    );
    expect(said.match(/cannot be switched off/g)).toHaveLength(1);
    expect(rail(wrapper)).not.toContain("rrn--notice");
  });
});

describe("a node pack PixlStash can install (#2054)", () => {
  const reason = {
    code: "missing_nodes",
    nodes: ["InpaintCropImproved", "InpaintStitchImproved"],
    packs: [
      {
        id: "comfyui-inpaint-cropandstitch",
        name: "ComfyUI-Inpaint-CropAndStitch",
        version: "3.0.11",
        nodes: ["InpaintCropImproved", "InpaintStitchImproved"],
      },
    ],
  };

  function mountInstall() {
    return mount(RunReasonNotice, {
      props: { reason, subject: "Inpaint" },
      global: {
        stubs: {
          AppButton: {
            props: ["loading", "disabled"],
            emits: ["click"],
            template:
              "<button :data-loading=\"loading\" :disabled=\"disabled\" @click=\"$emit('click')\"><slot /></button>",
          },
          // The Install button sits in a tip's activator slot, which the
          // unregistered Vuetify tooltip would not draw.
          Tooltip: {
            name: "Tooltip",
            props: ["text", "describe"],
            template: "<slot name='activator' :props='{}' />",
          },
        },
      },
    });
  }

  function button(wrapper, label) {
    return wrapper.findAll("button").find((b) => b.text().startsWith(label));
  }

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("says the whole pack name in a tip, since the label may be cut short (#2094)", () => {
    const tip = mountInstall().findComponent({ name: "Tooltip" });
    expect(tip.props("text")).toBe("Install ComfyUI-Inpaint-CropAndStitch…");
    // The label already names the button with these words.
    expect(tip.props("describe")).toBe(false);
  });

  it("asks before installing, and Cancel installs nothing", async () => {
    const wrapper = mountInstall();
    await button(wrapper, "Install ComfyUI-Inpaint-CropAndStitch…").trigger("click");
    const said = wrapper.text().replace(/\s+/g, " ");
    expect(said).toContain("This installs third-party code (comfyui-inpaint-cropandstitch 3.0.11");
    expect(said).toContain("Install it yourself from ComfyUI Manager.");
    expect(installNodePack).not.toHaveBeenCalled();
    await button(wrapper, "Cancel").trigger("click");
    expect(button(wrapper, "Install and restart")).toBeUndefined();
    expect(installNodePack).not.toHaveBeenCalled();
  });

  it("installs once, waits for the nodes, then asks the parent again once", async () => {
    vi.useFakeTimers();
    installNodePack.mockResolvedValue({ restart: "requested", detail: null });
    getNodePack
      .mockRejectedValueOnce(new Error("ComfyUI restarting"))
      .mockResolvedValueOnce({ loaded: false })
      .mockResolvedValue({ loaded: true });
    const wrapper = mountInstall();
    await button(wrapper, "Install ComfyUI-Inpaint-CropAndStitch…").trigger("click");
    await button(wrapper, "Install and restart").trigger("click");
    await flushPromises();
    expect(installNodePack).toHaveBeenCalledTimes(1);
    expect(installNodePack).toHaveBeenCalledWith("comfyui-inpaint-cropandstitch");
    expect(wrapper.text()).toContain("Restarting ComfyUI…");
    for (let tick = 0; tick < 3; tick += 1) {
      await vi.advanceTimersByTimeAsync(3000);
    }
    expect(getNodePack).toHaveBeenCalledTimes(3);
    expect(wrapper.emitted("retry")).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(30000);
    expect(getNodePack).toHaveBeenCalledTimes(3);
    expect(wrapper.emitted("retry")).toHaveLength(1);
  });

  it("stops asking once it unmounts", async () => {
    vi.useFakeTimers();
    installNodePack.mockResolvedValue({ restart: "requested", detail: null });
    getNodePack.mockResolvedValue({ loaded: false });
    const wrapper = mountInstall();
    await button(wrapper, "Install ComfyUI-Inpaint-CropAndStitch…").trigger("click");
    await button(wrapper, "Install and restart").trigger("click");
    await flushPromises();
    await vi.advanceTimersByTimeAsync(3000);
    expect(getNodePack).toHaveBeenCalledTimes(1);
    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(30000);
    expect(getNodePack).toHaveBeenCalledTimes(1);
  });

  it("says why ComfyUI was not restarted, and offers Retry", async () => {
    installNodePack.mockResolvedValue({
      restart: "manual",
      detail: "ComfyUI is busy, so restart it yourself once its queue is empty.",
    });
    const wrapper = mountInstall();
    await button(wrapper, "Install ComfyUI-Inpaint-CropAndStitch…").trigger("click");
    await button(wrapper, "Install and restart").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("ComfyUI is busy, so restart it yourself");
    expect(getNodePack).not.toHaveBeenCalled();
    await button(wrapper, "Retry").trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);
  });

  it("shows the server's refusal and offers the install again", async () => {
    installNodePack.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: "ComfyUI-Manager is not installed in this ComfyUI." },
      },
    });
    const wrapper = mountInstall();
    await button(wrapper, "Install ComfyUI-Inpaint-CropAndStitch…").trigger("click");
    await button(wrapper, "Install and restart").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("ComfyUI-Manager is not installed in this ComfyUI.");
    expect(button(wrapper, "Install ComfyUI-Inpaint-CropAndStitch…")).toBeTruthy();
  });

  it("says where to install from when this is not the computer PixlStash runs on", async () => {
    installNodePack.mockRejectedValue({
      response: { status: 403, data: { detail: "restricted to loopback" } },
    });
    const wrapper = mountInstall();
    await button(wrapper, "Install ComfyUI-Inpaint-CropAndStitch…").trigger("click");
    await button(wrapper, "Install and restart").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("on the computer it runs on, or from ComfyUI Manager");
    expect(wrapper.text()).not.toContain("loopback");
  });
});
