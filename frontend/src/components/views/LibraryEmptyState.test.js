// The first screen of every install, in isolation.
//
// The shape of the answer, not the wording: three routes, folder first, and the
// accent on exactly one. That it renders in the grid at all, and that its
// buttons reach anything, is ImageGridLibraryEmptyState.test.js - this file
// would pass with the whole thing disconnected from the app.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
}));

import LibraryEmptyState from "./LibraryEmptyState.vue";
import { IMPORT_FILE_ACCEPT } from "../../utils/media";

function mountState() {
  return mount(LibraryEmptyState, {
    global: {
      stubs: {
        // No manual `$emit("click")`: the real AppButton declares no emits, so
        // `@click` on it is plain attribute fallthrough onto its own <button>
        // and fires once. A stub that emits as well fires twice, which would
        // have hidden a double-invocation bug rather than caught one.
        AppButton: {
          props: ["variant"],
          template: '<button :data-variant="variant"><slot /></button>',
        },
      },
    },
  });
}

const buttonLabels = (wrapper) =>
  wrapper.findAll(".library-empty__option button").map((b) => b.text().trim());

/** The `variant` each option's button was given, in order. */
const optionVariants = (wrapper) =>
  wrapper
    .findAll(".library-empty__option button")
    .map((b) => b.attributes("data-variant"));

describe("what it offers", () => {
  it("offers three routes, folder first", async () => {
    const wrapper = mountState();

    expect(
      wrapper.findAll(".library-empty__heading").map((h) => h.text()),
    ).toEqual([
      "Use a folder you already have",
      "Drop pictures in",
      "Connect ComfyUI",
    ]);
    expect(buttonLabels(wrapper)).toEqual([
      "Choose a folder…",
      "Add files…",
      "Connect…",
    ]);
  });

  it("gives the accent to the folder route and to nothing else", async () => {
    // "None of them is more official than the others" is the copy, and the
    // ordering plus a single accent is the whole of how that is said. Two
    // primaries would make it a choice between two official answers.
    const variants = optionVariants(mountState());

    expect(variants[0]).toBe("primary");
    expect(variants.slice(1)).toEqual(["secondary", "secondary"]);
  });

  it("never says 'database'", async () => {
    // The word this screen was rewritten to remove. The owner has pictures;
    // the thing holding them is an implementation detail they were never
    // introduced to.
    expect(mountState().text().toLowerCase()).not.toContain("database");
  });

  it("promises the folder route moves nothing, where the decision is made", async () => {
    // The release's headline claim. It belongs on the button that acts on it,
    // not in a help page nobody opens first.
    const folderOption = mountState().findAll(".library-empty__option")[0];

    expect(folderOption.text()).toContain("Nothing is moved");
  });
});

describe("what the buttons do", () => {
  it("emits the folder and ComfyUI routes for the app to place", async () => {
    const wrapper = mountState();
    const buttons = wrapper.findAll(".library-empty__option button");

    await buttons[0].trigger("click");
    await buttons[2].trigger("click");

    expect(wrapper.emitted("choose-folder")).toHaveLength(1);
    expect(wrapper.emitted("connect-comfyui")).toHaveLength(1);
  });

  it("hands the files up unfiltered, and nothing for an empty list", async () => {
    const wrapper = mountState();
    const input = wrapper.find(".library-empty__file-input");
    const files = [new File(["x"], "one.jpg"), new File(["y"], "two.png")];

    // Cancelling the OS picker fires `change` with nothing selected. Starting
    // an import of zero files would put the grid into an import that finishes
    // immediately and explains nothing.
    //
    // What comes out is UNFILTERED on purpose: the grid drops what PixlStash
    // cannot read, against the same predicate its two drop paths use.
    // ImageGridLibraryEmptyState.test.js covers that half.
    Object.defineProperty(input.element, "files", {
      value: [],
      configurable: true,
    });
    await input.trigger("change");
    expect(wrapper.emitted("add-files")).toBeFalsy();

    Object.defineProperty(input.element, "files", {
      value: files,
      configurable: true,
    });
    await input.trigger("change");

    expect(wrapper.emitted("add-files")[0][0]).toEqual(files);
  });

  it("offers the picker everything the importer takes, not just media", async () => {
    // This shipped as `image/*,video/*`, which is narrower than the app: a zip
    // and a caption file are both supported imports, and the picker greyed them
    // out. `accept` is advisory rather than a filter, so the cost of getting it
    // wrong is not a rejected file - it is a route nobody finds.
    const input = mountState().find(".library-empty__file-input");

    expect(input.attributes("accept")).toBe(IMPORT_FILE_ACCEPT);
  });

  it("clears the input so the same files can be chosen twice", async () => {
    // `change` does not fire for an unchanged value, so without the reset a
    // person who picked the wrong folder, cancelled the import and picked the
    // same files again would get nothing at all.
    const wrapper = mountState();
    const input = wrapper.find(".library-empty__file-input");
    Object.defineProperty(input.element, "files", {
      value: [new File(["x"], "one.jpg")],
      configurable: true,
    });

    await input.trigger("change");

    expect(input.element.value).toBe("");
  });
});
