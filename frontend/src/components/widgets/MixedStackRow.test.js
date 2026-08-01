// One row of the Mixed stacks page.
//
// The row's shape is the point of it, so these pin the shape: it must not look
// like `DedupGroupRow`, whose card treatment says "decide this now, with the
// keyboard, and I will advance". A second thing that looked like the queue
// would be read as a second queue with a second to-do count.
//
// The rest is what the row promises: the suspect run is the evidence and must
// never accuse the majority, the primary button names the outcome it is about
// to produce, and everything that writes is withheld from a read-only session.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

import MixedStackRow from "./MixedStackRow.vue";

vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id, { version } = {}) =>
    `/pictures/thumbnails/${id}.webp${version ? `?v=${version}` : ""}`,
}));

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "http://backend.test/api/v1",
  appendShareToken: (url) => url,
}));

const globalOpts = { global: { stubs: { "v-icon": true } } };

/** One `MixedStackModel` row, in the backend's shape. */
function stack(over = {}) {
  return {
    stack_id: 42,
    threshold: 0.9,
    member_count: 5,
    member_ids: [7, 8, 9, 10, 11],
    membership_fingerprint: "fp",
    component_count: 2,
    component_sizes: [4, 1],
    components: [[7, 8, 9, 10], [11]],
    largest_component_size: 4,
    stranded_picture_ids: [11],
    weakest_edge: 0.91,
    unhashed_picture_ids: [],
    suggested_action: "split",
    kept: false,
    leader_picture_id: 7,
    leader_thumbnail_version: "512x384",
    ...over,
  };
}

function mountRow(props = {}) {
  return mount(MixedStackRow, {
    ...globalOpts,
    props: { stack: stack(), ...props },
  });
}

describe("MixedStackRow: it is a list row, not a card", () => {
  // Every treatment that would make it read as a queue row is deliberately
  // absent; the divider between rows is the whole separation.
  it("carries no per-row border, background, radius or focus bar", () => {
    const wrapper = mountRow();
    const classes = wrapper.find(".mrow").classes();
    expect(classes).toContain("mrow");
    expect(classes).not.toContain("grow");
    expect(wrapper.find(".grow").exists()).toBe(false);
    expect(wrapper.find(".gthumb").exists()).toBe(false);
  });

  // The order IS the ranking. A printed numeral would promise a position that
  // changes the moment the threshold slider moves.
  it("prints no rank numeral", () => {
    const wrapper = mountRow();
    expect(wrapper.find(".mrow").text()).not.toMatch(/^\s*\d+[.)]/);
  });

  it("titles the row over its reason", () => {
    const wrapper = mountRow();
    expect(wrapper.find(".mtitle b").text()).toBe("Stack of 5");
    expect(wrapper.find(".mreason").text()).toBe(
      "1 picture doesn't match the rest",
    );
  });

  // The cover wears the same ticks and badge it does in the queue and the grid,
  // so the same stack reads the same way wherever it is met.
  it("draws the stack's leader wearing its ticks and badge", () => {
    const wrapper = mountRow();
    expect(wrapper.find(".mcover-img").attributes("src")).toContain(
      "/thumbnails/7.webp?v=512x384",
    );
    expect(wrapper.find('[data-testid="stack-edge-ticks"]').exists()).toBe(
      true,
    );
    const badge = wrapper.find('[data-testid="stack-badge"]');
    expect(badge.exists()).toBe(true);
    expect(badge.attributes("data-flagged")).toBe("true");
  });
});

describe("MixedStackRow: the suspects are the row's reason to exist", () => {
  it("shows the stranded member behind a warning border", () => {
    const wrapper = mountRow();
    const suspects = wrapper.findAll(".msuspect-img");
    expect(suspects).toHaveLength(1);
    expect(suspects[0].attributes("src")).toContain("/thumbnails/11.webp");
  });

  // The majority is what SURVIVES a split; showing four of its members behind
  // a warning border would accuse the wrong pictures.
  it("never shows the majority cluster in the soft case", () => {
    const wrapper = mountRow({
      stack: stack({
        stranded_picture_ids: [],
        suggested_action: "unstack",
        member_count: 6,
        components: [
          [1, 2, 3, 4],
          [5, 6],
        ],
        largest_component_size: 4,
      }),
    });
    const srcs = wrapper
      .findAll(".msuspect-img")
      .map((n) => n.attributes("src"));
    expect(srcs).toHaveLength(2);
    expect(srcs.join()).toContain("/thumbnails/5.webp");
    expect(srcs.join()).not.toContain("/thumbnails/1.webp");
  });

  it("counts the suspects that did not fit", () => {
    const wrapper = mountRow({
      stack: stack({
        member_count: 12,
        stranded_picture_ids: [1, 2, 3, 4, 5, 6, 7, 8],
        components: [[9, 10, 11, 12]],
        largest_component_size: 4,
      }),
    });
    expect(wrapper.findAll(".msuspect-img")).toHaveLength(6);
    expect(wrapper.find(".msuspect-more").text()).toBe("+2");
  });

  // A member the embedding worker has not reached carries no edge, so it looks
  // stranded without being unlike anything. It is reported as not yet
  // comparable, never as a mistake.
  it("reports an unanalysed member as not yet comparable", () => {
    const wrapper = mountRow({
      stack: stack({ unhashed_picture_ids: [11] }),
    });
    expect(wrapper.find(".mreason--soft").text()).toContain(
      "has not been analysed yet",
    );
  });
});

describe("MixedStackRow: the actions name their outcome", () => {
  it("names the split with the number it will move", () => {
    const wrapper = mountRow({
      stack: stack({ stranded_picture_ids: [11, 12] }),
    });
    expect(wrapper.text()).toContain("Split off 2");
    expect(wrapper.text()).not.toContain("Unstack");
  });

  it("offers Unstack when there is no majority worth keeping", () => {
    const wrapper = mountRow({
      stack: stack({ suggested_action: "unstack", stranded_picture_ids: [] }),
    });
    expect(wrapper.text()).toContain("Unstack");
    expect(wrapper.text()).not.toContain("Split off");
  });

  it("reports the primary action and the Keep separately", async () => {
    const wrapper = mountRow();
    const buttons = wrapper.findAll(".mactions button");
    await buttons[0].trigger("click");
    await buttons[1].trigger("click");
    expect(wrapper.emitted("resolve")).toHaveLength(1);
    expect(wrapper.emitted("keep")).toHaveLength(1);
  });

  // The return half of the two-way shortcut, and it is offered only when there
  // is somewhere real to land: the queue is paged, and scrolling to a guessed
  // row would be worse than not offering the jump at all.
  it("offers the way back only when a loaded group holds the stack", async () => {
    expect(mountRow().text()).not.toContain("In the queue");
    const wrapper = mountRow({ canShowQueue: true });
    expect(wrapper.text()).toContain("In the queue");
    await wrapper.findAll(".mactions button").at(-1).trigger("click");
    expect(wrapper.emitted("show-queue")).toHaveLength(1);
  });

  // A read-only session sees the evidence and none of the writes.
  it("withholds every write from a read-only session", () => {
    const wrapper = mountRow({ readOnly: true, canShowQueue: true });
    expect(wrapper.text()).not.toContain("Split off");
    expect(wrapper.text()).not.toContain("Keep");
    // Reading is still offered: the shortcut changes the page, not the library.
    expect(wrapper.text()).toContain("In the queue");
  });

  it("marks its buttons busy while an action is in flight", () => {
    const wrapper = mountRow({ busy: true });
    const primary = wrapper.findAll(".mactions button")[0];
    expect(primary.attributes("aria-busy")).toBe("true");
  });
});

describe("MixedStackRow: a stack a locked set has frozen", () => {
  /** The row shape the server serves for a frozen stack. */
  function frozen(over = {}) {
    return stack({
      stackable: false,
      blocked_by_sets: [{ id: 3, name: "Frozen" }],
      ...over,
    });
  }

  // Both writes answer 423 and change nothing, so the row must not offer
  // either. `aria-disabled`, never `disabled`: a disabled button leaves the tab
  // order, taking the reason it points at with it.
  it("blocks the primary action and names the set that froze it", () => {
    const wrapper = mountRow({ stack: frozen() });
    const primary = wrapper.findAll(".mactions button")[0];
    expect(primary.attributes("aria-disabled")).toBe("true");
    expect(primary.attributes("disabled")).toBeUndefined();
    expect(wrapper.find(".mlock").text()).toBe("Frozen by locked set 'Frozen'");
  });

  it("names every set when more than one is freezing the stack", () => {
    const wrapper = mountRow({
      stack: frozen({
        blocked_by_sets: [
          { id: 3, name: "Frozen" },
          { id: 4, name: "Archive" },
        ],
      }),
    });
    expect(wrapper.find(".mlock").text()).toBe(
      "Frozen by locked sets 'Frozen, Archive'",
    );
  });

  // A refusal with no set names still has to say something the user can act
  // on, rather than a disabled button with no explanation at all.
  it("still says what is wrong when the sets were not named", () => {
    const wrapper = mountRow({ stack: frozen({ blocked_by_sets: [] }) });
    expect(wrapper.find(".mlock").text()).toBe("Frozen by a locked set");
  });

  // The reason has to survive a user who never hovers: it is real text in the
  // row, it is what the button points at, and it rides in the row's own name.
  it("puts the reason where assistive tech will find it, not only in a tooltip", () => {
    const wrapper = mountRow({ stack: frozen() });
    const note = wrapper.find(".mlock");
    const primary = wrapper.findAll(".mactions button")[0];
    expect(note.attributes("id")).toBeTruthy();
    expect(primary.attributes("aria-describedby")).toBe(note.attributes("id"));
    expect(primary.attributes("title")).toContain("'Frozen'");
    expect(primary.attributes("title")).toContain("Unlock it");
    expect(wrapper.find(".mrow").attributes("aria-label")).toContain(
      "Frozen by locked set 'Frozen'",
    );
  });

  // Keep writes a dismissal, not a picture: the backend's keep route carries no
  // lock guard, so disabling it would strand the one row the user cannot
  // otherwise clear from the list.
  it("leaves Keep and the way back to the queue fully live", () => {
    const wrapper = mountRow({ stack: frozen(), canShowQueue: true });
    const buttons = wrapper.findAll(".mactions button");
    expect(buttons[1].text()).toContain("Keep");
    expect(buttons[1].attributes("aria-disabled")).toBeUndefined();
    expect(buttons[2].attributes("aria-disabled")).toBeUndefined();
  });

  // The page owns the guard, the same way the review decision bar does, so the
  // press is answered out loud instead of reading as a dead control.
  it("still reports the press, so the page can say why it cannot run", async () => {
    const wrapper = mountRow({ stack: frozen() });
    await wrapper.findAll(".mactions button")[0].trigger("click");
    expect(wrapper.emitted("resolve")).toHaveLength(1);
  });

  // Over-blocking is its own regression, and a silent one: a live row must keep
  // its action and must not grow a reason it does not have.
  it("leaves a stackable row alone, with no lock reason anywhere", () => {
    const live = mountRow({ stack: stack({ stackable: true }) });
    const primary = live.findAll(".mactions button")[0];
    expect(primary.attributes("aria-disabled")).toBeUndefined();
    expect(live.find(".mlock").exists()).toBe(false);
    expect(live.find(".mrow").attributes("aria-label")).not.toContain("Frozen");
    // And a server that does not publish the pair at all is not a lock either.
    const legacy = mountRow();
    expect(
      legacy.findAll(".mactions button")[0].attributes("aria-disabled"),
    ).toBeUndefined();
    expect(legacy.find(".mlock").exists()).toBe(false);
  });

  // The sighted counterpart to the announcement, for the user who is looking at
  // the row rather than listening to it. The page owns its lifetime.
  it("flashes the note when the page says this row is the one that refused", () => {
    expect(
      mountRow({ stack: frozen() }).find(".mlock").classes(),
    ).not.toContain("mlock--flash");
    expect(
      mountRow({ stack: frozen(), lockFlash: true }).find(".mlock").classes(),
    ).toContain("mlock--flash");
  });
});

describe("MixedStackRow: what a screen reader hears", () => {
  // The visible row spreads its meaning across a title, a reason line and a run
  // of unlabelled thumbnails; none of that reaches assistive tech as a unit.
  it("carries the row's whole meaning in one accessible name", () => {
    const name = mountRow().find(".mrow").attributes("aria-label");
    expect(name).toContain("Stack of 5");
    expect(name).toContain("1 picture doesn't match the rest");
    expect(name).toContain("Split off 1");
  });

  it("names the suspect run for what it is", () => {
    expect(mountRow().find(".msuspects").attributes("aria-label")).toContain(
      "does not match the rest",
    );
  });
});
