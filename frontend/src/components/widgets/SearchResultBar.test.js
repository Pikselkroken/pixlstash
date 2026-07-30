// The search half of the grid action pill. It reports a search and, for a
// person-scoped face search, offers the threshold and the bulk assignment
// ("Suggest more pictures of <person>", #636).
//
// The tests pin the things a user acts on: that the assign button states how
// many pictures it would write and to whom, that an explicit grid selection
// wins over the threshold, that the threshold control is a real, labelled range
// input rather than a decoration, and — since the merge into one pill — that
// exactly one live region speaks and the controls do not vanish mid-search.

import { describe, it, expect, vi, afterEach } from "vitest";
import { mount } from "@vue/test-utils";

import SearchResultBar from "./SearchResultBar.vue";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-progress-circular": true,
      // Activator only: the popover form of the threshold is the same control
      // twice over, and rendering both would make every `find` ambiguous.
      "v-menu": {
        template: "<div><slot name='activator' :props='{}'/></div>",
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
    statusCount: 41,
    statusLabel: "possible pictures of Alice",
    threshold: 0.7,
    assignTarget: "Alice",
    assignCount: 41,
    ...overrides,
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("SearchResultBar — plain search", () => {
  it("hides the threshold and the assign action", () => {
    // A text or reverse-image search has neither a person to assign to nor a
    // likeness to cut on; rendering either would be an inert control.
    const wrapper = mountBar({ statusCount: 12, statusLabel: "matches" });
    expect(wrapper.find(".search-result-threshold").exists()).toBe(false);
    expect(wrapper.find(".assign-btn").exists()).toBe(false);
  });

  it("gives the count its own weight, separate from the sentence", () => {
    // Two numerals bracketing the pill is how a user tells the search half from
    // the selection half at a glance. It is the differentiator a second
    // background colour was rejected in favour of.
    const wrapper = mountBar({
      statusCount: 42,
      statusLabel: 'matches for "sunset" in Landscapes',
    });
    expect(wrapper.find(".search-result-count").text()).toBe("42");
    expect(wrapper.find(".search-result-label").text()).toBe(
      'matches for "sunset" in Landscapes',
    );
  });

  it("names the query in the title, at every width", () => {
    // Below 560px the label is hidden by the container query, and nothing else
    // on screen says what was searched once the toolbar popover closes.
    const wrapper = mountBar({
      statusCount: 42,
      statusLabel: 'matches for "sunset" in Landscapes',
    });
    expect(wrapper.find(".search-result-status").attributes("title")).toBe(
      '42 matches for "sunset" in Landscapes',
    );
  });
});

describe("SearchResultBar — character face search", () => {
  it("states the blast radius on the assign button", () => {
    // "Assign all" hides how much is about to be written. The count is what
    // makes the threshold slider legible and the click safe to make.
    const wrapper = mountBar(characterSearchProps());
    expect(wrapper.find(".assign-btn").text()).toContain("Assign 41 to Alice");
  });

  it("follows an explicit grid selection instead of the threshold", () => {
    // Writing 41 pictures when the user deliberately selected 12 is the error
    // this mode exists to prevent, so the label has to change with it.
    const wrapper = mountBar(
      characterSearchProps({ assignCount: 12, assignFromSelection: true }),
    );
    expect(wrapper.find(".assign-btn").text()).toContain(
      "Assign 12 selected to Alice",
    );
  });

  it("says so when the selection is narrower than the result set", () => {
    // The selection silently seizing the assign target used to have only a
    // label change as its signal, and that change now happens inside a pill
    // that is opening at the same moment.
    const wrapper = mountBar(
      characterSearchProps({ assignCount: 12, assignFromSelection: true }),
    );
    expect(wrapper.find(".assign-btn").attributes("aria-label")).toContain(
      "Using your 12 selected, not all 41 matches.",
    );
  });

  it("disables the assign action when nothing is above the cut", () => {
    // A button that promises "Assign 0" is a dead affordance.
    const wrapper = mountBar(characterSearchProps({ assignCount: 0 }));
    expect(wrapper.find(".assign-btn").attributes("disabled")).toBeDefined();
  });

  it("disables the assign action while a write is in flight", () => {
    // Double-submitting a bulk assignment would raise two operation-log
    // entries, so Undo would only reverse half of it.
    const wrapper = mountBar(characterSearchProps({ assignBusy: true }));
    expect(wrapper.find(".assign-btn").attributes("disabled")).toBeDefined();
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
    expect(label.text()).toBe("Match ≥");
  });

  it("announces the cut as a percentage, not as a raw ratio", () => {
    // <output for> is a reverse relationship, not a labelling one, so without
    // this a screen reader reads "Match ≥, slider, 0.7".
    const wrapper = mountBar(characterSearchProps());
    expect(
      wrapper.find(".search-result-threshold-input").attributes("aria-valuetext"),
    ).toBe("70%");
  });

  it("keeps the <output> out of the live region", () => {
    // <output> maps to role="status" by default, so it would announce on every
    // pointer sample of a drag, in parallel with the pill's own region.
    const wrapper = mountBar(characterSearchProps());
    expect(wrapper.find("output").attributes("aria-live")).toBe("off");
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

  it("emits assign when the action is used", () => {
    const wrapper = mountBar(characterSearchProps());
    wrapper.find(".assign-btn").trigger("click");
    expect(wrapper.emitted("assign")).toHaveLength(1);
  });

  it("keeps the threshold mounted while the search is still running", async () => {
    // Hiding it collapsed the pill and snapped it back to full width when the
    // results landed, moving targets under a cursor already travelling toward
    // them. It stays, marked aria-disabled.
    const wrapper = mountBar(characterSearchProps({ imagesLoading: true }));
    expect(wrapper.find(".search-result-threshold").exists()).toBe(true);
    expect(
      wrapper.find(".search-result-threshold-input").attributes("aria-disabled"),
    ).toBe("true");
  });
});

describe("SearchResultBar — the live region", () => {
  it("carries the full sentence and the cut, debounced to one announcement", async () => {
    // A sighted user watches the number move with the slider; everyone else
    // needs it in a live region (WCAG 4.1.3) — but once per drag, not once per
    // pointer sample.
    vi.useFakeTimers();
    const wrapper = mountBar(characterSearchProps());
    const region = wrapper.find('[role="status"]');

    expect(region.text()).toBe("");
    vi.advanceTimersByTime(300);
    await wrapper.vm.$nextTick();
    expect(region.text()).toBe(
      "41 possible pictures of Alice at 70% or better",
    );

    // Three rapid changes, one announcement.
    for (const value of [0.75, 0.8, 0.85]) {
      await wrapper.setProps({ threshold: value });
      vi.advanceTimersByTime(50);
    }
    expect(region.text()).toBe(
      "41 possible pictures of Alice at 70% or better",
    );
    vi.advanceTimersByTime(300);
    await wrapper.vm.$nextTick();
    expect(region.text()).toBe(
      "41 possible pictures of Alice at 85% or better",
    );
  });

  it("is the only live region in the half", () => {
    const wrapper = mountBar(characterSearchProps());
    expect(wrapper.findAll('[aria-live="polite"]')).toHaveLength(1);
  });
});

describe("SearchResultBar — the Esc keycap", () => {
  it("wears the keycap only when Esc actually reaches it", async () => {
    // Both halves claiming Esc means one of them is lying. An
    // aria-keyshortcuts on a button that will not get the key is a 4.1.2 lie.
    const wrapper = mountBar({ statusCount: 12, statusLabel: "matches" });
    expect(wrapper.find(".key-hint").exists()).toBe(true);
    expect(
      wrapper.find(".clear-search-btn").attributes("aria-keyshortcuts"),
    ).toBe("Escape");

    await wrapper.setProps({ ownsEscape: false });
    expect(wrapper.find(".key-hint").exists()).toBe(false);
    expect(
      wrapper.find(".clear-search-btn").attributes("aria-keyshortcuts"),
    ).toBeUndefined();
    expect(wrapper.find(".clear-search-btn").attributes("title")).toBe(
      "Clear search — press Esc twice, or click",
    );
  });
});
