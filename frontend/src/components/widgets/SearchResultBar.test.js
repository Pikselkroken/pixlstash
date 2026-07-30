// The bar under the grid that reports a search and, for a person-scoped face
// search, offers the threshold and the bulk assignment ("Suggest more pictures
// of <person>", #636).
//
// The tests pin the things a user acts on: that the assign button states how
// many pictures it would write and to whom, that an explicit grid selection
// wins over the threshold, and that the threshold control is a real, labelled
// range input rather than a decoration.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import SearchResultBar from "./SearchResultBar.vue";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-progress-circular": true,
      "v-tooltip": { template: "<div><slot name='activator' :props='{}'/></div>" },
      "v-btn": {
        props: ["disabled", "loading"],
        // `emits` declared so the stub does not ALSO let the native click fall
        // through to the parent's handler, which would double every action.
        emits: ["click"],
        template:
          "<button :disabled='disabled' @click=\"$emit('click')\"><slot/></button>",
      },
    },
  },
};

function mountBar(props = {}) {
  return mount(SearchResultBar, { ...globalOpts, props });
}

/** Props for an armed character face search. */
function characterSearchProps(overrides = {}) {
  return {
    count: 41,
    statusText: "41 possible pictures of Alice",
    threshold: 0.7,
    assignTarget: "Alice",
    assignCount: 41,
    ...overrides,
  };
}

describe("SearchResultBar — plain search", () => {
  it("hides the threshold and the assign action", () => {
    // A text or reverse-image search has neither a person to assign to nor a
    // likeness to cut on; rendering either would be an inert control.
    const wrapper = mountBar({ count: 12 });
    expect(wrapper.find(".search-result-threshold").exists()).toBe(false);
    expect(wrapper.find(".search-result-assign").exists()).toBe(false);
  });
});

describe("SearchResultBar — character face search", () => {
  it("states the blast radius on the assign button", () => {
    // "Assign all" hides how much is about to be written. The count is what
    // makes the threshold slider legible and the click safe to make.
    const wrapper = mountBar(characterSearchProps());
    expect(wrapper.find(".search-result-assign").text()).toContain(
      "Assign 41 to Alice",
    );
  });

  it("follows an explicit grid selection instead of the threshold", () => {
    // Writing 41 pictures when the user deliberately selected 12 is the error
    // this mode exists to prevent, so the label has to change with it.
    const wrapper = mountBar(
      characterSearchProps({ assignCount: 12, assignFromSelection: true }),
    );
    expect(wrapper.find(".search-result-assign").text()).toContain(
      "Assign 12 selected to Alice",
    );
  });

  it("disables the assign action when nothing is above the cut", () => {
    // A button that promises "Assign 0" is a dead affordance.
    const wrapper = mountBar(characterSearchProps({ assignCount: 0 }));
    expect(
      wrapper.find(".search-result-assign").attributes("disabled"),
    ).toBeDefined();
  });

  it("disables the assign action while a write is in flight", () => {
    // Double-submitting a bulk assignment would raise two operation-log
    // entries, so Undo would only reverse half of it.
    const wrapper = mountBar(characterSearchProps({ assignBusy: true }));
    expect(
      wrapper.find(".search-result-assign").attributes("disabled"),
    ).toBeDefined();
  });

  it("renders the threshold as a labelled range input", () => {
    // Keyboard operability (WCAG 2.1.1) and a name for the control both come
    // free from a native range with a real label; a div with a drag handler
    // gives neither.
    const wrapper = mountBar(characterSearchProps());
    const input = wrapper.find(".search-result-threshold-input");
    expect(input.attributes("type")).toBe("range");
    const label = wrapper.find(".search-result-threshold-label");
    expect(label.attributes("for")).toBe(input.attributes("id"));
    expect(label.text()).toBe("Match at least");
  });

  it("shows the threshold as a percentage", () => {
    // 0.7 is the stored value; 70% is the one a person reasons about.
    const wrapper = mountBar(characterSearchProps());
    expect(wrapper.find(".search-result-threshold-value").text()).toBe("70%");
  });

  it("emits the new threshold while dragging, not only on release", () => {
    // The count has to track the drag. Listening on `change` instead of `input`
    // would leave the number stale until the pointer is let go.
    const wrapper = mountBar(characterSearchProps());
    const input = wrapper.find(".search-result-threshold-input");
    input.element.value = "0.82";
    input.trigger("input");
    expect(wrapper.emitted("update:threshold")[0]).toEqual([0.82]);
  });

  it("bounds the slider by the fetch floor", () => {
    // Below the floor there are no fetched results to reveal, so dragging there
    // would silently show fewer pictures than the number promised.
    const wrapper = mountBar(
      characterSearchProps({ thresholdMin: 0.5, thresholdMax: 0.95 }),
    );
    const input = wrapper.find(".search-result-threshold-input");
    expect(input.attributes("min")).toBe("0.5");
    expect(input.attributes("max")).toBe("0.95");
  });

  it("announces the count as it changes", () => {
    // A sighted user watches the number move with the slider; everyone else
    // needs it in a live region (WCAG 4.1.3).
    const wrapper = mountBar(characterSearchProps());
    expect(wrapper.find('[aria-live="polite"]').text()).toContain(
      "41 possible pictures of Alice",
    );
  });

  it("emits assign when the action is used", () => {
    const wrapper = mountBar(characterSearchProps());
    wrapper.find(".search-result-assign").trigger("click");
    expect(wrapper.emitted("assign")).toHaveLength(1);
  });

  it("hides the threshold while the search is still running", () => {
    // Cutting a result set that has not arrived yet reads as a broken control.
    const wrapper = mountBar(characterSearchProps({ imagesLoading: true }));
    expect(wrapper.find(".search-result-threshold").exists()).toBe(false);
  });
});
