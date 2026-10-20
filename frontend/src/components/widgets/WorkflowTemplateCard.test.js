// One template on the Templates tab (#2000): where it comes from, what it
// would download and whether it could run here, in its four states.

import { afterEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import WorkflowTemplateCard from "./WorkflowTemplateCard.vue";

enableAutoUnmount(afterEach);

const file = (name, present, directory) => ({ name, present, directory });

const BASE = {
  key: "default/flux_krea",
  module: "default",
  name: "flux_krea",
  source: "builtin",
  title: "Flux.1 Krea Dev",
  thumbnail: true,
  files: [
    file("flux1-krea-dev.safetensors", false, "diffusion_models"),
    file("clip_l.safetensors", true, "text_encoders"),
    file("ae.safetensors", true, "vae"),
  ],
};

const mountCard = (template) =>
  mount(WorkflowTemplateCard, {
    props: { template },
    global: { stubs: { Tooltip: true } },
  });

const state = (wrapper) =>
  wrapper.find('[data-testid="template-readiness"]').text();

describe("a template card", () => {
  it("marks each file solid or hollow, and counts what is missing", () => {
    const wrapper = mountCard(BASE);
    const marks = wrapper.findAll('[data-testid="template-mark"]');
    expect(marks.map((m) => m.text())).toEqual(["F", "T", "V"]);
    expect(marks.map((m) => m.classes("wtc-mark--missing"))).toEqual([
      true,
      false,
      false,
    ]);
    expect(state(wrapper)).toBe("1 of 3 missing");
    expect(wrapper.find('[data-testid="template-source"]').text()).toBe("ComfyUI");
    // The picture comes through PixlStash, never from ComfyUI directly.
    expect(wrapper.find("img").attributes("src")).toContain(
      "/comfyui/templates/thumbnail?module=default&name=flux_krea",
    );
  });

  it("is ready to run when the shelf has every file", () => {
    const wrapper = mountCard({
      ...BASE,
      files: BASE.files.map((f) => ({ ...f, present: true })),
    });
    expect(state(wrapper)).toBe("Ready to run");
    expect(wrapper.find("article").attributes("aria-label")).toBe(
      "Flux.1 Krea Dev, ComfyUI template, Ready to run",
    );
  });

  it("names a pack, and says when its nodes did not load", () => {
    const wrapper = mountCard({
      ...BASE,
      key: "WanVideoWrapper/x",
      module: "WanVideoWrapper",
      source: "pack",
      pack_loaded: false,
      files: [],
    });
    expect(wrapper.find('[data-testid="template-source"]').text()).toBe(
      "WanVideoWrapper",
    );
    expect(state(wrapper)).toBe("Pack not loaded");
  });

  it("says a partner template runs on Comfy's API, and draws no picture it has not got", () => {
    const wrapper = mountCard({
      ...BASE,
      source: "partner",
      files: [],
      thumbnail: false,
    });
    expect(state(wrapper)).toBe("Partner API");
    expect(wrapper.text()).toContain("Runs on Comfy's API");
    expect(wrapper.find("img").exists()).toBe(false);
  });

  it("falls back to the quiet cover when the picture will not load", async () => {
    const wrapper = mountCard(BASE);
    await wrapper.find("img").trigger("error");
    expect(wrapper.find("img").exists()).toBe(false);
  });
});
