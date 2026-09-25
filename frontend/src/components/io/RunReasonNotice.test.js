// The one notice both Run popups draw, in its two tones.
//
// It carries refusals and, since #1463, one thing that is NOT a refusal: a
// LoRA this ComfyUI does not have, whose loader the run leaves out. Drawing
// that in the error hue under "can't run" would tell the owner the opposite of
// what is about to happen, so what these pin is the wording and the MODIFIER
// the hue hangs off. The hue itself is a token in a scoped stylesheet, which
// jsdom does not apply, so `rrn--notice` is as far as a unit test reaches;
// which colour that class resolves to is `docs/design/notice-surface.md`.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", async (importOriginal) => ({
  ...(await importOriginal()),
  VIcon: { name: "VIcon", template: "<i><slot /></i>" },
}));

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

describe("a recipe LoRA with no loader, which is not a refusal (#1478)", () => {
  const reason = {
    code: "loras_unplaced",
    workflowKey: "k".repeat(64),
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
    expect(wrapper.emitted("edit-loras")?.[0]).toEqual(["k".repeat(64)]);
  });
});

describe("a LoRA the owner skipped, and one that cannot be skipped", () => {
  it("reads a requested bypass back as the owner's skip, not as a missing file", () => {
    const wrapper = mountNotice({
      code: "loras_skipped",
      models: [{ file: "mira_v2.safetensors", folder: "loras", requested: true }],
    });
    const said = wrapper.text().replace(/\s+/g, " ");
    expect(said).toContain("Skipped for this run: mira_v2.safetensors.");
    expect(said).not.toContain("not on this ComfyUI");
    expect(said).not.toMatch(/remov|delet/i);
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
      "stacked-a.safetensors cannot be skipped for this run. It sits in a stacker whose other LoRAs are present.",
    );
    expect(rail(wrapper)).not.toContain("rrn--notice");
  });
});
