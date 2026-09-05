// "About your library" - the read-only findings screen.
//
// The screen is mostly prose the server writes, so the assertions worth having
// are the three things the VIEW decides:
//
// 1. a check that came back clear still gets a row, and reads as settled
//    rather than as a complaint - the whole reason the screen is not a nag;
// 2. a finding's button carries the server's action object out untouched, so
//    the tool opens on the pictures the evidence counted;
// 3. nothing on the screen offers a way to change anything.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

const getInsights = vi.fn();
vi.mock("../../api/insights", () => ({
  getInsights: (...args) => getInsights(...args),
}));

import LibraryInsights from "./LibraryInsights.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

const TODO = {
  id: "unsorted_pile",
  state: "todo",
  title: "900 pictures are in _unsorted and nowhere else",
  evidence: "They are in no set, no project and under nobody's name.",
  action: {
    label: "Sort them",
    note: "rapid triage",
    kind: "unassigned_in_folder",
    path: "/home/me/library/_unsorted",
    folder_label: "_unsorted",
  },
};

const CLEAR = {
  id: "untagged",
  state: "clear",
  title: "Your library is tagged",
  evidence: "11,900 of 12,000 pictures carry at least one tag.",
  action: null,
};

function payload(findings, extra = {}) {
  return {
    total_pictures: 12000,
    folder_pictures: 12000,
    folders: 200,
    findings,
    ...extra,
  };
}

async function mountScreen(body) {
  getInsights.mockResolvedValue(body);
  const wrapper = mount(LibraryInsights, globalOpts);
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  getInsights.mockReset();
});

describe("LibraryInsights - a clear check is still a row", () => {
  it("renders the checks that found nothing, and says so", async () => {
    const wrapper = await mountScreen(payload([TODO, CLEAR]));

    const rows = wrapper.findAll("[data-finding]");
    expect(rows).toHaveLength(2);

    const clear = wrapper.find('[data-finding="untagged"]');
    expect(clear.exists()).toBe(true);
    expect(clear.classes()).toContain("ins-find--clear");
    // It says "nothing to do" instead of offering a button. A clear row that
    // rendered a button would be inventing work the server did not find.
    expect(clear.text()).toContain("nothing to do");
    expect(clear.find("button").exists()).toBe(false);
    // And it still shows its evidence: "nothing to fix" with no number behind
    // it is indistinguishable from a check that never ran.
    expect(clear.text()).toContain("11,900");
  });

  it("leads with what was looked at, not with what is wrong", async () => {
    const wrapper = await mountScreen(payload([TODO, CLEAR]));
    const lede = wrapper.find(".ins-lede").text();
    expect(lede).toContain("12,000");
    expect(lede).toContain("2 things worth knowing");
    expect(lede).toContain("1 of them is worth a look");
    expect(lede).toContain("the one below it is fine as it is");
  });

  it("does not say 'the 0 below them are fine' when everything is a todo", async () => {
    // The messy pre-PixlStash library this release exists for is the all-todo
    // case, and the first version of this sentence greeted it with a tail
    // clause about zero rows.
    const wrapper = await mountScreen(
      payload([TODO, { ...TODO, id: "uncaptioned" }]),
    );
    const lede = wrapper.find(".ins-lede").text();
    expect(lede).not.toMatch(/\b0 below\b/);
    expect(lede).toContain("every one of them");
  });

  it("counts one clear row in the singular", async () => {
    const wrapper = await mountScreen(
      payload([TODO, { ...TODO, id: "uncaptioned" }, CLEAR]),
    );
    expect(wrapper.find(".ins-lede").text()).toContain(
      "the one below it is fine as it is",
    );
  });

  it("says it has nothing to suggest when every check is clear", async () => {
    const wrapper = await mountScreen(
      payload([CLEAR, { ...CLEAR, id: "uncaptioned" }]),
    );
    expect(wrapper.find(".ins-lede").text()).toContain("nothing to suggest");
    expect(wrapper.findAll("button[class*='app-btn']")).toHaveLength(1); // Look again
  });
});

describe("LibraryInsights - it says what it read", () => {
  it("names the split when part of the library has no folder names", async () => {
    const wrapper = await mountScreen(
      payload([CLEAR], {
        total_pictures: 12000,
        folder_pictures: 9000,
        folders: 200,
      }),
    );
    const scope = wrapper.find(".ins-scope").text();
    expect(scope).toContain("9,000");
    expect(scope).toContain("200 folders");
  });

  it("says nothing when every picture is in a folder it reads", async () => {
    // The case this release is built for. A line saying "12,000 of 12,000" is
    // noise above the findings.
    const wrapper = await mountScreen(payload([CLEAR]));
    expect(wrapper.find(".ins-scope").exists()).toBe(false);
  });
});

describe("LibraryInsights - the button opens the tool the finding names", () => {
  it("emits the server's action object unchanged", async () => {
    const wrapper = await mountScreen(payload([TODO]));

    await wrapper
      .find('[data-finding="unsorted_pile"] button')
      .trigger("click");

    expect(wrapper.emitted("act")).toHaveLength(1);
    // Verbatim: the view must not re-derive the path or the kind. The folder
    // the evidence counted is the folder the tool opens on, and any rewriting
    // here is a chance for the two to disagree.
    expect(wrapper.emitted("act")[0][0]).toEqual(TODO.action);
  });

  it("shows what the button opens under it", async () => {
    const wrapper = await mountScreen(payload([TODO]));
    expect(wrapper.find('[data-finding="unsorted_pile"]').text()).toContain(
      "rapid triage",
    );
  });
});

describe("LibraryInsights - it only ever reads", () => {
  it("re-reads on Look again and asks for nothing else", async () => {
    const wrapper = await mountScreen(payload([TODO]));
    expect(getInsights).toHaveBeenCalledTimes(1);

    await wrapper.find(".ins-tb-right button").trigger("click");
    await flushPromises();

    expect(getInsights).toHaveBeenCalledTimes(2);
    // The bar states the promise the screen is built on, and the footnote
    // repeats it where the eye lands last.
    expect(wrapper.text()).toContain("nothing here has been changed");
    expect(wrapper.text()).toContain("This screen only ever reads");
  });

  it("says so plainly when the library cannot be read", async () => {
    getInsights.mockRejectedValue({ message: "network down" });
    const wrapper = mount(LibraryInsights, globalOpts);
    await flushPromises();

    expect(wrapper.find('[role="alert"]').text()).toContain("network down");
    expect(wrapper.findAll("[data-finding]")).toHaveLength(0);
  });

  it("never renders a validation body as [object Object]", async () => {
    // `detail` is a STRING on a raised HTTPException and a LIST OF OBJECTS on
    // a FastAPI validation error. Only the second shape is the bug, and a test
    // that rejects with `{message}` alone cannot see it.
    getInsights.mockRejectedValue({
      response: { data: { detail: [{ loc: ["query"], msg: "bad" }] } },
      message: "Request failed with status code 422",
    });
    const wrapper = mount(LibraryInsights, globalOpts);
    await flushPromises();

    const alert = wrapper.find('[role="alert"]').text();
    expect(alert).not.toContain("[object Object]");
    expect(alert).toContain("Request failed");
  });

  it("gives the screen a heading above its findings", async () => {
    // The findings are `h3`. Without an `h2` over them the screen has no
    // outline for a screen reader to move through, and the visual style is
    // pinned separately by Toolbar.test.js - so this can only be an outline
    // change, never a look change.
    const wrapper = await mountScreen(payload([TODO, CLEAR]));
    expect(wrapper.find("h2.ins-title").text()).toBe("About your library");
    expect(wrapper.findAll("h3.ins-find-title")).toHaveLength(2);
  });

  it("invites the folder rather than reporting on an empty library", async () => {
    const wrapper = await mountScreen(
      payload([CLEAR], { total_pictures: 0, folder_pictures: 0, folders: 0 }),
    );
    expect(wrapper.find(".ins-lede").text()).toContain("no pictures here yet");
  });
});
