// The one notice both Run popups draw, in its two tones.
//
// It carries refusals and, since #1463, one thing that is NOT a refusal: a
// LoRA this ComfyUI does not have, whose loader the run leaves out. Drawing
// that in the error hue under "can't run" would tell the owner the opposite of
// what is about to happen, so the tone is what these pin.

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

  it("keeps the error rail", () => {
    expect(rail(mountNotice(reason))).not.toContain("rrn--notice");
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

  it("takes the warning rail rather than the error one", () => {
    expect(rail(mountNotice(reason))).toContain("rrn--notice");
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
