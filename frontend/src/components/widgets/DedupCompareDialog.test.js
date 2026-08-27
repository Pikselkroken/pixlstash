// The compare dialog is where a duplicate group is actually adjudicated, so
// these tests pin the things that would quietly make the decision wrong: which
// value is marked best in each column, which candidate shows its path, and that
// the two card gestures (pick a cover, leave a copy out) stay separate.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

// The thumbnail URL builder pulls in the Axios client; the dialog only needs a
// string for `<img src>`.
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/pictures/thumbnails/${id}.webp`,
}));

// The stack member read is the dialog's one network call: the leader's row when
// the group does not carry it, and the whole member list when an expansion
// opens.
const { listStackMembersMock } = vi.hoisted(() => ({
  listStackMembersMock: vi.fn(),
}));
vi.mock("../../api/dedup", () => ({
  listStackMembers: listStackMembersMock,
  MAX_STACK_MEMBER_PAGE: 200,
}));

import DedupCompareDialog from "./DedupCompareDialog.vue";
import AppButton from "./AppButton.vue";

const AppDialogStub = {
  name: "AppDialog",
  template:
    "<div><slot name='header-right'/><slot/><slot name='footer'/></div>",
};

const globalOpts = {
  global: { stubs: { "v-icon": true, AppDialog: AppDialogStub } },
};

// Three copies with a different winner per column: biggest picture, biggest
// file and best score, and the most tags.
// The backend's `DedupGroupModel` shape. Three copies with a different winner
// per column: biggest picture, biggest file and best score, and the most tags.
// The last one is the reference-folder copy, the only one that shows a path.
const GROUP = {
  signature: "g1",
  tier: "near",
  confidence: 0.92,
  member_count: 3,
  cover_picture_id: 1,
  why: [
    { text: "Same camera", against: false },
    { text: "Different crop", against: true },
  ],
  candidates: [
    {
      picture_id: 1,
      width: 4000,
      height: 3000,
      megapixels: 12,
      size_bytes: 5_000_000,
      format: "JPEG",
      is_raw: false,
      created_at: "2026-05-01T09:00:00",
      score: 2,
      tag_count: 3,
      file_path: null,
      reference_folder_id: null,
    },
    {
      picture_id: 2,
      width: 2000,
      height: 1500,
      megapixels: 3,
      size_bytes: 9_500_000,
      format: "PNG",
      is_raw: false,
      created_at: "2026-05-01T09:00:01",
      score: 4,
      tag_count: 1,
      file_path: null,
      reference_folder_id: null,
    },
    {
      picture_id: 3,
      width: 1000,
      height: 750,
      megapixels: 0.75,
      size_bytes: 1_000_000,
      format: "JPEG",
      is_raw: false,
      created_at: "2026-05-01T09:00:02",
      score: 0,
      tag_count: 12,
      file_path: "/mnt/ref/2024/img.jpg",
      reference_folder_id: 3,
    },
  ],
};

function mountDialog(props = {}) {
  return mount(DedupCompareDialog, {
    ...globalOpts,
    props: { open: true, group: GROUP, coverId: 1, ...props },
  });
}

/** The metadata values of one card, in render order. */
function values(card) {
  return card.findAll(".dc-meta .dc-cell .dc-val");
}

/** The footer verdict buttons, in render order: Close, Keep separate, Stack. */
function footerButtons(wrapper) {
  return wrapper.findAllComponents(AppButton);
}

/**
 * One button by its visible label.
 *
 * The positional helper above only holds while the footer's buttons are the
 * ONLY ones mounted, and an error state (the expansion band's retry, the mixed
 * read's) adds one ahead of them.
 */
function buttonLabelled(wrapper, label) {
  return wrapper
    .findAllComponents(AppButton)
    .find((button) => button.text().includes(label));
}

describe("DedupCompareDialog: the comparison", () => {
  it("uses the single supporting similarity treatment in the header", () => {
    const wrapper = mountDialog();
    const confidence = wrapper.find(".conf-pill");
    expect(confidence.text()).toBe("92% similar");
    expect(confidence.classes()).toContain("conf-pill--near");
    expect(confidence.attributes("aria-label")).toBe(
      "92% similar. Supports stacking.",
    );
    expect(wrapper.find(".dc-confidence").exists()).toBe(false);
  });

  it("marks the winner of each column, one card at a time", () => {
    // A single wrong best-mark is the whole point of the dialog going wrong:
    // the user picks the cover by reading which value is emphasised.
    const cards = mountDialog().findAll(".dc-card");
    const best = (card) =>
      values(card).map((v) => v.classes().includes("dc-val--best"));

    // ID, then Resolution, File, Captured, Score, Metadata.
    expect(best(cards[0]).slice(1, 6)).toEqual([
      true,
      false,
      false,
      false,
      false,
    ]);
    expect(best(cards[1]).slice(1, 6)).toEqual([
      false,
      true,
      false,
      true,
      false,
    ]);
    expect(best(cards[2]).slice(1, 6)).toEqual([
      false,
      false,
      false,
      false,
      true,
    ]);
  });

  it("shows a shortened path only for the reference-folder copy", () => {
    // A managed-library path is an implementation detail; showing it everywhere
    // buries the values that matter under noise.
    const cards = mountDialog().findAll(".dc-card");
    expect(cards[0].text()).not.toContain("/mnt/ref");
    expect(cards[1].text()).not.toContain("/mnt/ref");

    const path = cards[2].find(".dc-path");
    expect(path.text()).toContain("…/2024/img.jpg");
    expect(path.attributes("title")).toBe("/mnt/ref/2024/img.jpg");
  });

  it("keeps the Location row on every card once one copy has a path", () => {
    // The regression this pins is a visual one with teeth: the row is an extra
    // line in the meta grid, the meta grid takes its height off the image, so
    // rendering it per candidate left the pictures at different heights and the
    // copies could no longer be compared against each other.
    const cards = mountDialog().findAll(".dc-card");
    for (const card of cards) {
      expect(card.findAll(".dc-cell--wide")).toHaveLength(1);
    }
    expect(cards[0].find(".dc-cell--wide").text()).toContain("In your library");
  });

  it("drops the Location row entirely when no copy is in a reference folder", () => {
    // Nothing to say about location, so nothing is said: the row exists for the
    // user who manages their own files, not as a permanent empty field.
    const managed = {
      ...GROUP,
      candidates: GROUP.candidates.map((c) => ({
        ...c,
        file_path: null,
        reference_folder_id: null,
      })),
    };
    const cards = mountDialog({ group: managed }).findAll(".dc-card");
    for (const card of cards) {
      expect(card.find(".dc-cell--wide").exists()).toBe(false);
    }
  });

  it("renders the counter-evidence pill first", () => {
    // The red pill is the reason this group needs a careful look, so it must
    // not be pushed off the end of the row by the supporting evidence.
    const pills = mountDialog().findAll(".why-pill");
    expect(pills[0].text()).toContain("Different crop");
    expect(pills[0].classes()).toContain("why-pill--neg");
  });
});

describe("DedupCompareDialog: the blink compare (zoom)", () => {
  function zoomEl() {
    return document.querySelector('[data-testid="dedup-zoom"]');
  }

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("opens full-screen from a card's zoom button and flips in place", async () => {
    const wrapper = mountDialog();
    expect(zoomEl()).toBeNull();

    await wrapper.findAll(".dc-zoom")[1].trigger("click");
    expect(zoomEl()).not.toBeNull();
    expect(wrapper.vm.isZoomOpen()).toBe(true);

    // Flip wraps: a blink loop, not a bounded carousel.
    wrapper.vm.flipZoom(1);
    await wrapper.vm.$nextTick();
    wrapper.vm.flipZoom(1);
    await wrapper.vm.$nextTick();
    const onButtons = Array.from(
      zoomEl().querySelectorAll(".dc-zv-flip button"),
    ).map((b) => b.classList.contains("dc-zv-on"));
    expect(onButtons.filter(Boolean)).toHaveLength(1);

    wrapper.vm.closeZoom();
    await wrapper.vm.$nextTick();
    expect(zoomEl()).toBeNull();
    wrapper.unmount();
  });

  it("opens on the cover when no candidate was named (the Z key path)", async () => {
    const wrapper = mountDialog({ coverId: 2 });
    wrapper.vm.openZoom();
    await wrapper.vm.$nextTick();
    const on = Array.from(
      zoomEl().querySelectorAll(".dc-zv-flip button"),
    ).findIndex((b) => b.classList.contains("dc-zv-on"));
    expect(GROUP.candidates[on].picture_id).toBe(2);
    wrapper.unmount();
  });

  it("resets the zoom for the next group", async () => {
    const wrapper = mountDialog();
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    // A new group must start un-zoomed at Fit: a held-over zoom would open
    // on the wrong picture.
    await wrapper.setProps({ group: { ...GROUP, signature: "other" } });
    expect(wrapper.vm.isZoomOpen()).toBe(false);
    wrapper.unmount();
  });
});

describe("DedupCompareDialog: the wheel", () => {
  function zoomEl() {
    return document.querySelector('[data-testid="dedup-zoom"]');
  }

  /** The zero-based index of the candidate the zoom is showing. */
  function zoomIndexShown() {
    return Array.from(
      zoomEl().querySelectorAll(".dc-zv-flip button"),
    ).findIndex((b) => b.classList.contains("dc-zv-on"));
  }

  function wheel(el, deltaY) {
    const event = new window.WheelEvent("wheel", {
      deltaY,
      bubbles: true,
      cancelable: true,
    });
    el.dispatchEvent(event);
    return event;
  }

  afterEach(() => {
    document.body.innerHTML = "";
  });

  /** Give the zoom real geometry: an 800×600 viewport over a 1600×1200
   * image → fit scale 0.5. jsdom computes no layout, so the metrics the
   * component reads are defined directly and the image's load dispatched. */
  async function primeZoom(wrapper, { iw = 1600, ih = 1200 } = {}) {
    const surface = zoomEl().querySelector(".dc-zv-img");
    const img = surface.querySelector("img");
    Object.defineProperty(surface, "clientWidth", {
      value: 800,
      configurable: true,
    });
    Object.defineProperty(surface, "clientHeight", {
      value: 600,
      configurable: true,
    });
    surface.getBoundingClientRect = () => ({ left: 0, top: 0 });
    Object.defineProperty(img, "naturalWidth", {
      value: iw,
      configurable: true,
    });
    Object.defineProperty(img, "naturalHeight", {
      value: ih,
      configurable: true,
    });
    img.dispatchEvent(new Event("load"));
    await wrapper.vm.$nextTick();
    return surface;
  }

  // The wheel means ZOOM for the whole gesture: wheel UP over a candidate's
  // picture opens the zoom, and the same motion keeps magnifying inside it.
  it("wheel up opens the zoom on that candidate; wheel down does not", async () => {
    const wrapper = mountDialog();
    await wrapper.findAll(".dc-thumb")[1].trigger("wheel", { deltaY: 3 });
    expect(wrapper.vm.isZoomOpen()).toBe(false);
    await wrapper.findAll(".dc-thumb")[1].trigger("wheel", { deltaY: -3 });
    expect(wrapper.vm.isZoomOpen()).toBe(true);
    expect(zoomIndexShown()).toBe(1);
    wrapper.unmount();
  });

  it("continued wheeling zooms in - it never flips the candidate", async () => {
    const wrapper = mountDialog();
    await wrapper.findAll(".dc-thumb")[0].trigger("wheel", { deltaY: -100 });
    const surface = await primeZoom(wrapper);
    expect(wrapper.vm.zoomLevel()).toBeCloseTo(0.5, 5); // opened at fit

    const event = wheel(surface, -100);
    await wrapper.vm.$nextTick();
    expect(zoomIndexShown()).toBe(0);
    expect(wrapper.vm.zoomLevel()).toBeCloseTo(0.5 * Math.exp(0.2), 4);
    // The wheel never scrolls the page or the dialog behind the zoom.
    expect(event.defaultPrevented).toBe(true);
    expect(wheel(surface, 100).defaultPrevented).toBe(true);
    wrapper.unmount();
  });

  // Wheeling out THREE full notches of deliberate resistance while already at
  // the fit floor closes the zoom back to Compare (ZOOM_EXIT_RESISTANCE; the
  // owner raised it from one notch, which exited too easily). The
  // accumulation is the hysteresis, so trackpad crumbs cannot blow through
  // and the boundary cannot flap.
  it("closes exactly once: three out-notches at the fit floor, not before", async () => {
    const wrapper = mountDialog();
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    const surface = await primeZoom(wrapper);

    // Zoom in, then a huge out-wheel: clamps AT the floor, must not close.
    wheel(surface, -300);
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.zoomLevel()).toBeGreaterThan(0.5);
    wheel(surface, 2000);
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.zoomLevel()).toBeCloseTo(0.5, 5);
    expect(wrapper.vm.isZoomOpen()).toBe(true);

    // At the floor, sub-resistance accumulation keeps the zoom open - a full
    // two notches is still not enough...
    wheel(surface, 120);
    wheel(surface, 120);
    expect(wrapper.vm.isZoomOpen()).toBe(true);
    // ...and the tick that completes the third notch closes, once.
    wheel(surface, 120);
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.isZoomOpen()).toBe(false);
    expect(zoomEl()).toBeNull();
    wrapper.unmount();
  });

  it("a zoom-in between out-ticks resets the exit resistance", async () => {
    const wrapper = mountDialog();
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    const surface = await primeZoom(wrapper);

    wheel(surface, 120);
    wheel(surface, 120);
    // A zoom-in leaves the floor and clears the accumulation; the huge
    // out-wheel after it only clamps back to fit.
    wheel(surface, -100);
    await wrapper.vm.$nextTick();
    wheel(surface, 2000);
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.zoomLevel()).toBeCloseTo(0.5, 5);
    // Two more notches: still under the fresh resistance.
    wheel(surface, 120);
    wheel(surface, 120);
    expect(wrapper.vm.isZoomOpen()).toBe(true);
    wrapper.unmount();
  });

  // The blink guarantee: a flip keeps the magnification (and the pan), so
  // differences read as motion, not as a reset.
  it("flipping candidates preserves the zoom level", async () => {
    const wrapper = mountDialog();
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    const surface = await primeZoom(wrapper);
    wheel(surface, -200);
    await wrapper.vm.$nextTick();
    const level = wrapper.vm.zoomLevel();
    expect(level).toBeGreaterThan(0.5);

    wrapper.vm.flipZoom(1);
    await wrapper.vm.$nextTick();
    expect(zoomIndexShown()).toBe(1);
    expect(wrapper.vm.zoomLevel()).toBe(level);
    wrapper.unmount();
  });

  it("drags pan the overflowing view; the wheel never does", async () => {
    const wrapper = mountDialog();
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    const surface = await primeZoom(wrapper);
    wheel(surface, -600); // well past fit: the view overflows
    await wrapper.vm.$nextTick();

    surface.scrollLeft = 100;
    surface.scrollTop = 80;
    surface.dispatchEvent(
      new window.MouseEvent("mousedown", {
        clientX: 100,
        clientY: 100,
        button: 0,
        bubbles: true,
      }),
    );
    surface.dispatchEvent(
      new window.MouseEvent("mousemove", {
        clientX: 80,
        clientY: 90,
        bubbles: true,
      }),
    );
    expect(surface.scrollLeft).toBe(120);
    expect(surface.scrollTop).toBe(90);
    wrapper.unmount();
  });

  // Fit and 100% are snap stops on the continuum: P flips between them and
  // the readout names where you are (100% = actual pixels, the photo-tool
  // convention).
  it("P snaps between fit and actual pixels, with the percentage shown", async () => {
    const wrapper = mountDialog();
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    await primeZoom(wrapper);
    expect(zoomEl().querySelector(".dc-zv-pct").textContent).toBe("50%");

    wrapper.vm.toggleZoomPixels();
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.zoomLevel()).toBe(1);
    expect(zoomEl().querySelector(".dc-zv-pct").textContent).toBe("100%");

    wrapper.vm.toggleZoomPixels();
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.zoomLevel()).toBeCloseTo(0.5, 5);
    wrapper.unmount();
  });
});

describe("DedupCompareDialog: closing peels one layer", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  // AppDialog claims Escape on its own subtree and Vuetify's ESC/scrim close
  // arrives the same way, so the dialog's close intent must respect the zoom
  // layer or ESC with the zoom up would close both at once.
  it("a close request with the zoom up closes only the zoom; the next one closes the dialog", async () => {
    const wrapper = mountDialog();
    wrapper.vm.openZoom(1);
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.isZoomOpen()).toBe(true);

    const dialog = wrapper.findComponent({ name: "AppDialog" });
    dialog.vm.$emit("close");
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.isZoomOpen()).toBe(false);
    expect(wrapper.emitted("close")).toBeUndefined();

    dialog.vm.$emit("close");
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });
});

describe("DedupCompareDialog: the smart score column", () => {
  /** The default GROUP with smart scores stamped onto its three copies. */
  function scoredGroup(scores) {
    return {
      ...GROUP,
      candidates: GROUP.candidates.map((c, i) => ({
        ...c,
        smart_score: scores[i],
      })),
    };
  }

  /** The card's Smart score cell, or null when the column is absent. */
  function smartCellOf(card) {
    const cell = card
      .findAll(".dc-cell")
      .find((c) => c.find(".dc-label").text() === "Smart score");
    return cell ?? null;
  }

  // The metadata panel's own precision (toFixed(2)); the dash for a copy
  // whose siblings have a score and it does not - the row is group-level,
  // like Location, so the cards keep the same shape.
  it("shows each copy's smart score, best-marked, on every card", () => {
    const cards = mountDialog({
      group: scoredGroup([3.7156, 2.1, null]),
    }).findAll(".dc-card");
    const cells = cards.map(smartCellOf);
    expect(cells.every(Boolean)).toBe(true);
    expect(cells.map((c) => c.find(".dc-val").text())).toEqual([
      "3.72",
      "2.10",
      "–",
    ]);
    expect(cells[0].find(".dc-val").classes()).toContain("dc-val--best");
    expect(cells[1].find(".dc-val").classes()).not.toContain("dc-val--best");
  });

  // Today's backend serves no smart_score at all: no column, anywhere.
  it("renders no column when no copy has a score", () => {
    const cards = mountDialog().findAll(".dc-card");
    for (const card of cards) {
      expect(smartCellOf(card)).toBeNull();
    }
  });

  // -1.0 is "computation failed" and NULL is "not yet computed": neither is
  // a number a person should read, so a group with only those has no column.
  it("treats failed (-1) and pending (null) scores as absent", () => {
    const cards = mountDialog({
      group: scoredGroup([-1.0, null, -1.0]),
    }).findAll(".dc-card");
    for (const card of cards) {
      expect(smartCellOf(card)).toBeNull();
    }
  });

  it("carries the smart score into the zoom's meta line", async () => {
    const wrapper = mountDialog({ group: scoredGroup([3.7156, null, null]) });
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    expect(
      document.querySelector('[data-testid="dedup-zoom"] .dc-zv-meta')
        .textContent,
    ).toContain("Smart score 3.72");
    wrapper.vm.closeZoom();
    await wrapper.vm.$nextTick();
    document.body.innerHTML = "";
    wrapper.unmount();
  });
});

describe("DedupCompareDialog: the sharpness column", () => {
  /** The default GROUP with sharpness stamped onto its three copies. */
  function sharpGroup(values) {
    return {
      ...GROUP,
      candidates: GROUP.candidates.map((c, i) => ({
        ...c,
        sharpness: values[i],
      })),
    };
  }

  /** The card's Sharpness cell, or null when the column is absent. */
  function sharpCellOf(card) {
    const cell = card
      .findAll(".dc-cell")
      .find((c) => c.find(".dc-label").text() === "Sharpness");
    return cell ?? null;
  }

  // Three decimals, not the Smart score's two: the server serialises the
  // metric at 3dp on a typical 0-0.5 range, where two decimals would flatten
  // genuinely different copies into the same number. Group-level presence
  // and the best mark work exactly as the Smart score column's.
  it("shows each copy's sharpness, best-marked, on every card", () => {
    const cards = mountDialog({
      group: sharpGroup([0.312, 0.1876, null]),
    }).findAll(".dc-card");
    const cells = cards.map(sharpCellOf);
    expect(cells.every(Boolean)).toBe(true);
    expect(cells.map((c) => c.find(".dc-val").text())).toEqual([
      "0.312",
      "0.188",
      "–",
    ]);
    expect(cells[0].find(".dc-val").classes()).toContain("dc-val--best");
    expect(cells[1].find(".dc-val").classes()).not.toContain("dc-val--best");
  });

  it("renders no column when no copy has a usable metric", () => {
    for (const wrapper of [
      mountDialog(),
      mountDialog({ group: sharpGroup([null, null, null]) }),
    ]) {
      for (const card of wrapper.findAll(".dc-card")) {
        expect(sharpCellOf(card)).toBeNull();
      }
    }
  });
});

describe("DedupCompareDialog: the card gestures", () => {
  it("makes a clicked card the cover, and marks only that card pressed", () => {
    // aria-pressed is the only cover signal a screen-reader user gets.
    const wrapper = mountDialog({ coverId: 3 });
    const cards = wrapper.findAll(".dc-card");
    expect(
      cards.map((c) => c.find(".dc-pick").attributes("aria-pressed")),
    ).toEqual(["false", "false", "true"]);

    cards[1].find(".dc-pick").trigger("click");
    expect(wrapper.emitted("set-cover")).toEqual([[2]]);
  });

  it("leaves a copy out from the in-stack toggle without changing the cover", () => {
    // The toggle sits inside the card; without the stop it would also promote
    // that copy to cover, which is the opposite of what the user asked for.
    const wrapper = mountDialog();
    wrapper.findAll(".dc-card")[1].find(".dc-toggle").trigger("click");
    expect(wrapper.emitted("toggle-excluded")).toEqual([[2]]);
    expect(wrapper.emitted("set-cover")).toBeUndefined();
  });

  it("leaves a copy out on right-click", () => {
    const wrapper = mountDialog();
    wrapper.findAll(".dc-card")[0].trigger("contextmenu");
    expect(wrapper.emitted("toggle-excluded")).toEqual([[1]]);
  });
});

describe("DedupCompareDialog: the verdict footer", () => {
  it("counts down the Stack label as copies are left out", () => {
    // The label is the user's only confirmation of how big the stack will be.
    expect(footerButtons(mountDialog())[2].text()).toContain("Stack 3");
    expect(
      footerButtons(mountDialog({ excludedIds: [2] }))[2].text(),
    ).toContain("Stack 2");
  });

  it("locks both verdicts while one is in flight, but never Close", () => {
    // A double-click on Stack would create the stack twice.
    const buttons = footerButtons(mountDialog({ busy: true }));
    expect(buttons[0].find("button").attributes("disabled")).toBeUndefined();
    expect(buttons[1].find("button").attributes("disabled")).toBeDefined();
    expect(buttons[2].find("button").attributes("disabled")).toBeDefined();
  });

  // A shortcut shown next to the action it triggers is the only kind anyone
  // discovers; Stack always wore its Enter chip, Keep separate lacked its S.
  // Amendment #3: K keeps separate; S became Stack's synonym (taught in
  // copy, not chrome - one chip per button, the primary key shown). The
  // machine-readable set rides aria-keyshortcuts, since the chips are
  // aria-hidden.
  it("shows the shortcut on both verdicts: Enter on Stack, K on Keep separate", () => {
    const buttons = footerButtons(mountDialog());
    expect(buttons[1].find("kbd").text()).toBe("K");
    expect(buttons[1].find("button").attributes("aria-keyshortcuts")).toBe("K");
    expect(buttons[2].find("kbd").text()).toBe("↵");
  });

  it("emits the verdict the user picked", () => {
    const wrapper = mountDialog();
    const buttons = footerButtons(wrapper);
    buttons[1].find("button").trigger("click");
    buttons[2].find("button").trigger("click");
    expect(wrapper.emitted("keep-separate")).toHaveLength(1);
    expect(wrapper.emitted("stack")).toHaveLength(1);
  });

  // A share session can open Compare, because reading the comparison is not a
  // verdict. Offering it two buttons the server will refuse is worse than
  // offering none.
  it("drops the verdicts, but not Close, in a read-only session", () => {
    const buttons = footerButtons(mountDialog({ readOnly: true }));
    expect(buttons).toHaveLength(1);
    expect(buttons[0].text()).toContain("Close");
  });

  it("drops the gesture hint in a read-only session", () => {
    expect(mountDialog({ readOnly: true }).find(".dc-hint").exists()).toBe(
      false,
    );
  });

  // The keys work inside Compare, so the hint that teaches the gestures has to
  // name them too, and it has to repeat the one fact that makes the verdict
  // safe to give without a confirmation.
  it("names the keys and the zero deletions in the hint", () => {
    const hint = mountDialog().find(".dc-hint").text();
    expect(hint).toContain("press its number");
    expect(hint).toContain("press X");
    expect(hint).toContain("No file is ever deleted");
  });
});

// ── Units ──────────────────────────────────────────────────────────────────
// A verdict moves UNITS (a whole existing stack goes in or stays out as one),
// so the dialog compares units, exactly as the queue row does. The fixture is
// the common shape and the hard one: a live stack of four, only two of whose
// members this group named, whose LEADER is not in the group at all.

/** One member of stack 12, in canonical stack order (leader first). */
const STACK_12_MEMBERS = [
  {
    picture_id: 501,
    position: 0,
    is_leader: true,
    width: 6000,
    height: 4000,
    megapixels: 24,
    size_bytes: 12_000_000,
    format: "JPEG",
    is_raw: false,
    created_at: "2026-04-01T08:00:00",
    score: 5,
    tag_count: 9,
    thumbnail_version: "v501",
    file_path: null,
    reference_folder_id: null,
  },
  {
    picture_id: 502,
    position: 1,
    is_leader: false,
    width: 4000,
    height: 3000,
    megapixels: 12,
    size_bytes: 3_000_000,
    format: "JPEG",
    score: 0,
    tag_count: 0,
    thumbnail_version: "v502",
  },
  {
    picture_id: 503,
    position: 2,
    is_leader: false,
    width: 1000,
    height: 750,
    megapixels: 0.75,
    size_bytes: 2_000_000,
    format: "JPEG",
    score: 1,
    tag_count: 2,
    thumbnail_version: "v503",
  },
  {
    picture_id: 505,
    position: 3,
    is_leader: false,
    width: 1000,
    height: 750,
    megapixels: 0.75,
    size_bytes: 1_000_000,
    format: "JPEG",
    score: 0,
    tag_count: 0,
    thumbnail_version: "v505",
  },
];

/** The two members of stack 12 this group named, as queue candidates. */
const MATCHED_12 = [
  {
    picture_id: 503,
    stack_id: 12,
    width: 1000,
    height: 750,
    megapixels: 0.75,
    size_bytes: 2_000_000,
    format: "JPEG",
    is_raw: false,
    created_at: "2026-05-01T09:00:00",
    score: 1,
    tag_count: 2,
    thumbnail_version: "v503",
    file_path: null,
    reference_folder_id: null,
  },
  {
    picture_id: 505,
    stack_id: 12,
    width: 1000,
    height: 750,
    megapixels: 0.75,
    size_bytes: 1_000_000,
    format: "JPEG",
    is_raw: false,
    created_at: "2026-05-01T09:00:01",
    score: 0,
    tag_count: 0,
    thumbnail_version: "v505",
    file_path: null,
    reference_folder_id: null,
  },
];

/** The loose picture the stack is being compared against. */
const LOOSE_9 = {
  picture_id: 9,
  stack_id: null,
  width: 4000,
  height: 3000,
  megapixels: 12,
  size_bytes: 8_000_000,
  format: "JPEG",
  is_raw: false,
  created_at: "2026-05-02T10:00:00",
  score: 3,
  tag_count: 5,
  thumbnail_version: "v9",
  file_path: null,
  reference_folder_id: null,
};

/** Three candidates, two units: a deck of four and one loose picture. */
const DECK_GROUP = {
  signature: "deck-group",
  tier: "near",
  confidence: 0.88,
  member_count: 3,
  cover_picture_id: 501,
  why: [],
  candidates: [...MATCHED_12, LOOSE_9],
  stacks: {
    12: {
      stack_id: 12,
      member_count: 4,
      leader_picture_id: 501,
      leader_thumbnail_version: "v501",
      matched_picture_ids: [503, 505],
      stackable: true,
      blocked_by_sets: [],
    },
  },
};

/** A second deck, so "one expansion at a time" has something to fight over. */
const TWO_DECK_GROUP = {
  ...DECK_GROUP,
  signature: "two-deck-group",
  candidates: [
    ...MATCHED_12,
    { ...LOOSE_9, picture_id: 700, stack_id: 20 },
    { ...LOOSE_9, picture_id: 701, stack_id: 20 },
  ],
  stacks: {
    ...DECK_GROUP.stacks,
    20: {
      stack_id: 20,
      member_count: 2,
      leader_picture_id: 700,
      leader_thumbnail_version: "v700",
      matched_picture_ids: [700, 701],
      stackable: true,
      blocked_by_sets: [],
    },
  },
};

/** The leader page (one member) and the whole stack, as the API serves them. */
function stackMemberResponse(stackId, { limit } = {}) {
  const members = stackId === 12 ? STACK_12_MEMBERS : [];
  if (limit === 1) {
    return Promise.resolve({
      stack_id: stackId,
      member_count: members.length,
      members: members.slice(0, 1),
      next_offset: members.length > 1 ? 1 : null,
    });
  }
  return Promise.resolve({
    stack_id: stackId,
    member_count: members.length,
    members,
    next_offset: null,
  });
}

/** Mount over a group with a deck, with the leader's row already fetched. */
async function mountDeck(props = {}) {
  const wrapper = mount(DedupCompareDialog, {
    ...globalOpts,
    props: { open: true, group: DECK_GROUP, coverId: 501, ...props },
  });
  await flushPromises();
  return wrapper;
}

/** One card's cell whose label reads `label`, or null. */
function cellOf(card, label) {
  return (
    card
      .findAll(".dc-cell")
      .find((cell) => cell.find(".dc-label").text() === label) ?? null
  );
}

describe("DedupCompareDialog: one card per unit", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(stackMemberResponse);
  });

  it("collapses a stack's candidates into one card", async () => {
    // Three candidates, two units. A card per candidate would offer a
    // comparison between two things no verdict can move apart.
    const cards = (await mountDeck()).findAll(".dc-card");
    expect(cards).toHaveLength(2);
    expect(cards[0].find(".dc-index").text()).toBe("1");
    expect(cards[1].find(".dc-index").text()).toBe("2");
  });

  it("compares every original candidate individually when collapsing is off", async () => {
    const wrapper = await mountDeck({ collapseStacks: false, readOnly: true });
    expect(wrapper.findAll(".dc-card")).toHaveLength(3);
    expect(wrapper.findAll(".dc-expand")).toHaveLength(0);
    expect(listStackMembersMock).not.toHaveBeenCalled();
  });

  it("shows the LEADER's numbers on the deck, labelled as the leader's", async () => {
    // The leader is not one of the group's candidates, which is the common
    // case, so its row is fetched. An aggregate, or the matched member's
    // numbers, would both answer a question nobody asked and would move the
    // best mark onto the wrong file.
    const wrapper = await mountDeck();
    expect(listStackMembersMock).toHaveBeenCalledWith(12, { limit: 1 });

    const deck = wrapper.findAll(".dc-card")[0];
    expect(cellOf(deck, "Leader").find(".dc-val").text()).toBe("#501");
    expect(cellOf(deck, "Resolution").find(".dc-val").text()).toBe(
      "6000 x 4000",
    );
    expect(cellOf(deck, "File").find(".dc-val").text()).toBe("12.0 MB, JPEG");
    // 24 MP beats the loose copy's 12, so the deck wins the column.
    expect(cellOf(deck, "Resolution").find(".dc-val").classes()).toContain(
      "dc-val--best",
    );
    const loose = wrapper.findAll(".dc-card")[1];
    expect(cellOf(loose, "Resolution").find(".dc-val").classes()).not.toContain(
      "dc-val--best",
    );
    expect(deck.find(".dc-flag--leader").exists()).toBe(true);
  });

  it("says nothing rather than a confident zero before the leader lands", async () => {
    // A dash reads as "there is nothing here"; `none` and `0.0 MB` would be
    // claims about a file whose row has not been read.
    listStackMembersMock.mockImplementation(() => new Promise(() => {}));
    const wrapper = mount(DedupCompareDialog, {
      ...globalOpts,
      props: { open: true, group: DECK_GROUP, coverId: 501 },
    });
    const deck = wrapper.findAll(".dc-card")[0];
    expect(cellOf(deck, "File").find(".dc-val").text()).toBe("–");
    expect(cellOf(deck, "Metadata").find(".dc-val").text()).toBe("–");
  });

  it("names the verdict's outcome in the footer, in units", async () => {
    // `Stack 3` would count pictures the verdict does not move independently
    // and would contradict the row the user pressed C on.
    const wrapper = await mountDeck();
    expect(footerButtons(wrapper)[2].text()).toContain("Add 1 to stack of 4");
  });

  it("keeps the cover and exclusion gestures unit-level", async () => {
    const wrapper = await mountDeck({ coverId: 9 });
    const cards = wrapper.findAll(".dc-card");
    // The deck answers to its leader, which is not a group candidate at all.
    expect(
      cards.map((c) => c.find(".dc-pick").attributes("aria-pressed")),
    ).toEqual(["false", "true"]);

    cards[0].find(".dc-pick").trigger("click");
    expect(wrapper.emitted("set-cover")).toEqual([[501]]);
    // The exclusion travels as a matched member: the store resolves it to the
    // whole deck, and only a matched member is certainly in the group.
    cards[0].trigger("contextmenu");
    expect(wrapper.emitted("toggle-excluded")).toEqual([[503]]);
  });
});

describe("DedupCompareDialog: the Contains row", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(stackMemberResponse);
  });

  it("states what each card stands for, on every card once one is a deck", async () => {
    // All-or-none, like Location and Smart score: the meta grid is what the
    // picture above it gives its leftover height to, so a row on some cards
    // only would leave the pictures out of register with each other.
    const cards = (await mountDeck()).findAll(".dc-card");
    expect(cards.map((card) => cellOf(card, "Contains") !== null)).toEqual([
      true,
      true,
    ]);
    expect(cellOf(cards[0], "Contains").text()).toContain("4 pictures");
    expect(cellOf(cards[1], "Contains").text()).toBe("Contains1 picture");
  });

  it("drops the row entirely when every card is one picture", () => {
    // `1 picture` on three cards is a column of noise: the File column is
    // already the whole truth there.
    for (const card of mountDialog().findAll(".dc-card")) {
      expect(cellOf(card, "Contains")).toBeNull();
    }
  });

  it("adds the deck's footprint once the whole member list is known", async () => {
    // The payload carries no total, and summing the one page we hold would
    // state a stack's size from a fraction of it.
    const wrapper = await mountDeck();
    const contains = () =>
      cellOf(wrapper.findAll(".dc-card")[0], "Contains").text();
    expect(contains()).not.toContain("MB");

    await wrapper.findAll(".dc-card")[0].find(".dc-expand").trigger("click");
    await flushPromises();
    expect(contains()).toContain("18.0 MB");
  });
});

describe("DedupCompareDialog: the expansion", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(stackMemberResponse);
  });

  it("opens the members BELOW the strip, never inside a card", async () => {
    // A band inside a card grows that card, and the registration between the
    // pictures is the one thing this whole surface exists to hold.
    const wrapper = await mountDeck();
    expect(wrapper.find('[data-testid="dedup-expansion"]').exists()).toBe(
      false,
    );

    await wrapper.findAll(".dc-card")[0].find(".dc-expand").trigger("click");
    await flushPromises();
    const band = wrapper.find('[data-testid="dedup-expansion"]');
    expect(band.exists()).toBe(true);

    const strip = wrapper.find(".dc-strip").element;
    expect(strip.contains(band.element)).toBe(false);
    expect(
      strip.compareDocumentPosition(band.element) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // All four members, not just the two the group named.
    expect(band.findAll('[data-testid="stack-member"]')).toHaveLength(4);
  });

  it("keeps exactly one expansion open", async () => {
    // Two bands push the cards off the screen.
    const wrapper = mount(DedupCompareDialog, {
      ...globalOpts,
      props: { open: true, group: TWO_DECK_GROUP, coverId: 501 },
    });
    await flushPromises();
    const expanders = () => wrapper.findAll(".dc-expand");
    await expanders()[0].trigger("click");
    await flushPromises();
    expect(wrapper.findAll('[data-testid="dedup-expansion"]')).toHaveLength(1);
    expect(expanders()[0].attributes("aria-expanded")).toBe("true");

    await expanders()[1].trigger("click");
    await flushPromises();
    expect(wrapper.findAll('[data-testid="dedup-expansion"]')).toHaveLength(1);
    expect(expanders()[0].attributes("aria-expanded")).toBe("false");
    expect(expanders()[1].attributes("aria-expanded")).toBe("true");
  });

  it("reports a failed member read and offers the retry", async () => {
    listStackMembersMock.mockRejectedValue(new Error("boom"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const wrapper = mount(DedupCompareDialog, {
      ...globalOpts,
      props: { open: true, group: DECK_GROUP, coverId: 501 },
    });
    await flushPromises();
    await wrapper.findAll(".dc-card")[0].find(".dc-expand").trigger("click");
    await flushPromises();

    const band = wrapper.find('[data-testid="dedup-expansion"]');
    expect(band.find(".dc-expansion-state--error").exists()).toBe(true);
    expect(band.text()).toContain("Try again");
    expect(warn).toHaveBeenCalled();

    listStackMembersMock.mockImplementation(stackMemberResponse);
    await band.find("button").trigger("click");
    await flushPromises();
    expect(
      wrapper.findAll('[data-testid="stack-member"]').length,
    ).toBeGreaterThan(0);
    warn.mockRestore();
  });

  it("makes promoting a member say what it costs before it happens", async () => {
    // Promotion re-covers that stack across the library, which is why it was
    // withdrawn from the queue row. Here it survives, but never as a bare
    // click: the confirmation carries the consequence in its own text.
    const wrapper = await mountDeck();
    await wrapper.findAll(".dc-card")[0].find(".dc-expand").trigger("click");
    await flushPromises();

    await wrapper.findAll('[data-testid="stack-member"]')[1].trigger("click");
    expect(wrapper.emitted("set-cover")).toBeUndefined();
    const promote = wrapper.find(".dc-promote");
    expect(promote.exists()).toBe(true);
    expect(promote.text()).toContain("#502");
    expect(promote.text()).toContain("everywhere in your library");

    await promote
      .findAllComponents(AppButton)[1]
      .find("button")
      .trigger("click");
    expect(wrapper.emitted("set-cover")).toEqual([[502]]);
    expect(wrapper.find(".dc-promote").exists()).toBe(false);
  });

  it("offers no Unstack in Compare", async () => {
    // The dialog has no unstack pathway to honour, and a dead control is
    // worse than an absent one.
    const wrapper = await mountDeck();
    await wrapper.findAll(".dc-card")[0].find(".dc-expand").trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="stack-unstack"]').exists()).toBe(false);
  });
});

describe("DedupCompareDialog: In stack is per unit", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(stackMemberResponse);
  });

  it("reads All N for a deck and None when it is out", async () => {
    // A stack goes into the verdict entire or not at all, so `Yes`/`No` would
    // under-state what the checkbox moves.
    const inStack = (wrapper, index) =>
      cellOf(wrapper.findAll(".dc-card")[index], "In stack").find(".dc-val");

    const wrapper = await mountDeck();
    expect(inStack(wrapper, 0).text()).toBe("All 4");
    expect(inStack(wrapper, 1).text()).toBe("Yes");

    const excluded = await mountDeck({ excludedIds: [503, 505] });
    expect(inStack(excluded, 0).text()).toBe("None");
    expect(
      cellOf(excluded.findAll(".dc-card")[0], "In stack")
        .find(".dc-toggle")
        .attributes("aria-pressed"),
    ).toBe("false");
  });

  it("toggles the whole deck from the checkbox", async () => {
    const wrapper = await mountDeck();
    await cellOf(wrapper.findAll(".dc-card")[0], "In stack")
      .find(".dc-toggle")
      .trigger("click");
    expect(wrapper.emitted("toggle-excluded")).toEqual([[503]]);
    expect(wrapper.emitted("set-cover")).toBeUndefined();
  });
});

describe("DedupCompareDialog: the zoom flips pictures, not units", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(stackMemberResponse);
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  /** The `#id` the zoom's meta line is currently naming. */
  function zoomedId() {
    const text = document.querySelector(
      '[data-testid="dedup-zoom"] .dc-zv-meta',
    ).textContent;
    return text.split(" ")[0];
  }

  function flipCount() {
    return document.querySelectorAll(
      '[data-testid="dedup-zoom"] .dc-zv-flip button',
    ).length;
  }

  it("walks each unit's leader then its known members, in order", async () => {
    // The sequence is the disclosure: a group that named one member of a stack
    // can still be checked against its siblings at 100%.
    const wrapper = await mountDeck();
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    expect(flipCount()).toBe(4);

    const order = [];
    for (let i = 0; i < 4; i += 1) {
      wrapper.vm.zoomTo(i);
      await wrapper.vm.$nextTick();
      order.push(zoomedId());
    }
    expect(order).toEqual(["#501", "#503", "#505", "#9"]);
    wrapper.vm.closeZoom();
    wrapper.unmount();
  });

  it("grows to the whole stack once its members are fetched", async () => {
    const wrapper = await mountDeck();
    await wrapper.findAll(".dc-card")[0].find(".dc-expand").trigger("click");
    await flushPromises();

    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    expect(flipCount()).toBe(5);
    const order = [];
    for (let i = 0; i < 5; i += 1) {
      wrapper.vm.zoomTo(i);
      await wrapper.vm.$nextTick();
      order.push(zoomedId());
    }
    expect(order).toEqual(["#501", "#502", "#503", "#505", "#9"]);
    wrapper.vm.closeZoom();
    wrapper.unmount();
  });

  it("opens a card's zoom on that unit's leader", async () => {
    const wrapper = await mountDeck();
    await wrapper.findAll(".dc-zoom")[1].trigger("click");
    expect(zoomedId()).toBe("#9");
    wrapper.vm.closeZoom();
    wrapper.unmount();
  });

  it("keeps the cover pick unit-level from inside the zoom", async () => {
    // Clicking a deck member at 100% must not silently re-cover that stack in
    // the library; that gesture belongs to the expansion, where it is
    // confirmed against its consequence.
    const wrapper = await mountDeck();
    wrapper.vm.openZoom(1);
    await wrapper.vm.$nextTick();
    expect(zoomedId()).toBe("#503");
    document.querySelector('[data-testid="dedup-zoom"] .dc-zv-img').click();
    expect(wrapper.emitted("set-cover")).toEqual([[501]]);
    wrapper.vm.closeZoom();
    wrapper.unmount();
  });
});

// ── Mixed mode ─────────────────────────────────────────────────────────────
// The Mixed stacks page opens the SAME dialog: one strip, one zoom, one footer.
// What changes is what a card means (one member of one live stack, not a unit
// of a proposed one) and what its press does (mark a stranger, not choose a
// cover). These tests pin that difference, and pin that the `group` path above
// is untouched by it; every test before this point runs on the default mode.

/** One `MixedStackModel` row: a burst of three plus one stray. */
const MIXED_STACK = {
  stack_id: 42,
  threshold: 0.9,
  member_count: 4,
  member_ids: [7, 8, 9, 11],
  membership_fingerprint: "fp1",
  component_count: 2,
  component_sizes: [3, 1],
  components: [[7, 8, 9], [11]],
  largest_component_size: 3,
  stranded_picture_ids: [11],
  unhashed_picture_ids: [],
  weakest_edge: 0.94,
  // `strongest_edge` is thresholded and therefore null for the stray;
  // `nearest_edge` is the unconditional measurement, and the stray's is 0.89:
  // 7 bits of 64 where the 0.90 cut is 6. The Match column shows the second.
  member_edges: [
    {
      picture_id: 7,
      strongest_edge: 0.97,
      closest_picture_id: 8,
      nearest_edge: 0.97,
      nearest_picture_id: 8,
    },
    {
      picture_id: 8,
      strongest_edge: 0.97,
      closest_picture_id: 7,
      nearest_edge: 0.97,
      nearest_picture_id: 7,
    },
    {
      picture_id: 9,
      strongest_edge: 0.94,
      closest_picture_id: 8,
      nearest_edge: 0.94,
      nearest_picture_id: 8,
    },
    {
      picture_id: 11,
      strongest_edge: null,
      closest_picture_id: null,
      nearest_edge: 0.89,
      nearest_picture_id: 9,
    },
  ],
  why: [
    { text: "1 picture is only 89% like the rest", against: true },
    { text: "Weakest match 94%", against: false },
  ],
  suggested_action: "split",
  kept: false,
  leader_picture_id: 7,
  leader_thumbnail_version: "v7",
  stackable: true,
  blocked_by_sets: [],
};

/** The dialog as the Mixed stacks page mounts it. */
function mountMixed(props = {}) {
  return mount(DedupCompareDialog, {
    ...globalOpts,
    props: {
      open: true,
      mode: "mixed",
      mixedStack: MIXED_STACK,
      markedIds: [11],
      primaryLabel: "Split off 1",
      primaryIcon: "call-split",
      ...props,
    },
  });
}

/** Mount, then let the one member read land, which is the steady state. */
async function mountMixedLoaded(props = {}) {
  const wrapper = mountMixed(props);
  await flushPromises();
  return wrapper;
}

/**
 * The picture rows of stack 42, as `GET /dedup/stacks/{id}/members` serves them.
 *
 * Neither the mixed row nor the queue group carries these: the row names its
 * members and measures their edges, and says nothing about what the files ARE.
 */
const MIXED_MEMBER_ROWS = [
  {
    picture_id: 7,
    position: 0,
    is_leader: true,
    width: 6000,
    height: 4000,
    megapixels: 24,
    size_bytes: 12_000_000,
    format: "JPEG",
    is_raw: false,
    created_at: "2026-04-01T08:00:00",
    score: 5,
    tag_count: 9,
    thumbnail_version: "v7",
    file_path: null,
    reference_folder_id: null,
  },
  {
    picture_id: 8,
    position: 1,
    width: 4000,
    height: 3000,
    megapixels: 12,
    size_bytes: 6_000_000,
    format: "JPEG",
    score: 2,
    tag_count: 1,
    thumbnail_version: "v8",
  },
  {
    picture_id: 9,
    position: 2,
    width: 2000,
    height: 1500,
    megapixels: 3,
    size_bytes: 2_000_000,
    format: "JPEG",
    score: 0,
    tag_count: 0,
    thumbnail_version: "v9",
  },
  {
    picture_id: 11,
    position: 3,
    width: 1000,
    height: 750,
    megapixels: 0.75,
    size_bytes: 1_000_000,
    format: "JPEG",
    score: 1,
    tag_count: 4,
    thumbnail_version: "v11",
  },
];

/** The whole-stack read the mixed dialog issues on open. */
function mixedMemberResponse(stackId) {
  const members = stackId === 42 ? MIXED_MEMBER_ROWS : [];
  return Promise.resolve({
    stack_id: stackId,
    member_count: members.length,
    members,
    next_offset: null,
  });
}

describe("DedupCompareDialog: mixed mode renders members, not units", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(mixedMemberResponse);
  });

  it("draws one card per member, in stack order", () => {
    const wrapper = mountMixed();
    const cards = wrapper.findAll(".dc-card");
    expect(cards).toHaveLength(4);
    expect(
      cards.map((card) => cellOf(card, "ID").find(".dc-val").text()),
    ).toEqual(["#7", "#8", "#9", "#11"]);
  });

  it("reads the whole stack once, and never a second time", async () => {
    // Every member is a card, so the deck's leader-only read is the wrong shape
    // here: one request for the lot, on a surface the user opened deliberately.
    // The never-render-whole rule is about the queue's rows, not about this.
    const wrapper = await mountMixedLoaded();
    expect(listStackMembersMock).toHaveBeenCalledTimes(1);
    expect(listStackMembersMock).toHaveBeenCalledWith(42, { limit: 200 });

    // Marking is the gesture this page is made of, and it re-renders every
    // card. It must not re-read the stack each time.
    await wrapper.setProps({ markedIds: [11, 9] });
    await flushPromises();
    expect(listStackMembersMock).toHaveBeenCalledTimes(1);
  });

  it("names the stack rather than the group in the header", () => {
    const wrapper = mountMixed();
    // The stub declares no props, so the title arrives as a fallthrough attr.
    expect(
      wrapper.findComponent({ name: "AppDialog" }).attributes("title"),
    ).toBe("Compare stack");
    // The row's own reason, not a confidence percentage: this stack exists, so
    // there is no proposal to be confident about.
    expect(wrapper.find(".dc-confidence").text()).toBe(
      "1 picture doesn't match the rest",
    );
  });

  it("tones the evidence as fact, from the row's own pills", () => {
    // A run of red chips over a stack the user already made reads as an
    // accusation; the pills here report what was measured.
    const pills = mountMixed().findAll(".why-pill");
    expect(pills).toHaveLength(2);
    for (const pill of pills) {
      expect(pill.classes()).toContain("why-pill--fact");
    }
  });
});

describe("DedupCompareDialog: mixed mode marks strangers", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(mixedMemberResponse);
  });

  it("marks a member on the card's primary click, and on right-click too", () => {
    // Click and context-menu are ONE gesture here, exactly as they are on the
    // queue row: a mixed stack has no cover to choose, so there is only one
    // thing a card can mean.
    const wrapper = mountMixed();
    const cards = wrapper.findAll(".dc-card");

    cards[1].find(".dc-pick").trigger("click");
    expect(wrapper.emitted("toggle-mark")).toEqual([[8]]);
    expect(wrapper.emitted("set-cover")).toBeUndefined();

    cards[2].trigger("contextmenu");
    expect(wrapper.emitted("toggle-mark")).toEqual([[8], [9]]);
    expect(wrapper.emitted("toggle-excluded")).toBeUndefined();
  });

  it("presses the marked card, and only that one", () => {
    // aria-pressed is the only signal of the mark a screen-reader user gets.
    const wrapper = mountMixed();
    expect(
      wrapper
        .findAll(".dc-card")
        .map((card) => card.find(".dc-pick").attributes("aria-pressed")),
    ).toEqual(["false", "false", "false", "true"]);
    expect(wrapper.findAll(".dc-card--split")).toHaveLength(1);
    expect(wrapper.findAll(".dc-flag--split")).toHaveLength(1);
    expect(wrapper.findAll(".dc-card")[3].find(".dc-flag--split").text()).toBe(
      "Split off",
    );
  });

  it("reads Stranger for a marked member and Yes for the rest", () => {
    // `Stranger`, not `No`: the value says what the picture IS relative to its
    // siblings, which is the fact the primary button acts on.
    const wrapper = mountMixed();
    const inStack = (card) => cellOf(card, "In the stack").find(".dc-val");
    expect(wrapper.findAll(".dc-card").map((c) => inStack(c).text())).toEqual([
      "Yes",
      "Yes",
      "Yes",
      "Stranger",
    ]);
    // The checkbox reads the same fact, and toggles the same mark.
    const stranger = wrapper.findAll(".dc-card")[3];
    expect(
      cellOf(stranger, "In the stack")
        .find(".dc-toggle")
        .attributes("aria-pressed"),
    ).toBe("false");
    cellOf(stranger, "In the stack").find(".dc-toggle").trigger("click");
    expect(wrapper.emitted("toggle-mark")).toEqual([[11]]);
  });

  it("marks nothing in a read-only session", () => {
    // The verdict footer goes, so the gestures that feed it must go with it.
    const wrapper = mountMixed({ readOnly: true });
    wrapper.findAll(".dc-card")[1].find(".dc-pick").trigger("click");
    wrapper.findAll(".dc-card")[1].trigger("contextmenu");
    expect(wrapper.emitted("toggle-mark")).toBeUndefined();

    const buttons = footerButtons(wrapper);
    expect(buttons).toHaveLength(1);
    expect(buttons[0].text()).toContain("Close");
    expect(wrapper.find(".dc-hint").exists()).toBe(false);
  });
});

describe("DedupCompareDialog: mixed mode asks a different question", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(mixedMemberResponse);
  });

  it("marks no column winner anywhere", async () => {
    // The best-value chip answers "which is the better file"; this page asks
    // "which of these does not belong". A green-ish winner beside a stranger's
    // warning border is a confident answer to the wrong question. Asserted with
    // the rows LOADED, so there are genuine per-column maxima to suppress:
    // member #7 wins resolution, file size, score and tags outright.
    const wrapper = await mountMixedLoaded();
    expect(wrapper.find(".dc-val").exists()).toBe(true);
    expect(wrapper.findAll(".dc-val--best")).toHaveLength(0);
  });

  it("offers no Contains row and no expansion band", () => {
    // Every card is one picture, so there is nothing folded away to disclose.
    const wrapper = mountMixed();
    for (const card of wrapper.findAll(".dc-card")) {
      expect(cellOf(card, "Contains")).toBeNull();
    }
    expect(wrapper.findAll(".dc-expand")).toHaveLength(0);
    expect(wrapper.find('[data-testid="dedup-expansion"]').exists()).toBe(
      false,
    );
  });

  it("shows each member's closest match, the stranger's included", () => {
    // The reported bug. The stray has no edge that survives 0.90, but it is 89%
    // like picture 9, and the column said "–" and the row said it matched
    // nothing. The dash is reserved for an absent MEASUREMENT, never for a
    // measurement that fell outside the user's own threshold.
    const cards = mountMixed().findAll(".dc-card");
    const match = (card) => cellOf(card, "Match").find(".dc-val");
    expect(cards.map((card) => match(card).text())).toEqual([
      "97%",
      "97%",
      "94%",
      "89%",
    ]);
    expect(match(cards[0]).attributes("title")).toContain(
      "Closest match 97%, to picture #8",
    );
    const strangerTitle = match(cards[3]).attributes("title");
    expect(strangerTitle).toContain("Closest match 89%, to picture #9");
    expect(strangerTitle).toContain("below your threshold");
    expect(strangerTitle).not.toContain("matches nothing");
  });

  it("keeps the dash for a member with nothing to compare against", () => {
    // The one surviving absence: no sibling has been analysed, so there is no
    // number, as opposed to a number the threshold rejected.
    const alone = {
      ...MIXED_STACK,
      member_edges: MIXED_STACK.member_edges.map((edge) =>
        edge.picture_id === 11
          ? { ...edge, nearest_edge: null, nearest_picture_id: null }
          : edge,
      ),
    };
    const cards = mountMixed({ mixedStack: alone }).findAll(".dc-card");
    const cell = cellOf(cards[3], "Match").find(".dc-val");
    expect(cell.text()).toBe("–");
    expect(cell.attributes("title")).toContain("nothing to compare this");
  });

  it("chips the gap on the stranger's Match value and nowhere else", () => {
    const cards = mountMixed().findAll(".dc-card");
    expect(
      cards.map((card) =>
        cellOf(card, "Match").find(".dc-val").classes().includes("dc-val--gap"),
      ),
    ).toEqual([false, false, false, true]);
  });

  it("says a not-yet-analysed member apart from a distant one", () => {
    // Not comparable yet is NOT a mistake, and reporting it as one is the false
    // positive this feature cannot afford. An unhashed member also carries no
    // measurement, so this must win over the "closest match" sentence.
    const pending = {
      ...MIXED_STACK,
      unhashed_picture_ids: [11],
      stranded_picture_ids: [],
      member_edges: MIXED_STACK.member_edges.map((edge) =>
        edge.picture_id === 11
          ? { ...edge, nearest_edge: null, nearest_picture_id: null }
          : edge,
      ),
    };
    const cards = mountMixed({ mixedStack: pending, markedIds: [] }).findAll(
      ".dc-card",
    );
    expect(
      cellOf(cards[3], "Match").find(".dc-val").attributes("title"),
    ).toContain("has not been analysed yet");
  });
});

describe("DedupCompareDialog: the mixed member read", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(mixedMemberResponse);
  });

  it("fills the metric cells from the fetched rows", async () => {
    // Neither the mixed row nor the queue group carries a picture's numbers, so
    // without this read every metric cell is a dash and the comparison compares
    // nothing.
    const wrapper = await mountMixedLoaded();
    const leader = wrapper.findAll(".dc-card")[0];
    expect(cellOf(leader, "Resolution").find(".dc-val").text()).toBe(
      "6000 x 4000",
    );
    expect(cellOf(leader, "File").find(".dc-val").text()).toBe("12.0 MB, JPEG");
    expect(cellOf(leader, "Metadata").find(".dc-val").text()).toBe("9 tags");

    const stranger = wrapper.findAll(".dc-card")[3];
    expect(cellOf(stranger, "Resolution").find(".dc-val").text()).toBe(
      "1000 x 750",
    );
  });

  it("says nothing rather than a confident zero before the rows land", async () => {
    listStackMembersMock.mockImplementation(() => new Promise(() => {}));
    const card = mountMixed().findAll(".dc-card")[0];
    expect(cellOf(card, "File").find(".dc-val").text()).toBe("–");
    expect(cellOf(card, "Metadata").find(".dc-val").text()).toBe("–");
    // The row's own facts never depend on the read.
    expect(cellOf(card, "Match").find(".dc-val").text()).toBe("97%");
  });

  it("serves the down-scaled original once the rows land", async () => {
    // This is the point of the read. Compare exists to judge fine detail, and a
    // zoom whose Actual pixels is 1:1 on a grid thumbnail is not that zoom.
    // The same mount either side of the response, deliberately: a prop swap
    // would not re-read (the membership has not changed) and would prove
    // nothing about the transition.
    let land;
    listStackMembersMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          land = () => resolve(mixedMemberResponse(42));
        }),
    );
    const wrapper = mountMixed();
    expect(wrapper.findAll(".dc-thumb-img")[0].attributes("src")).toContain(
      "/pictures/thumbnails/7",
    );

    land();
    await flushPromises();
    const src = wrapper.findAll(".dc-thumb-img")[0].attributes("src");
    expect(src).toContain("/pictures/7.jpeg");
    expect(src).not.toContain("thumbnails");

    // And the zoom, which is the reason this matters, serves it too.
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    expect(
      document
        .querySelector('[data-testid="dedup-zoom"] img')
        .getAttribute("src"),
    ).toContain("/pictures/7.jpeg");
    wrapper.vm.closeZoom();
    await wrapper.vm.$nextTick();
    document.body.innerHTML = "";
    wrapper.unmount();
  });

  it("reports the failure without overstating what survives it", async () => {
    // The read supplies DETAIL. The ids, the evidence and the gestures come off
    // the row payload, so a failure must not present a working surface as a
    // broken one; but the line must not claim more than the next test proves,
    // and the zoom really is degraded (it opens on thumbnails).
    listStackMembersMock.mockRejectedValue(new Error("boom"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const wrapper = await mountMixedLoaded();

    const note = wrapper.find('[data-testid="dedup-mixed-error"]');
    expect(note.exists()).toBe(true);
    expect(note.text()).toContain("full-size previews are missing");
    expect(note.text()).toContain("Marking and splitting still work");
    expect(warn).toHaveBeenCalled();
    expect(warn.mock.calls[0][0]).toContain("stack 42");

    // Everything the row payload alone can draw is still drawn.
    expect(wrapper.findAll(".dc-card")).toHaveLength(4);
    expect(
      cellOf(wrapper.findAll(".dc-card")[0], "Match").find(".dc-val").text(),
    ).toBe("97%");
    expect(wrapper.findAll(".why-pill")).toHaveLength(2);

    // And the retry is a real way out of it.
    listStackMembersMock.mockImplementation(mixedMemberResponse);
    await note.findComponent(AppButton).find("button").trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="dedup-mixed-error"]').exists()).toBe(
      false,
    );
    expect(
      cellOf(wrapper.findAll(".dc-card")[0], "Resolution")
        .find(".dc-val")
        .text(),
    ).toBe("6000 x 4000");
    warn.mockRestore();
  });

  it("really does still mark and split when the read failed", async () => {
    // The claim in that line, verified rather than asserted. The marks act on
    // picture ids from the ROW, never from the fetched member rows, so nothing
    // about the gesture depends on the read; but "nothing depends on it" is
    // exactly the kind of thing that is true right up until it is not.
    listStackMembersMock.mockRejectedValue(new Error("boom"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const wrapper = await mountMixedLoaded();
    const cards = () => wrapper.findAll(".dc-card");

    // Every gesture that marks: the card, its right-click, the checkbox.
    await cards()[1].find(".dc-pick").trigger("click");
    await cards()[2].trigger("contextmenu");
    await cellOf(cards()[0], "In the stack")
      .find(".dc-toggle")
      .trigger("click");
    expect(wrapper.emitted("toggle-mark")).toEqual([[8], [9], [7]]);

    // And the mark ROUND-TRIPS: the page hands the new list back and the card
    // adopts every part of the stranger treatment.
    await wrapper.setProps({ markedIds: [11, 8] });
    expect(cards()[1].find(".dc-pick").attributes("aria-pressed")).toBe("true");
    expect(cards()[1].classes()).toContain("dc-card--split");
    expect(cards()[1].find(".dc-flag--split").text()).toBe("Split off");
    expect(cellOf(cards()[1], "In the stack").find(".dc-val").text()).toBe(
      "Stranger",
    );

    // Both verdicts are live, and neither is disabled by the failed read.
    // Found by LABEL, not by index: the failure line carries its own Try again
    // button ahead of the footer, so the positional helper is off by one here.
    const keep = buttonLabelled(wrapper, "Keep");
    const primary = buttonLabelled(wrapper, "Split off 1");
    expect(keep.find("button").attributes("disabled")).toBeUndefined();
    expect(primary.find("button").attributes("disabled")).toBeUndefined();
    await keep.find("button").trigger("click");
    await primary.find("button").trigger("click");
    expect(wrapper.emitted("keep")).toHaveLength(1);
    expect(wrapper.emitted("resolve")).toHaveLength(1);

    // The zoom still opens, on thumbnails, and its click still marks.
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    const zoom = document.querySelector('[data-testid="dedup-zoom"]');
    expect(zoom.querySelector("img").getAttribute("src")).toContain(
      "/pictures/thumbnails/7",
    );
    zoom.querySelector(".dc-zv-img").click();
    expect(wrapper.emitted("toggle-mark")).toEqual([[8], [9], [7], [7]]);
    wrapper.vm.closeZoom();
    await wrapper.vm.$nextTick();
    document.body.innerHTML = "";
    warn.mockRestore();
    wrapper.unmount();
  });

  it("refetches for the next stack and never paints the previous one's rows", async () => {
    // The dialog flips row in place as the cursor moves, so a response that
    // outlived its stack would put one stack's numbers under another's photos.
    let releaseFirst;
    listStackMembersMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseFirst = () => resolve(mixedMemberResponse(42));
        }),
    );
    const wrapper = mountMixed();
    expect(listStackMembersMock).toHaveBeenCalledWith(42, { limit: 200 });

    const next = {
      ...MIXED_STACK,
      stack_id: 43,
      member_ids: [70, 71],
      member_count: 2,
      stranded_picture_ids: [],
      member_edges: [
        {
          picture_id: 70,
          strongest_edge: 0.8,
          closest_picture_id: 71,
          nearest_edge: 0.8,
          nearest_picture_id: 71,
        },
        {
          picture_id: 71,
          strongest_edge: 0.8,
          closest_picture_id: 70,
          nearest_edge: 0.8,
          nearest_picture_id: 70,
        },
      ],
    };
    listStackMembersMock.mockImplementation((stackId) =>
      Promise.resolve({
        stack_id: stackId,
        member_count: 2,
        members: [
          { picture_id: 70, width: 100, height: 100, format: "PNG" },
          { picture_id: 71, width: 200, height: 200, format: "PNG" },
        ],
        next_offset: null,
      }),
    );
    await wrapper.setProps({ mixedStack: next, markedIds: [] });
    await flushPromises();
    expect(listStackMembersMock).toHaveBeenCalledWith(43, { limit: 200 });

    const cards = () => wrapper.findAll(".dc-card");
    expect(cards()).toHaveLength(2);
    expect(cellOf(cards()[0], "Resolution").find(".dc-val").text()).toBe(
      "100 x 100",
    );

    // Stack 42's answer lands late. It must be dropped, not painted.
    releaseFirst();
    await flushPromises();
    expect(cards()).toHaveLength(2);
    expect(cellOf(cards()[0], "ID").find(".dc-val").text()).toBe("#70");
    expect(cellOf(cards()[0], "Resolution").find(".dc-val").text()).toBe(
      "100 x 100",
    );
    wrapper.unmount();
  });

  it("re-reads when the stack's membership changes under the same id", async () => {
    // The reset is keyed on `membership_fingerprint`, not on `stack_id`, and
    // the case that forces it is a member being ADDED: that card has no cached
    // row, so keyed on the id it would show the en dash for the rest of the
    // dialog's life with nothing left to trigger a second read.
    const wrapper = await mountMixedLoaded();
    expect(listStackMembersMock).toHaveBeenCalledTimes(1);

    const grown = {
      ...MIXED_STACK,
      membership_fingerprint: "fp2",
      member_count: 5,
      member_ids: [7, 8, 9, 11, 12],
      member_edges: [
        ...MIXED_STACK.member_edges,
        {
          picture_id: 12,
          strongest_edge: 0.91,
          closest_picture_id: 7,
          nearest_edge: 0.91,
          nearest_picture_id: 7,
        },
      ],
    };
    listStackMembersMock.mockImplementation(() =>
      Promise.resolve({
        stack_id: 42,
        member_count: 5,
        members: [
          ...MIXED_MEMBER_ROWS,
          { picture_id: 12, width: 800, height: 600, format: "JPEG" },
        ],
        next_offset: null,
      }),
    );
    await wrapper.setProps({ mixedStack: grown });
    await flushPromises();

    expect(listStackMembersMock).toHaveBeenCalledTimes(2);
    const cards = wrapper.findAll(".dc-card");
    expect(cards).toHaveLength(5);
    // The new member is not left as a permanent row of dashes.
    expect(cellOf(cards[4], "Resolution").find(".dc-val").text()).toBe(
      "800 x 600",
    );
    wrapper.unmount();
  });

  it("re-reads nothing when the same membership is served again", async () => {
    // A threshold reload re-serves the same stack with the same members, so the
    // fingerprint is unchanged and this costs exactly what keying on the id
    // would have cost: nothing.
    const wrapper = await mountMixedLoaded();
    expect(listStackMembersMock).toHaveBeenCalledTimes(1);

    await wrapper.setProps({
      mixedStack: { ...MIXED_STACK, threshold: 0.65, weakest_edge: 0.9 },
    });
    await flushPromises();
    expect(listStackMembersMock).toHaveBeenCalledTimes(1);
    expect(
      cellOf(wrapper.findAll(".dc-card")[0], "Resolution")
        .find(".dc-val")
        .text(),
    ).toBe("6000 x 4000");
    wrapper.unmount();
  });

  it("still offers no Contains row and no expansion band once the rows land", async () => {
    // The read supplies numbers, not a disclosure: every card is still one
    // picture, so there is nothing folded away to expand.
    const wrapper = await mountMixedLoaded();
    for (const card of wrapper.findAll(".dc-card")) {
      expect(cellOf(card, "Contains")).toBeNull();
    }
    expect(wrapper.findAll(".dc-expand")).toHaveLength(0);
    expect(wrapper.find('[data-testid="dedup-expansion"]').exists()).toBe(
      false,
    );
  });
});

describe("DedupCompareDialog: the mixed verdict footer", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(mixedMemberResponse);
  });

  it("wears the label and the icon the page predicted", () => {
    // The row and this footer must name the same press the same way, so the
    // prediction arrives as a prop rather than being computed a second time.
    const buttons = footerButtons(mountMixed());
    expect(buttons[2].text()).toContain("Split off 1");
    expect(buttons[2].props("iconLeft")).toBe("call-split");
    expect(buttons[2].find("kbd").text()).toBe("↵");

    const dissolving = footerButtons(
      mountMixed({ primaryLabel: "Unstack all 4", primaryIcon: "layers-off" }),
    );
    expect(dissolving[2].text()).toContain("Unstack all 4");
    expect(dissolving[2].props("iconLeft")).toBe("layers-off");
  });

  it("emits the verdict the user pressed", () => {
    const wrapper = mountMixed();
    const buttons = footerButtons(wrapper);
    expect(buttons[1].text()).toContain("Keep");
    expect(buttons[1].find("kbd").text()).toBe("K");

    buttons[1].find("button").trigger("click");
    buttons[2].find("button").trigger("click");
    expect(wrapper.emitted("keep")).toHaveLength(1);
    expect(wrapper.emitted("resolve")).toHaveLength(1);
    // The group verdicts are a different surface's, and must not fire here.
    expect(wrapper.emitted("stack")).toBeUndefined();
    expect(wrapper.emitted("keep-separate")).toBeUndefined();
  });

  it("locks both verdicts while one is in flight, but never Close", () => {
    const buttons = footerButtons(mountMixed({ busy: true }));
    expect(buttons[0].find("button").attributes("disabled")).toBeUndefined();
    expect(buttons[1].find("button").attributes("disabled")).toBeDefined();
    expect(buttons[2].find("button").attributes("disabled")).toBeDefined();
  });

  it("teaches the page's own gesture in the hint", () => {
    const hint = mountMixed().find(".dc-hint").text();
    expect(hint).toContain("press X, to mark it as a stranger");
    expect(hint).toContain("No file is ever deleted");
    // The cover gesture does not exist here and must not be taught.
    expect(hint).not.toContain("cover");
  });
});

describe("DedupCompareDialog: the mixed zoom", () => {
  beforeEach(() => {
    listStackMembersMock.mockReset();
    listStackMembersMock.mockImplementation(mixedMemberResponse);
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  function zoomEl() {
    return document.querySelector('[data-testid="dedup-zoom"]');
  }

  /** The `#id` the zoom's meta line is currently naming. */
  function zoomedId() {
    return zoomEl().querySelector(".dc-zv-meta").textContent.split(" ")[0];
  }

  it("walks the members, one entry each, in stack order", async () => {
    const wrapper = mountMixed();
    wrapper.vm.openZoom(0);
    await wrapper.vm.$nextTick();
    expect(zoomEl().querySelectorAll(".dc-zv-flip button")).toHaveLength(4);

    const order = [];
    for (let i = 0; i < 4; i += 1) {
      wrapper.vm.zoomTo(i);
      await wrapper.vm.$nextTick();
      order.push(zoomedId());
    }
    expect(order).toEqual(["#7", "#8", "#9", "#11"]);
    wrapper.vm.closeZoom();
    wrapper.unmount();
  });

  it("marks the member on screen when the zoom is clicked", async () => {
    // The zoom's gesture is the card's gesture at full screen; it does not get
    // to mean something else just because the picture got bigger.
    const wrapper = mountMixed();
    wrapper.vm.openZoom(1);
    await wrapper.vm.$nextTick();
    expect(zoomedId()).toBe("#8");
    zoomEl().querySelector(".dc-zv-img").click();
    expect(wrapper.emitted("toggle-mark")).toEqual([[8]]);
    expect(wrapper.emitted("set-cover")).toBeUndefined();
    wrapper.vm.closeZoom();
    wrapper.unmount();
  });

  it("flags the marked member, and never a cover", async () => {
    const wrapper = mountMixed();
    wrapper.vm.openZoom(3);
    await wrapper.vm.$nextTick();
    const flags = Array.from(zoomEl().querySelectorAll(".dc-flag--zv")).map(
      (el) => el.textContent.trim(),
    );
    expect(flags).toEqual(["Split off"]);

    wrapper.vm.zoomTo(0);
    await wrapper.vm.$nextTick();
    expect(zoomEl().querySelectorAll(".dc-flag--zv")).toHaveLength(0);
    wrapper.vm.closeZoom();
    wrapper.unmount();
  });

  it("opens a card's zoom on that member", async () => {
    const wrapper = mountMixed();
    await wrapper.findAll(".dc-zoom")[2].trigger("click");
    expect(zoomedId()).toBe("#9");
    wrapper.vm.closeZoom();
    wrapper.unmount();
  });
});
