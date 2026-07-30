// The compare dialog is where a duplicate group is actually adjudicated, so
// these tests pin the things that would quietly make the decision wrong: which
// value is marked best in each column, which candidate shows its path, and that
// the two card gestures (pick a cover, leave a copy out) stay separate.

import { describe, it, expect, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";

// The thumbnail URL builder pulls in the Axios client; the dialog only needs a
// string for `<img src>`.
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/pictures/thumbnails/${id}.webp`,
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

describe("DedupCompareDialog: the comparison", () => {
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
    return Array.from(zoomEl().querySelectorAll(".dc-zv-flip button")).findIndex(
      (b) => b.classList.contains("dc-zv-on"),
    );
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

  it("continued wheeling zooms in — it never flips the candidate", async () => {
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

    // At the floor, sub-resistance accumulation keeps the zoom open — a full
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
  // whose siblings have a score and it does not — the row is group-level,
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
  // copy, not chrome — one chip per button, the primary key shown). The
  // machine-readable set rides aria-keyshortcuts, since the chips are
  // aria-hidden.
  it("shows the shortcut on both verdicts: Enter on Stack, K on Keep separate", () => {
    const buttons = footerButtons(mountDialog());
    expect(buttons[1].find("kbd").text()).toBe("K");
    expect(buttons[1].find("button").attributes("aria-keyshortcuts")).toBe(
      "K",
    );
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
