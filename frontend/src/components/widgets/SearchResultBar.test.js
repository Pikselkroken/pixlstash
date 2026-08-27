// The search half of the grid action pill. It reports a search and, for a
// person-scoped face search, offers the tuning panel and the bulk assignment
// ("Suggest more pictures of <person>", #636).
//
// The tests pin the things a user acts on: that the assign button states how
// many pictures it would write and to whom, that an explicit grid selection
// wins over the sliders, that both knobs are real, labelled range inputs rather
// than decorations, that the second one is absent when it could not filter
// anything, and, since the merge into one pill, that exactly one live region
// speaks and the controls do not vanish mid-search.

import { readFileSync } from "node:fs";

import { describe, it, expect, vi, afterEach } from "vitest";
import { mount } from "@vue/test-utils";

import SearchResultBar from "./SearchResultBar.vue";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-progress-circular": true,
      // Both slots: the tuning control has exactly one form now, so rendering
      // the panel alongside its trigger is not the duplication it once was.
      "v-menu": {
        template: "<div><slot name='activator' :props='{}'/><slot/></div>",
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
    statusLabel: "matches",
    threshold: 0.7,
    minRefs: 1,
    referenceCount: 7,
    assignTarget: "Alice",
    assignCount: 41,
    ...overrides,
  };
}

/** The nth slider group in the panel: 0 is match strength, 1 is agreement. */
function group(wrapper, index) {
  return wrapper.findAll(".threshold-group")[index];
}

/** A default armed search, for the assertions that only read the panel. */
function armedBar() {
  return mountBar(characterSearchProps());
}

afterEach(() => {
  vi.useRealTimers();
});

describe("SearchResultBar - plain search", () => {
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

describe("SearchResultBar - character face search", () => {
  it("states the blast radius on the assign button", () => {
    // "Assign all" hides how much is about to be written. The count is what
    // makes the sliders legible and the click safe to make.
    //
    // Asserted on the accessible name, not on rendered text: the word space
    // before the name is a CSS margin (see the .assign-target rule), so the
    // DOM text runs the count into it. The accessible name is a real string
    // built in JS and is the thing that must never degrade.
    const wrapper = mountBar(characterSearchProps());
    expect(wrapper.find(".assign-btn").attributes("aria-label")).toBe(
      "Assign 41 to Alice",
    );
    expect(wrapper.find(".assign-label").text()).toContain("Assign 41");
    expect(wrapper.find(".assign-target").text()).toBe("to Alice");
  });

  it("follows an explicit grid selection instead of the threshold", () => {
    // Writing 41 pictures when the user deliberately selected 12 is the error
    // this mode exists to prevent, so the label has to change with it.
    const wrapper = mountBar(
      characterSearchProps({ assignCount: 12, assignFromSelection: true }),
    );
    expect(wrapper.find(".assign-btn").attributes("aria-label")).toContain(
      "Assign 12 selected to Alice",
    );
    expect(wrapper.find(".assign-label").text()).toContain(
      "Assign 12 selected",
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

  it("drops the person's name before the count when space runs out", () => {
    // The name is its own element so the ladder can remove it whole. Ellipsising
    // the label produced "Assign 2 t…", which truncates mid-preposition and
    // costs the count its neighbour anyway.
    const wrapper = mountBar(characterSearchProps());
    expect(wrapper.find(".assign-target").text()).toBe("to Alice");
    expect(wrapper.find(".assign-label").text()).toContain("Assign 41");
  });

  it("spaces the name off the count with a margin, not a text space", () => {
    // `.assign-target` is inline-block so `text-overflow: ellipsis` has a box to
    // clip, and CSS strips leading whitespace at the start of an inline-block's
    // line box. A leading space in the markup therefore vanishes and the button
    // reads "Assign 0to Walter". jsdom applies no CSS, so no rendered-text
    // assertion can catch that; this pins both halves of the fix instead.
    const wrapper = mountBar(characterSearchProps());
    const target = wrapper.find(".assign-target");
    expect(target.text()).toBe("to Alice");
    expect(target.element.textContent).not.toMatch(/^\s/);

    const style = SearchResultBar.__file
      ? readFileSync(SearchResultBar.__file, "utf8")
      : readFileSync(new URL("./SearchResultBar.vue", import.meta.url), "utf8");
    const rule = style.slice(
      style.indexOf(".assign-target {"),
      style.indexOf("}", style.indexOf(".assign-target {")),
    );
    expect(rule).toContain("margin-left");
  });

  it("renders both knobs as labelled range inputs", () => {
    // Keyboard operability (WCAG 2.1.1) and a name for the control both come
    // free from a native range with a real label; a div with a drag handler
    // gives neither.
    const strength = group(armedBar(), 0);
    const input = strength.find(".search-result-threshold-input");
    expect(input.attributes("type")).toBe("range");
    const label = strength.find(".section-label");
    expect(label.attributes("for")).toBe(input.attributes("id"));
    expect(label.text()).toBe("Match strength");

    const refs = group(armedBar(), 1);
    const refsInput = refs.find(".search-result-threshold-input");
    expect(refsInput.attributes("type")).toBe("range");
    expect(refs.find(".section-label").text()).toBe("Reference faces");
    expect(refs.find(".section-label").attributes("for")).toBe(
      refsInput.attributes("id"),
    );
  });

  it("announces the cut as a percentage, not as a raw ratio", () => {
    // <output for> is a reverse relationship, not a labelling one, so without
    // this a screen reader reads "Match strength, slider, 0.7".
    const input = group(armedBar(), 0).find(".search-result-threshold-input");
    expect(input.attributes("aria-valuetext")).toBe("70%");
  });

  it("announces the agreement knob in references, not in raw steps", () => {
    // "slider, 2" says nothing; "2 of 7" is the whole meaning of the control.
    const input = group(armedBar(), 1).find(".search-result-threshold-input");
    expect(input.attributes("aria-valuetext")).toBe("1 of 7");
  });

  it("keeps every <output> out of the live region", () => {
    // <output> maps to role="status" by default, so each would announce on every
    // pointer sample of a drag, in parallel with the pill's own region, and
    // there are two of them now.
    const outputs = armedBar().findAll("output");
    expect(outputs.length).toBe(2);
    for (const out of outputs) {
      expect(out.attributes("aria-live")).toBe("off");
    }
  });

  it("shows the threshold as a percentage on the trigger", () => {
    // 0.7 is the stored value; 70% is the one a person reasons about. It stays
    // on the trigger because a standing filter must not disappear into a menu.
    expect(armedBar().find(".search-result-threshold-value").text()).toBe(
      "70%",
    );
  });

  it("keeps the agreement knob off the trigger until it filters something", () => {
    // At 1-of-7 it excludes nothing, and a permanent "1/7" reads as a live
    // constraint the user never set.
    expect(armedBar().find(".search-result-threshold-refs").exists()).toBe(
      false,
    );
    const engaged = mountBar(characterSearchProps({ minRefs: 2 }));
    expect(engaged.find(".search-result-threshold-refs").text()).toContain(
      "2/7",
    );
  });

  it("drops the agreement slider when there is nothing to choose", () => {
    // One reference face means its only legal position is its minimum, which is
    // chrome rather than a control. The strength slider stays.
    const wrapper = mountBar(characterSearchProps({ referenceCount: 1 }));
    expect(wrapper.findAll(".threshold-group")).toHaveLength(1);
    expect(wrapper.find(".search-result-threshold").exists()).toBe(true);
  });

  it("emits the new threshold while dragging, not only on release", () => {
    // The count has to track the drag. Listening on `change` instead of `input`
    // would leave the number stale until the pointer is let go.
    const wrapper = mountBar(characterSearchProps());
    const input = group(wrapper, 0).find(".search-result-threshold-input");
    input.element.value = "0.82";
    input.trigger("input");
    expect(wrapper.emitted("update:threshold")[0]).toEqual([0.82]);
  });

  it("emits the reference count as an integer while dragging", () => {
    // A range reports strings; emitting "3" would compare unequal to 3 in the
    // clamp on the other side and leave the slider un-movable.
    const wrapper = mountBar(characterSearchProps());
    const input = group(wrapper, 1).find(".search-result-threshold-input");
    input.element.value = "3";
    input.trigger("input");
    expect(wrapper.emitted("update:min-refs")[0]).toEqual([3]);
  });

  it("bounds the strength slider by the fetch floor", () => {
    // Below the floor there are no fetched results to reveal, so dragging there
    // would silently show fewer pictures than the number promised.
    const wrapper = mountBar(
      characterSearchProps({ thresholdMin: 0.5, thresholdMax: 0.95 }),
    );
    const input = group(wrapper, 0).find(".search-result-threshold-input");
    expect(input.attributes("min")).toBe("0.5");
    expect(input.attributes("max")).toBe("0.95");
  });

  it("bounds the agreement slider by the references that exist", () => {
    // Asking for 8 of 7 empties the grid with no way to see why.
    const input = group(armedBar(), 1).find(".search-result-threshold-input");
    expect(input.attributes("min")).toBe("1");
    expect(input.attributes("max")).toBe("7");
  });

  it("emits assign when the action is used", () => {
    const wrapper = mountBar(characterSearchProps());
    wrapper.find(".assign-btn").trigger("click");
    expect(wrapper.emitted("assign")).toHaveLength(1);
  });

  it("keeps the tuning control mounted while the search is still running", () => {
    // Hiding it collapsed the pill and snapped it back to full width when the
    // results landed, moving targets under a cursor already travelling toward
    // them. It stays, marked aria-disabled.
    const wrapper = mountBar(characterSearchProps({ imagesLoading: true }));
    expect(wrapper.find(".search-result-threshold").exists()).toBe(true);
    expect(
      wrapper.find(".search-result-tune-btn").attributes("aria-disabled"),
    ).toBe("true");
  });

  it("names both knobs in the trigger's accessible name", () => {
    // The visible label is two numbers and a separator. A control's name has to
    // stand on its own, without the pill around it.
    const wrapper = mountBar(characterSearchProps({ minRefs: 3 }));
    expect(
      wrapper.find(".search-result-tune-btn").attributes("aria-label"),
    ).toBe(
      "Tune suggestions. Match strength 70%, on at least 3 of 7 reference faces.",
    );
  });
});

describe("SearchResultBar - the live region", () => {
  it("carries the full sentence and the cut, debounced to one announcement", async () => {
    // A sighted user watches the number move with the slider; everyone else
    // needs it in a live region (WCAG 4.1.3) - but once per drag, not once per
    // pointer sample.
    vi.useFakeTimers();
    const wrapper = mountBar(characterSearchProps());
    const region = wrapper.find('[role="status"]');

    expect(region.text()).toBe("");
    vi.advanceTimersByTime(300);
    await wrapper.vm.$nextTick();
    expect(region.text()).toBe("41 matches at 70% or better");

    // Three rapid changes, one announcement.
    for (const value of [0.75, 0.8, 0.85]) {
      await wrapper.setProps({ threshold: value });
      vi.advanceTimersByTime(50);
    }
    expect(region.text()).toBe("41 matches at 70% or better");
    vi.advanceTimersByTime(300);
    await wrapper.vm.$nextTick();
    expect(region.text()).toBe("41 matches at 85% or better");
  });

  it("folds the agreement knob into the same sentence", async () => {
    // A second region for the second knob is the double-speak defect §6.4 was
    // written about: one drag, one announcement, both constraints in it.
    vi.useFakeTimers();
    const wrapper = mountBar(characterSearchProps({ minRefs: 3 }));
    vi.advanceTimersByTime(300);
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[role="status"]').text()).toBe(
      "41 matches at 70% or better, on at least 3 of 7 reference faces",
    );
  });

  it("is the only live region in the half", () => {
    const wrapper = mountBar(characterSearchProps());
    expect(wrapper.findAll('[aria-live="polite"]')).toHaveLength(1);
  });
});

describe("SearchResultBar - the Esc keycap", () => {
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
      "Clear search - press Esc twice, or click",
    );
  });
});
