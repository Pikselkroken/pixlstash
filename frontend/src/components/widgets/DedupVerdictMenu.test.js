// The Decided page's filter: which decisions the list shows.
//
// The tests pin what the component decides for itself rather than what the
// store hands it: that both directions of the toggle actually reach the parent,
// that the counts it renders are the unfiltered ones (a hidden row must not
// read as "there are none"), and that the last row standing cannot be switched
// off - an empty gate can only ever produce an empty page, which reads as a
// broken queue rather than as a choice the user made.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import DedupVerdictMenu from "./DedupVerdictMenu.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

/** The rows the store publishes, with `over` applied per id. */
function rows(over = {}) {
  return [
    {
      id: "stacked",
      label: "Stacked",
      hint: "folded into one stack",
      count: 12,
      enabled: true,
      ...(over.stacked ?? {}),
    },
    {
      id: "keep_separate",
      label: "Kept separate",
      hint: "not duplicates",
      count: 4,
      enabled: true,
      ...(over.keep_separate ?? {}),
    },
  ];
}

function mountMenu(props = {}) {
  return mount(DedupVerdictMenu, {
    ...globalOpts,
    props: { verdicts: rows(), groupCount: 16, ...props },
  });
}

describe("DedupVerdictMenu", () => {
  it("names both decisions and their counts", () => {
    const text = mountMenu().text();
    for (const phrase of ["Stacked", "Kept separate", "12", "4", "16 groups"]) {
      expect(text).toContain(phrase);
    }
  });

  it("reports a toggle in both directions", async () => {
    const menu = mountMenu();
    await menu.findAll(".vrow")[1].trigger("click");
    expect(menu.emitted("toggle")[0]).toEqual(["keep_separate", false]);

    const narrowed = mountMenu({
      verdicts: rows({ keep_separate: { enabled: false } }),
    });
    await narrowed.findAll(".vrow")[1].trigger("click");
    expect(narrowed.emitted("toggle")[0]).toEqual(["keep_separate", true]);
  });

  // The count is the MENU's, not the page's: it says what turning a hidden row
  // back on would bring, so it survives that row being hidden.
  it("keeps a hidden row's count and its way back", () => {
    const menu = mountMenu({
      verdicts: rows({ keep_separate: { enabled: false } }),
    });
    const row = menu.findAll(".vrow")[1];
    expect(row.text()).toContain("4");
    expect(row.attributes("aria-pressed")).toBe("false");
    expect(row.attributes("disabled")).toBeUndefined();
  });

  it("holds the last verdict on, and says why", () => {
    const menu = mountMenu({
      verdicts: rows({ keep_separate: { enabled: false } }),
    });
    const last = menu.findAll(".vrow")[0];
    expect(last.attributes("disabled")).toBeDefined();
    expect(last.attributes("title")).toContain("the page is empty");
    expect(menu.emitted("toggle")).toBeUndefined();
  });
});
