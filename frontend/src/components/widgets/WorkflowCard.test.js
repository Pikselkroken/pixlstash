// The uniform workflow card (v1.12 Workflows & Recipes, F0).
//
// jsdom has no layout, so "the content cannot grow the card" is pinned in two
// halves: the stylesheet's own sum, resolved against the shipped token values,
// and the mounted card's structure, which must not change with what it holds
// (0 or 5 LoRAs, long names). Together they say the meta block is fixed, the
// four rows are fixed, and a row clips.
//
// The CARD's total height is deliberately not fixed any more: the cover is a
// 6:5 box at every column width and at every picture count, so cards still
// line up - that is what the old fixed height was really protecting. What
// changes inside it is the tracks, one arrangement per count, so the cell
// ratios are asserted three times rather than once.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { generatedMark } from "../../utils/modelShelf";
import { createPinia, setActivePinia } from "pinia";

// ⓘ carries *Show all N pictures* (F7), so the card now reaches the filter
// store and the router through it.
const push = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
  useRoute: () => ({ name: "workflows", query: {} }),
}));

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  const stubs = vuetifyComponentStubs();
  const VMenu = {
    name: "VMenu",
    template: `<div><slot name="activator" :props="{}" /><slot /></div>`,
  };
  return new Proxy(stubs, {
    get: (target, prop) => (prop === "VMenu" ? VMenu : target[prop]),
  });
});

import WorkflowCard from "./WorkflowCard.vue";
import { coverCellRatio } from "../../utils/workflowCard";

const read = (rel) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

const LONG = "a-checkpoint-name-that-is-far-too-long-for-any-card-column_v12";

// One `covers` entry, which is an object since #1465: the URL, plus the stored
// face-weighted rectangle where the picture has one. Most cases here are about
// the arrangement rather than the crop, so the rectangle is opt-in and those
// covers exercise the fallback path.
const cover = (url, crop = {}) => ({ url, ...crop });

// A cover WITH a rectangle: an 832×1216 generation's 384×561 bitmap whose
// square sits at y 120, which a top anchor would cut through.
const CROPPED = {
  thumbnail_width: 384,
  thumbnail_height: 561,
  square_crop_x: 0,
  square_crop_y: 120,
  square_crop_side: 384,
};

const BARE = {
  key: "w0",
  name: "Outpaint to 16:9",
  models: [{ name: "flux1-fill-dev", kind: "checkpoint" }],
  loras: [],
  type: "outpaint",
  picture_count: 22,
  rating: 3.4,
  covers: [cover("/a.webp"), cover("/b.webp"), cover("/c.webp")],
};

// A card whose whole content is a stored editor-format workflow file (#1466):
// no variant, so no recipe to read models off, and no pictures to cover it.
const FILE_ONLY = {
  key: "w9",
  name: "z image",
  models: [
    {
      // `title` is deliberately nothing like the filename's derived name:
      // `deriveModelName("z_image_bf16.safetensors")` is "Z Image Bf16", and
      // both would give the same initials, so an initials assertion alone
      // cannot tell which of the two the mark used.
      name: "z_image_bf16.safetensors",
      title: "Borrowed Light",
      icon: null,
      base_model: null,
      base_model_folded: null,
      kind: "unet",
    },
  ],
  loras: [],
  imported: true,
  picture_count: 0,
  covers: [],
  variant_count: 0,
};

// The same card on a model the shelf holds a chosen picture for.
const FILE_ONLY_WITH_ICON = {
  ...FILE_ONLY,
  key: "w11",
  models: [{ ...FILE_ONLY.models[0], icon: "a".repeat(64) }],
};

// The same card on a file the recovery could read nothing out of.
const UNREAD = { ...FILE_ONLY, key: "w10", models: [] };

// And one where the recovery found a LoRA but missed the loader beside it:
// `models` is empty while `loras` is not, so "non-empty" cannot stand in for
// "read".
const HALF_READ = {
  ...FILE_ONLY,
  key: "w12",
  models: [],
  loras: [{ name: "add_detail.safetensors", mark: "structural" }],
};

const CROWDED = {
  ...BARE,
  key: "w5",
  name: `${LONG} ${LONG}`,
  models: [{ name: LONG, kind: "checkpoint" }],
  loras: [
    ...Array.from({ length: 4 }, (_, i) => ({
      name: `${LONG}-${i}`,
      mark: "structural",
    })),
    { mark: "recipe" },
  ],
  differs_by: ["+ face detailer", "+ upscale 2×", "other checkpoint"],
  imported: true,
  stack_size: 6,
};

beforeEach(() => {
  setActivePinia(createPinia());
  push.mockClear();
});

function mountCard(card, props = {}) {
  return mount(WorkflowCard, {
    props: { card, ...props },
    global: { stubs: { Tooltip: true } },
  });
}

/** Token name → px, from the shipped token sheet and the card's local ones. */
function tokens() {
  const values = {};
  const sources =
    read("../../styles/design-tokens.css") + read("./WorkflowCard.vue");
  for (const [, name, px] of sources.matchAll(/(--[\w-]+):\s*(\d+)px;/g)) {
    values[name] = Number(px);
  }
  return values;
}

/** The card's own stylesheet, comments stripped. */
const styleBlock = () =>
  read("./WorkflowCard.vue")
    .split("<style scoped>")[1]
    .replace(/\/\*[\s\S]*?\*\//g, "");

/** Every selector in the card's stylesheet that matches `pattern`. */
function selectorsMatching(pattern) {
  return styleBlock()
    .split("}")
    .map((block) => block.split("{")[0].trim())
    .filter((selector) => pattern.test(selector));
}

/** The declarations of one rule in the card's stylesheet. */
function rule(selector) {
  const css = styleBlock();
  const body = css
    .split("}")
    .find((r) => r.split("{")[0].trim() === selector)
    ?.split("{")[1];
  expect(body, selector).toBeTruthy();
  return Object.fromEntries(
    body
      .split(";")
      .map((d) => d.split(":").map((s) => s.trim()))
      .filter(([k, v]) => k && v),
  );
}

/**
 * The cell shapes one cover arrangement produces, as width ÷ height.
 *
 * The cover is a 6:5 box at every count, so a column track worth `c` of the
 * columns' total and `r` of the rows' total is `c·W` wide by `r·(5/6)W` tall.
 * With two rows the first cell spans both of them - the mosaic's big cell - and
 * the second column holds one cell per row.
 */
function cellRatios(count) {
  const cover = rule(".wf-card__cover");
  const tracks = rule(`.wf-card__cover--${count}`);
  const [w, h] = cover["aspect-ratio"].split("/").map(Number);
  const coverH = h / w; // the cover's height as a fraction of its width
  const fr = (key) => tracks[key].split(" ").map((n) => parseFloat(n));
  const cols = fr("grid-template-columns");
  const rows = fr("grid-template-rows");
  const sum = (list) => list.reduce((total, one) => total + one, 0);
  const shape = (col, rowSpan) =>
    cols[col] / sum(cols) / ((rowSpan / sum(rows)) * coverH);

  if (rows.length === 1) return cols.map((_, col) => shape(col, sum(rows)));
  return [shape(0, sum(rows)), ...rows.map((row) => shape(1, row))];
}

/** Resolve `var(--x)`, `calc(var(--a) + var(--b))` or `1px` to a number. */
function px(value, t = tokens()) {
  const expr = value
    .replace(/^calc\((.*)\)$/, "$1")
    .replace(/var\((--[\w-]+)\)/g, (_, name) => {
      expect(t[name], name).toBeDefined();
      return t[name];
    })
    .replace(/(\d+)px/g, "$1");
  expect(expr).toMatch(/^[\d\s+×*]+$/);
  return expr.split("+").reduce((sum, term) => sum + Number(term.trim()), 0);
}

describe("WorkflowCard height", () => {
  it("adds the meta block up to exactly --wf-meta-h", () => {
    const t = tokens();
    const card = rule(".wf-card");
    const cover = rule(".wf-card__cover");
    const meta = rule(".wf-card__meta");

    expect(card["box-sizing"]).toBe("border-box");
    expect(card.overflow).toBe("hidden");
    expect(cover.flex).toBe("none");
    expect(meta.flex).toBe("none");

    const rows = meta["grid-template-rows"].match(/^repeat\((\d+), (.+)\)$/);
    expect(Number(rows[1])).toBe(4);
    const total =
      2 * px(meta.padding) + 4 * px(rows[2]) + 3 * px(meta["row-gap"]);
    expect(t["--wf-meta-h"]).toBe(118);
    expect(total).toBe(t["--wf-meta-h"]);
  });

  // The defect this replaces: a flat `height` on the cover against a fluid
  // card width, so the cells stretched from 1.2:1 at the 240px column floor
  // to 1.8:1 by 360px. A ratio holds at every width instead.
  it("sizes the cover by ratio, not by a pixel height", () => {
    const cover = rule(".wf-card__cover");

    expect(cover.height).toBeUndefined();
    expect(cover["aspect-ratio"]).toBe("6 / 5");
  });

  // The cover is 6:5 at every count and only the tracks inside it change, so
  // what each arrangement is worth is the shape of the cells it produces. One
  // assertion per count, because there are three arrangements now: a cell that
  // is never drawn without a picture in it means the tracks follow the strip
  // (#1456).
  it.each([
    ["1", [1.2]],
    ["2", [0.6, 0.6]],
    ["3", [0.8, 0.8, 0.8]],
  ])("gives %s picture(s) cells of %s (width ÷ height)", (count, expected) => {
    expect(cellRatios(count)).toEqual(
      expected.map((r) => expect.closeTo(r, 5)),
    );
  });

  // `coverCellRatio` is what the CROP is computed from (#1465), and it is a
  // hand-copy of the arithmetic above living in a different file. Nothing else
  // ties the two together: change a track here and the cells change shape while
  // the crop goes on framing them as the old one, which renders perfectly and
  // is simply cut in the wrong place. Derived from the stylesheet on one side
  // and read from the map on the other, so only agreement passes.
  it.each(["1", "2", "3"])(
    "crops %s picture(s) to the ratio the tracks actually produce",
    (count) => {
      for (const ratio of cellRatios(count)) {
        expect(coverCellRatio(Number(count))).toBeCloseTo(ratio, 5);
      }
    },
  );

  it("spans the big cell over both rows only where there are two rows", () => {
    // The row span is what makes the three-picture mosaic a mosaic: without it
    // the three cells auto-place as (1,1), (1,2), (2,1) and a whole track row
    // is empty. The ratios above cannot see that - they read the tracks, not
    // the placement - so the declaration itself is asserted here, and the
    // ABSENCE of it anywhere that would reach the other two arrangements.
    expect(
      rule(".wf-card__cover--3 .wf-card__pic:first-child")["grid-row"],
    ).toBe("1 / 3");
    for (const selector of selectorsMatching(/\.wf-card__pic:first-child$/)) {
      expect(selector, "spans a cover that has one row").toBe(
        ".wf-card__cover--3 .wf-card__pic:first-child",
      );
    }
  });

  it("fits the strip's worst case in the narrowest card row", () => {
    // A one-column stack panel is the narrowest host: its 1px border and
    // padding come off the 240px column floor, then the card's own 1px border
    // and the meta padding. The worst case is STRIP_MARKS marks, the hairline
    // and "+N" (~20px, "+12" at --text-2xs) - and `overflow: hidden` clips the
    // LAST item, which is the "+N", so it has to fit outright.
    const t = tokens();
    const marks = Number(
      read("./WorkflowCard.vue").match(/STRIP_MARKS = (\d+)/)[1],
    );
    const floor = Number(
      read("../views/WorkflowsView.vue").match(/COLUMN_MIN = (\d+)/)[1],
    );
    const panel = read("../panels/StackPanel.vue")
      .split(".stack-panel__grid {")[1]
      .split("}")[0];
    const panelPad = px(panel.match(/padding: ([^;]+);/)[1], t);
    const card = floor - 2 * 1 - 2 * panelPad;
    const row = card - 2 * 1 - 2 * px(rule(".wf-card__meta").padding, t);
    const gap = px(rule(".wf-card__strip").gap, t);
    const hairline = px(rule(".wf-card__rule").width, t);
    const worst =
      marks * t["--entity-thumb"] + hairline + 20 + (marks + 1) * gap;
    expect(row).toBe(196);
    expect(worst).toBeLessThanOrEqual(row);
    // And one more mark would not have fitted, so the cap is not loose.
    expect(worst + t["--entity-thumb"] + gap - 20).toBeGreaterThan(row);
  });

  it("keeps row 4 clear of the ⓘ button", () => {
    // ⓘ is a --control-h-sm square inset --space-3 from the card's bottom-right.
    // Row 4 sits in the same band (meta padding = the inset, row height = the
    // button), so the only thing between them is row 4's right padding.
    const info = rule(".wf-card__info");
    const meta = rule(".wf-card__meta");
    const facts = rule(".wf-card__row--facts");
    const buttonSize = tokens()["--control-h-sm"];

    expect(px(info.bottom)).toBe(px(meta.padding));
    const infoLeftEdge = px(info.right) + buttonSize;
    const rowContentEnd = px(meta.padding) + px(facts["padding-right"]);
    expect(rowContentEnd).toBeGreaterThan(infoLeftEdge);
  });

  it("turns ▸'s rotation off under reduced motion", () => {
    // The only claim in this component that a mounted test cannot reach.
    const css = read("./WorkflowCard.vue").split("<style scoped>")[1];
    const block = css.match(
      /@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\}/,
    );
    expect(block, "no reduced-motion block").toBeTruthy();
    expect(block[1]).toContain(".wf-card__toggle");
    expect(block[1]).toContain("transition: none");
  });

  it("keeps every row on one line, the name ellipsized", () => {
    // Rows do not clip themselves (that would cut ▸'s focus ring); the name
    // and each ChipRow do.
    expect(rule(".wf-card__row")["white-space"]).toBe("nowrap");
    expect(rule(".wf-card__name")["text-overflow"]).toBe("ellipsis");
  });

  it("gives the cover a fixed box its content cannot grow", () => {
    const cover = rule(".wf-card__cover");
    expect(cover["box-sizing"]).toBe("border-box");
    expect(cover.overflow).toBe("hidden");
    expect(rule(".wf-card__cover--empty").height).toBeUndefined();
  });
});

describe("WorkflowCard", () => {
  it.each([
    ["no LoRAs", BARE],
    ["five LoRAs and long names", CROWDED],
  ])("renders the same four rows with %s", (_, card) => {
    const wrapper = mountCard(card);
    const rows = wrapper.findAll(".wf-card__meta > .wf-card__row");
    expect(rows).toHaveLength(4);
    expect(rows[3].classes()).toContain("wf-card__row--facts");
    expect(wrapper.find(".wf-card__meta").element.children).toHaveLength(4);
    // Nothing outside the cover and the meta block takes height: ⓘ is absolute.
    expect(wrapper.findAll(".wf-card > .wf-card__cover")).toHaveLength(1);
  });

  it("draws the base model and every LoRA as marks, base first", () => {
    // Row 2 is the strip (#1485): the base model's mark, the hairline, then
    // one mark per LoRA, and a recipe slot as a dashed box rather than a mark.
    const strip = mountCard(CROWDED).find(".wf-card__strip");
    const items = strip.findAll("li");
    expect(items.map((li) => li.classes()[0])).toEqual([
      "wf-card__mark",
      "wf-card__rule",
      ...Array(5).fill("wf-card__mark"),
    ]);
    expect(items.at(-1).classes()).toContain("wf-card__mark--slot");
    expect(items.at(-1).find(".mmark").exists()).toBe(false);
    // A fact is not a model: no glyph, and no fill either.
    const facts = mountCard(CROWDED).find(".wf-card__row--facts");
    for (const chip of facts.findAll(".chip-row > .chip-row__chip")) {
      expect(chip.classes()).toContain("chip-row__chip--fact");
    }
  });

  it("puts each model's file string in its mark's tooltip", () => {
    const tips = mountCard({
      ...BARE,
      loras: [
        { name: "add detail xl", mark: "structural", quant: "bf16" },
        { mark: "recipe" },
      ],
    })
      .findAll(".wf-card__strip .wf-card__mark > tooltip-stub")
      .map((tip) => tip.attributes("text"));
    expect(tips).toEqual([
      "flux1-fill-dev",
      "add detail xl · BF16",
      "recipe LoRA",
    ]);
  });

  it("puts the hairline after the base model only when LoRAs follow it", () => {
    expect(mountCard(BARE).find(".wf-card__rule").exists()).toBe(false);
    const rule = mountCard({
      ...BARE,
      loras: [{ name: "detail", mark: "structural" }],
    }).find(".wf-card__rule");
    expect(rule.element.previousElementSibling.textContent).toBe(
      generatedMark({ filename: "flux1-fill-dev" }).initials,
    );
  });

  it.each([
    [5, 6, 0],
    [6, 6, 1],
    [12, 6, 7],
  ])(
    "draws a card with %i LoRAs as %i marks and a +%i",
    (count, marks, more) => {
      const wrapper = mountCard({
        ...BARE,
        loras: Array.from({ length: count }, (_, i) => ({
          name: `lora-${i}`,
          mark: "structural",
        })),
      });
      expect(wrapper.findAll(".wf-card__strip .wf-card__mark")).toHaveLength(
        marks,
      );
      const plus = wrapper.find(".wf-card__mark-more");
      expect(plus.exists() ? plus.text() : "+0").toBe(`+${more}`);
      // The count beside the name is the whole list, not what fitted.
      expect(wrapper.find(".wf-card__lora-count").text()).toBe(
        `${count} LoRAs`,
      );
    },
  );

  it("prints the base model's name once, beside the LoRA count", () => {
    const row = mountCard({
      ...BARE,
      models: [{ name: "juggernautXL v9", kind: "checkpoint", title: null }],
      loras: [{ name: "detail", mark: "structural" }],
    }).findAll(".wf-card__meta > .wf-card__row")[2];
    expect(row.find(".wf-card__base").text()).toBe("juggernautXL v9");
    expect(row.find(".wf-card__lora-count").text()).toBe("1 LoRA");
  });

  it("counts a recipe slot as a slot, not as a LoRA", () => {
    const count = (loras) =>
      mountCard({ ...BARE, loras })
        .find(".wf-card__lora-count")
        .text();
    expect(count([{ mark: "recipe" }])).toBe("1 LoRA slot");
    expect(
      count([{ name: "detail", mark: "structural" }, { mark: "recipe" }]),
    ).toBe("1 LoRA");
  });

  it("says No LoRAs rather than leaving the row blank", () => {
    expect(mountCard(BARE).find(".wf-card__lora-count").text()).toBe(
      "No LoRAs",
    );
  });

  it("carries the full chip list in its accessible name", () => {
    const name = mountCard(CROWDED).attributes("aria-label");
    for (let i = 0; i < 4; i++) {
      expect(name).toContain(`${LONG}-${i}, workflow LoRA`);
    }
    expect(name).toContain("recipe LoRA slot");
    expect(name).toContain("rated 3.4 of 5");
  });

  it("gives a stack a ▸ button outside the tab order", async () => {
    const wrapper = mountCard(CROWDED, { panelId: "stack-panel" });
    const toggle = wrapper.find(".wf-card__toggle");
    expect(toggle.element.tagName).toBe("BUTTON");
    expect(toggle.attributes("tabindex")).toBe("-1");
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(toggle.attributes("aria-controls")).toBe("stack-panel");
    await toggle.trigger("click");
    expect(wrapper.emitted("toggle")).toHaveLength(1);

    await wrapper.setProps({ expanded: true });
    expect(toggle.attributes("aria-expanded")).toBe("true");
    expect(toggle.classes()).toContain("wf-card__toggle--open");
    expect(wrapper.find(".wf-card__badge--start").text()).toContain("6");
  });

  it("opens the stack from the layered count as well as from ▸", async () => {
    const wrapper = mountCard(CROWDED, { panelId: "stack-panel" });
    const badge = wrapper.find(".wf-card__badge--start");
    // It is the mark that says there are cards underneath, so it is the first
    // thing a reader aims at. A real button, but off the tab order and hidden
    // from assistive tech: ▸ is the announced control, and hiding a second one
    // is only honest while the two do exactly the same thing.
    expect(badge.element.tagName).toBe("BUTTON");
    expect(badge.attributes("tabindex")).toBe("-1");
    expect(badge.attributes("aria-hidden")).toBe("true");
    await badge.trigger("click");
    expect(wrapper.emitted("toggle")).toHaveLength(1);
  });

  it("does not put a layered count on a card that is not a stack", () => {
    const wrapper = mountCard(BARE);
    expect(wrapper.find(".wf-card__badge--start").exists()).toBe(false);
  });

  it("keeps the layered count on a stack with no pictures", () => {
    const wrapper = mountCard({ ...CROWDED, covers: [], picture_count: 0 });
    expect(wrapper.find(".wf-card__cover--empty").exists()).toBe(true);
    expect(wrapper.find(".wf-card__badge--start").text()).toContain("6");
  });

  it("does not claim no pictures while covers are still missing", () => {
    const wrapper = mountCard({ ...BARE, covers: [] });
    expect(wrapper.find(".wf-card__cover--empty").exists()).toBe(false);
  });

  // The defect this replaces: three cells were drawn whatever the card had and
  // only the <img> was conditional, so one picture sat beside two painted
  // `input-background` rectangles and the card read as one that had failed to
  // load rather than one showing everything it has (#1456).
  it.each([
    [1, [cover("/a.webp")]],
    [2, [cover("/a.webp"), cover("/b.webp")]],
    [3, [cover("/a.webp"), cover("/b.webp"), cover("/c.webp")]],
    [3, ["a", "b", "c", "d"].map((n) => cover(`/${n}.webp`))],
  ])("draws %i cell(s), each with a picture in it", (cells, covers) => {
    const wrapper = mountCard({ ...BARE, covers });
    const cover = wrapper.find(".wf-card__cover");

    expect(cover.findAll(".wf-card__pic")).toHaveLength(cells);
    // Not one cell more than there are images: an empty one is the bug.
    expect(cover.findAll(".wf-card__pic img")).toHaveLength(cells);
    expect(cover.classes()).toContain(`wf-card__cover--${cells}`);
  });

  // `picture_count` says there are pictures, so this is not the "No pictures
  // yet" cover; it must still be a box of the cover's own size rather than a
  // collapsed nothing, and it should hold the shape the pictures are about to
  // land in. One cell for every count would make a one-picture-wide card's
  // placeholder the whole 6:5 box, which is what the `--empty` cover looks
  // like and reads as "there is nothing here".
  it.each([
    [22, 3],
    [2, 2],
    [1, 1],
  ])(
    "draws a %i-picture card whose covers have not arrived as %i cell(s)",
    (pictureCount, cells) => {
      const cover = mountCard({
        ...BARE,
        covers: [],
        picture_count: pictureCount,
      }).find(".wf-card__cover");

      expect(cover.findAll(".wf-card__pic")).toHaveLength(cells);
      expect(cover.find(".wf-card__pic img").exists()).toBe(false);
      expect(cover.classes()).toContain(`wf-card__cover--${cells}`);
    },
  );

  it("does not count a cover entry it cannot show", () => {
    // An entry with no `url` has no picture behind it, and since the
    // arrangement is counted from this list it would otherwise buy itself a
    // cell — the empty cell #1456 removed.
    const strip = mountCard({
      ...BARE,
      covers: [cover("/a.webp"), null, {}, cover("")],
    }).find(".wf-card__cover");

    expect(strip.findAll(".wf-card__pic")).toHaveLength(1);
    expect(strip.classes()).toContain("wf-card__cover--1");
    expect(strip.find(".wf-card__pic img").attributes("src")).toContain(
      "/a.webp",
    );
  });

  it("shows no rating for an unrated workflow", () => {
    const wrapper = mountCard({ ...BARE, rating: 0 });
    expect(wrapper.find(".wf-card__badge--bottom").exists()).toBe(false);
    expect(wrapper.attributes("aria-label")).not.toContain("of 5");
  });

  it("has no ▸ or layered count on a single workflow", () => {
    const wrapper = mountCard(BARE);
    expect(wrapper.find(".wf-card__toggle").exists()).toBe(false);
    expect(wrapper.find(".wf-card__badge--start").exists()).toBe(false);
  });

  it("has none of them on a member row either, but keeps its differences", () => {
    // A member is served the WHOLE stack's `stack_size` — deliberately, so a
    // member opened alone still shows what it differs by — so every row in an
    // open panel wore the layered count and a ▸ that could not expand
    // anything, and announced itself as a stack of six while sitting inside
    // that stack.
    const wrapper = mountCard(CROWDED, { member: true });
    expect(wrapper.find(".wf-card__toggle").exists()).toBe(false);
    expect(wrapper.find(".wf-card__badge--start").exists()).toBe(false);
    expect(wrapper.classes()).not.toContain("wf-card--stack");

    const name = wrapper.attributes("aria-label");
    expect(name).not.toContain("stack of 6 workflows");
    // What the row is for stays: the chips, their label, and everything else
    // the name reads.
    expect(name).toContain("differs by: + face detailer");
    expect(wrapper.text()).toContain("differs by");
    expect(wrapper.text()).toContain("+ upscale 2×");
  });

  it("puts ⓘ on every card as a real button outside the tab order", () => {
    const info = mountCard(BARE).find(".wf-card__info");
    expect(info.element.tagName).toBe("BUTTON");
    expect(info.attributes("tabindex")).toBe("-1");
    expect(info.attributes("aria-haspopup")).toBe("dialog");
  });

  it("keeps the cover for a card with no pictures and offers Run it…", async () => {
    const wrapper = mountCard({ ...BARE, covers: [], picture_count: 0 });
    const cover = wrapper.find(".wf-card__cover--empty");
    expect(cover.classes()).toContain("wf-card__cover");
    expect(cover.find(".wf-card__type").text()).toBe("outpaint");
    const run = cover.find("button");
    expect(run.text()).toContain("Run it…");
    expect(run.attributes("tabindex")).toBe("-1");
    await run.trigger("click");
    expect(wrapper.emitted("run")).toHaveLength(1);
  });

  it("hides every visible row from assistive tech", () => {
    // The card's own label already reads all four rows, including the marks
    // "+N" clipped; leaving them exposed reads the card out twice.
    const wrapper = mountCard(CROWDED);
    const visible = [
      ...wrapper.findAll(".chip-row"),
      ...wrapper.findAll(".wf-card__name"),
      ...wrapper.findAll(".wf-card__strip"),
      ...wrapper.findAll(".wf-card__base"),
      ...wrapper.findAll(".wf-card__none"),
    ];
    expect(visible.length).toBeGreaterThan(4);
    for (const node of visible) {
      expect(node.attributes("aria-hidden"), node.classes().join(" ")).toBe(
        "true",
      );
    }
  });

  // `base_model` and `base_model_folded` travel the wire so a model takes the
  // SAME colour on a card as on the shelf. Both were unobserved by this suite
  // until #1483 - every fixture left them null and nothing asserted a mark's
  // colour, so deleting either line from `markRow` kept it green. These
  // assert identity between renders rather than a literal colour, because the
  // token is `hsl()` and jsdom reports it back as `rgb()`.
  const colourOf = (models) =>
    mountCard({ ...FILE_ONLY, key: `w-${Math.random()}`, models })
      .find(".wf-card__strip .mmark-initials")
      .attributes("style");
  const CHECKPOINT = { kind: "checkpoint", title: "Borrowed Light" };

  it("colours a mark by the base model, not by the file name", () => {
    const one = colourOf([
      { ...CHECKPOINT, name: "a.safetensors", base_model_folded: "flux.1-dev" },
    ]);
    const other = colourOf([
      { ...CHECKPOINT, name: "b.safetensors", base_model_folded: "flux.1-dev" },
    ]);
    const different = colourOf([
      { ...CHECKPOINT, name: "a.safetensors", base_model_folded: "sdxl-1.0" },
    ]);
    // Same base model, different files: one colour.
    expect(one).toBe(other);
    // Same file, different base model: not that colour.
    expect(one).not.toBe(different);
  });

  it("prefers the folded spelling, so one model is one colour everywhere", () => {
    // Why BOTH fields travel rather than one: `baseModelKey` folds to the
    // canonical spelling first, and a card keying on the raw one would colour
    // the same model differently from the shelf.
    const folded = colourOf([
      {
        ...CHECKPOINT,
        name: "a.safetensors",
        base_model: "Flux.1 D",
        base_model_folded: "flux.1-dev",
      },
    ]);
    const rawOnly = colourOf([
      { ...CHECKPOINT, name: "a.safetensors", base_model: "Flux.1 D" },
    ]);
    expect(folded).not.toBe(rawOnly);
  });

  it("still reads the raw spelling when the shelf folded nothing", () => {
    // The `base_model` line of the mapping, on its own: a model the fold
    // table does not know still colours by its base model rather than by its
    // file name.
    const raw = colourOf([
      { ...CHECKPOINT, name: "a.safetensors", base_model: "Flux.1 D" },
    ]);
    const none = colourOf([{ ...CHECKPOINT, name: "a.safetensors" }]);
    expect(raw).not.toBe(none);
  });

  it("falls back to the file name when the shelf has no title for it", () => {
    // `filename` is the third line of the same mapping and had no observer
    // either: a model the shelf cannot name draws its initials off the file.
    const mark = mountCard({
      ...FILE_ONLY,
      key: "w-untitled",
      models: [
        { name: "quiet_river_v2.safetensors", kind: "checkpoint", title: null },
      ],
    }).find(".wf-card__strip .mmark-initials");
    expect(mark.text()).toBe(
      generatedMark({ filename: "quiet_river_v2.safetensors" }).initials,
    );
  });

  it("draws a card with no recipe the way it draws every other card", () => {
    // #1485 retired the cover marks #1466 put on this card: its models are on
    // the strip, and its empty cover holds the type and Run it… and nothing
    // else.
    const wrapper = mountCard(FILE_ONLY);
    const cover = wrapper.find(".wf-card__cover--empty");
    expect(cover.find(".mmark").exists()).toBe(false);
    expect(cover.find(".wf-card__empty-line").exists()).toBe(false);
    expect(cover.text()).toContain("Run it");
    const marks = wrapper.findAll(".wf-card__strip .wf-card__mark");
    expect(marks).toHaveLength(1);
    expect(marks[0].find(".mmark-initials").text()).toBe("BL");
    // The SHELF's name for it, as every other card's model row does (#1454).
    const base = wrapper.find(".wf-card__base");
    expect(base.text()).toBe("Borrowed Light");
    expect(base.text()).not.toContain("z_image_bf16");
  });

  it("leads the strip with the base model, however the file listed them", () => {
    // A real Z-Image graph lists its VAE and its text encoder before its UNET.
    // Accessory slots are named in ⓘ and nowhere on the card.
    const many = {
      ...FILE_ONLY,
      key: "w13",
      models: [
        { name: "ae.safetensors", kind: "vae" },
        { name: "qwen_3_4b.safetensors", kind: "clip" },
        { name: "controlnet.safetensors", kind: "controlnet" },
        {
          name: "z_image_bf16.safetensors",
          title: "Borrowed Light",
          kind: "unet",
        },
      ],
      loras: [{ name: "detail.safetensors", mark: "structural" }],
    };
    const marks = mountCard(many).findAll(".wf-card__strip .wf-card__mark");
    expect(marks).toHaveLength(2);
    expect(marks[0].find(".mmark-initials").text()).toBe("BL");
  });

  it("draws the shelf's own picture for a model that has one", () => {
    // The whole point of carrying `icon` on the slot: without it every mark
    // on every card falls to initials and the field is decoration.
    const wrapper = mountCard(FILE_ONLY_WITH_ICON);
    const img = wrapper.find(".wf-card__strip .mmark-img");
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toContain("a".repeat(64));
    expect(wrapper.find(".wf-card__strip .mmark-initials").exists()).toBe(
      false,
    );
  });

  it("will not say No checkpoint about a loader it could not read", () => {
    // The recovery found the LoRA and missed the base model beside it, so
    // `models` is empty on a card that plainly loads one.
    const wrapper = mountCard(HALF_READ);
    const rows = wrapper.findAll(".wf-card__meta > .wf-card__row");
    expect(rows[1].findAll(".wf-card__mark")).toHaveLength(1);
    expect(rows[2].text()).toContain("Base model not read");
    expect(rows[2].text()).toContain("1 LoRA");
    expect(wrapper.attributes("aria-label")).toContain("base model not read");
  });

  it("says a checkpoint it has no name for is missing, not absent", () => {
    // The loader is in the graph; its name was never recorded or was
    // forgotten with the shelf's copy. "No checkpoint" said the graph has
    // none, where the Workflow tab says the same state is missing.
    const wrapper = mountCard({
      ...BARE,
      models: [{ name: null, kind: "checkpoint" }],
    });
    expect(wrapper.findAll(".wf-card__row")[1].text()).toBe(
      "Checkpoint missing",
    );
    expect(wrapper.attributes("aria-label")).toContain("checkpoint missing");
  });

  it("says Checkpoint missing beside a strip of other models, too", () => {
    // The strip draws the LoRA, so the base-model half of row 3 is where the
    // missing checkpoint is said - not "No checkpoint", which is for a graph
    // with no base-model loader at all.
    const wrapper = mountCard({
      ...BARE,
      models: [{ name: null, kind: "checkpoint" }],
      loras: [{ name: "detail", kind: "lora", mark: "structural" }],
    });
    expect(wrapper.findAll(".wf-card__row")[2].text()).toContain(
      "Checkpoint missing",
    );
  });

  it("still says No checkpoint for a graph that loads none", () => {
    const wrapper = mountCard({ ...BARE, models: [] });
    expect(wrapper.findAll(".wf-card__row")[1].text()).toBe("No checkpoint");
    expect(wrapper.attributes("aria-label")).not.toContain("missing");
  });

  it("keeps No pictures yet on a pictureless card that has a recipe", () => {
    // A card whose pictures were all binned is a different state from one
    // nothing has ever run.
    const wrapper = mountCard({ ...BARE, covers: [], picture_count: 0 });
    expect(wrapper.find(".wf-card__empty-line").text()).toBe("No pictures yet");
  });

  it("says a card's models were not read rather than that it has none", () => {
    const wrapper = mountCard(UNREAD);
    const rows = wrapper.findAll(".wf-card__meta > .wf-card__row");
    expect(rows[1].text()).toBe("Models not read");
    // Row 2 said it for both, so row 3 does not say it again.
    expect(rows[2].text()).toBe("");
    const label = wrapper.attributes("aria-label");
    expect(label).toContain("base model not read");
    expect(label).toContain("LoRAs not read");
  });

  it("says No checkpoint, not 'not read', on a recipe card with none", () => {
    // Only accessory slots: a VAE is loaded, so "No models" would be false.
    const rows = mountCard({
      ...BARE,
      models: [{ name: "ae", kind: "vae" }],
      loras: [],
    }).findAll(".wf-card__meta > .wf-card__row");
    expect(rows[1].text()).toBe("No checkpoint");
    expect(rows[2].text()).toBe("No LoRAs");
  });

  it("hides the pictureless cover's own words too", () => {
    const wrapper = mountCard({ ...BARE, covers: [], picture_count: 0 });
    expect(wrapper.find(".wf-card__type").attributes("aria-hidden")).toBe(
      "true",
    );
    expect(wrapper.find(".wf-card__empty-line").attributes("aria-hidden")).toBe(
      "true",
    );
  });

  it("puts the SHELF's name on the model chip, not the filename", () => {
    // The card's `name` row is generated from `title` server-side, so a chip
    // built from `name` reads `realvisxl.safetensors` under a row reading
    // `Krea 2` - one model, two names, on one card (#1416, #1454).
    const text = mountCard({
      ...BARE,
      name: "Krea 2: Outpaint",
      models: [
        { name: "realvisxl.safetensors", title: "Krea 2", kind: "checkpoint" },
      ],
    }).text();
    expect(text).toContain("Krea 2");
    expect(text).not.toContain("realvisxl.safetensors");
  });

  it("draws the precision beside the base model's name", () => {
    // The name has had the postfix taken off it server-side, so two quant
    // builds of one model are two cards reading one name - this is the whole
    // of what tells them apart.
    const row = mountCard({
      ...BARE,
      models: [{ name: "t5xxl", kind: "checkpoint", quant: "fp8_e4m3" }],
    }).findAll(".wf-card__meta > .wf-card__row")[2];
    expect(row.find(".wf-card__base").text()).toBe("t5xxl");
    expect(row.find(".wf-card__quant").text()).toBe("FP8");
  });

  it("draws no precision at all when nothing recorded one", () => {
    // Not an empty label and not `UNKNOWN`: a badge that is always there stops
    // meaning anything, and most models record no precision.
    const wrapper = mountCard({
      ...BARE,
      models: [{ name: "t5xxl", kind: "checkpoint", quant: null }],
    });
    expect(wrapper.find(".wf-card__quant").exists()).toBe(false);
  });

  it("speaks structural LoRA precision", () => {
    const card = mountCard({
      ...BARE,
      loras: [{ name: "Foxglove", mark: "structural", quant: "fp8_e4m3" }],
    });
    expect(card.find("article").attributes("aria-label")).toContain(
      "LoRAs: Foxglove, FP8 E4M3, workflow LoRA",
    );
  });

  it("says the type short on the facts row", () => {
    const facts = mountCard({
      ...BARE,
      type: "txt2img",
      type_label: "Text to Image",
    }).find(".wf-card__row--facts");
    expect(facts.text()).toContain("T2I");
    expect(facts.text()).not.toContain("Text to Image");
  });

  it("speaks the precision, since every chip on the card is aria-hidden", () => {
    // The accessible name is the whole of what a screen reader gets off this
    // card, so a badge missing from it does not exist for that reader.
    const label = mountCard({
      ...BARE,
      models: [{ name: "t5xxl", kind: "checkpoint", quant: "fp8_e4m3" }],
    })
      .find("article")
      .attributes("aria-label");
    expect(label).toContain("checkpoint t5xxl FP8 E4M3");
  });

  it("puts the precision on every model the ⓘ popover lists", () => {
    const popover = mountCard({
      ...BARE,
      models: [
        { name: "flux1 dev", kind: "unet", quant: "q4_k_m" },
        { name: "ae", kind: "vae", quant: null },
      ],
      loras: [{ name: "detail", mark: "structural", quant: "bf16" }],
    }).find('[data-testid="workflow-info-popover"]');
    // Per LINE, not over the whole panel's text: a chip on the wrong row, or
    // an empty chip on the row that records nothing, both satisfy a substring
    // search of the panel and neither is what this asserts.
    const lines = popover.findAll(".info-popover__line");
    expect(
      lines.map((line) => {
        const chip = line.find(".info-popover__chip");
        return chip.exists() ? chip.text() : null;
      }),
    ).toEqual(["Q4_K_M", null, "BF16"]);
  });

  it("lists every model, not just the checkpoint, in the ⓘ popover", () => {
    const text = mountCard({
      ...BARE,
      models: [
        { name: "flux1-dev", kind: "unet" },
        { name: "ae", kind: "vae" },
      ],
    })
      .find('[data-testid="workflow-info-popover"]')
      .text();
    expect(text).toContain("flux1-dev");
    expect(text).toContain("ae");
    expect(text).toContain("vae");
  });

  it("lists models, differences and defaults in the ⓘ popover", () => {
    const popover = mountCard({
      ...CROWDED,
      defaults: [{ label: "Steps · CFG", value: "8 · 2.0" }],
      saved_recipe_count: 3,
    }).find('[data-testid="workflow-info-popover"]');
    const text = popover.text();
    expect(text).toContain("Stack of 6 workflows · found in 22 pictures");
    expect(text).toContain("filled by the recipe");
    expect(text).toContain("+ upscale 2×");
    expect(text).toContain("8 · 2.0");
    expect(text).toMatch(/Saved recipes\s*3/);
  });
});

// ── The covers are API-relative and an <img> does not use axios ───────────
//
// `GET /workflows` sends `/pictures/thumbnails/{id}.webp?v=…`, and the
// real route is under `/api/v1`. An `<img src>` never reaches the apiClient
// interceptor that prepends the prefix, so a card that used the payload
// verbatim asked the page origin for a path nothing serves — every cover on
// the Workflows grid broke at once. It shipped that way in F1a because the
// grid was only reachable by typing `/workflows-next`.
describe("cover thumbnail URLs", () => {
  it("prepends the API base, so the src is a route that exists", () => {
    const wrapper = mountCard({
      ...CROWDED,
      covers: [cover("/pictures/thumbnails/12.webp?v=3")],
    });

    const src = wrapper.find(".wf-card__pic img").attributes("src");
    expect(src).toContain("/api/v1/pictures/thumbnails/12.webp?v=3");
    // The bare payload path is what broke it; it must not be the whole src.
    expect(src).not.toMatch(/^\/pictures\//);
  });
});

// ── The cover crop follows the stored rectangle ──────────────────────────
//
// The card crops each cell around the face-weighted rectangle the library
// already computed for that picture (#1465), so a full-length figure keeps its
// face instead of being cut wherever the top anchor happened to land. The
// fallback below is the other half, and the one that would be a regression.
describe("the cover crop", () => {
  const imgStyle = (card) =>
    mountCard(card).find(".wf-card__pic img").attributes("style") ?? "";

  it("crops a single cover's 6:5 cell around the stored rectangle", () => {
    // 6:5 around a 384 square in a 384×561 bitmap is the full width and 320
    // tall, centred on the square at y 312 → the crop starts at 152 rather
    // than at 0. The img is sized and translated in percentages of the cell.
    const style = imgStyle({ ...BARE, covers: [cover("/a.webp", CROPPED)] });

    expect(style).toContain(`height: ${(561 / 320) * 100}%`);
    expect(style).toContain(`top: ${(-152 / 320) * 100}%`);
    expect(style).toContain("object-fit: cover");
  });

  it("crops the mosaic's cells to their own 4:5, not to the 6:5 box", () => {
    // Three pictures put every cell at 4:5, which keeps 480 of the 561 rows
    // and so moves the crop by 72 rather than 152. A card that cropped every
    // arrangement the same way would be wrong in two of the three.
    const style = imgStyle({
      ...BARE,
      covers: [
        cover("/a.webp", CROPPED),
        cover("/b.webp", CROPPED),
        cover("/c.webp", CROPPED),
      ],
    });

    expect(style).toContain(`height: ${(561 / 480) * 100}%`);
    expect(style).toContain(`top: ${(-72 / 480) * 100}%`);
  });

  it("leaves a cover with no rectangle to the stylesheet", () => {
    // `square_crop_x` is null until the picture has been processed, and those
    // covers have to look exactly as they did before this existed. An inline
    // width or top here would be a framing invented from a missing number.
    const style = imgStyle({ ...BARE, covers: [cover("/a.webp")] });

    expect(style).not.toContain("top:");
    expect(style).not.toContain("width:");
  });
});

// ── The fallback crop is top-anchored ────────────────────────────────────
//
// jsdom applies no scoped CSS, so this reads the SFC's own `<style>` text -
// the shape `Toolbar.test.js` uses to pin a bar recipe. Worth pinning because
// the defect is invisible to every other test: a centred crop renders, it
// just takes the heads off, and the two short cells are far wider than tall.
describe("the fallback crop", () => {
  const styleOf = async (path) => {
    const { readFileSync } = await import("node:fs");
    return readFileSync(`${process.cwd()}/${path}`, "utf8");
  };

  it("anchors to the top, as the picture grid's cropped tiles do", async () => {
    const source = await styleOf("src/components/widgets/WorkflowCard.vue");
    const block = source.slice(
      source.indexOf(".wf-card__pic img {"),
      source.indexOf("}", source.indexOf(".wf-card__pic img {")),
    );
    expect(block).toContain("object-fit: cover");
    expect(block).toContain("object-position: top center");
  });

  it("uses the same anchor the shipped grid does", async () => {
    // One convention, not two: if the grid's moves, this should move with it
    // rather than quietly becoming the odd one out.
    const grid = await styleOf("src/components/views/ImageGrid.css");
    expect(grid).toContain("object-position: top center");
  });

  // The CSS half of #1465's crop, which is load-bearing and was invisible to
  // every other test in this file: `coverCellStyle` emits percentages, and a
  // percentage only means the CELL if the cell is the containing block and the
  // img is taken out of flow. Delete `relative` and the img sizes against the
  // whole cover instead, so every cell crops wrongly; delete `absolute` and
  // the inline `left`/`top` are inert and the heads are still cut. Both render
  // perfectly either way, which is why they are pinned here.
  it("makes the cell the box the crop is computed against", () => {
    expect(rule(".wf-card__pic").position).toBe("relative");
    expect(rule(".wf-card__pic").overflow).toBe("hidden");
  });

  it("takes the cropped img out of flow so its offsets apply", () => {
    const img = rule(".wf-card__pic img");

    expect(img.position).toBe("absolute");
    expect(img.top).toBe("0");
    expect(img.left).toBe("0");
  });
});

// ── The stack badge says what its number counts ──────────────────────────
describe("the layered count", () => {
  it("says the number is workflows, not pictures", () => {
    const wrapper = mountCard({ ...CROWDED, stack_size: 3 });
    const badge = wrapper.find(".wf-card__badge--button");

    expect(badge.text()).toContain("3");
    // Two glyph-and-number badges sit at opposite ends of the same cover, so
    // which one counts workflows is otherwise a guess.
    expect(badge.findComponent({ name: "Tooltip" }).props("text")).toBe(
      "3 workflows in this stack",
    );
  });

  it("puts no layered badge on a card that is not a stack", () => {
    const wrapper = mountCard({ ...BARE, stack_size: 1 });
    expect(wrapper.find(".wf-card__badge--button").exists()).toBe(false);
  });
});

// ── The selection mark is the card's own ─────────────────────────────────
//
// It used to be on the CELL each host wraps around this component, where it
// could never be seen: `.wf-card` paints an opaque `surface` across the whole
// cell, so the wash and the ring were drawn underneath the card. Nothing
// failed - the rule was valid, applied, and invisible - which is exactly why
// this is pinned on the painted class rather than on the host's markup.
describe("the selection mark", () => {
  it("marks the card itself, not something behind it", () => {
    expect(
      mountCard(BARE, { selected: true }).find(".wf-card").classes(),
    ).toContain("wf-card--selected");
    expect(mountCard(BARE).find(".wf-card").classes()).not.toContain(
      "wf-card--selected",
    );
  });

  it("wears the shell's wash and ring on one overlay", () => {
    const mark = rule(".wf-card--selected::after");

    expect(mark.background).toBe("var(--active-wash)");
    expect(mark["box-shadow"]).toBe("var(--selection-ring)");
  });

  // The whole point: an inset shadow paints over an element's background but
  // UNDER its children, and the cover's <img>s are children. On the card
  // itself the ring ran along the text rows and stopped dead at the
  // thumbnails. Only a layer above the content covers both.
  it("lies above the cover, so the mark crosses the thumbnails", () => {
    const mark = rule(".wf-card--selected::after");

    expect(mark.position).toBe("absolute");
    expect(mark.inset).toBe("0");
    expect(mark["z-index"]).toBe("var(--z-raised)");
    // Above the badges, which sit on the cover at a bare z-index of 1.
    expect(Number(rule(".wf-card__badge--start")["z-index"])).toBeLessThan(10);
    // And it must not eat the clicks meant for the controls underneath.
    expect(mark["pointer-events"]).toBe("none");
  });

  // The reason the mark cannot live on an ancestor either, stated as a fact
  // about this file so that moving it back up fails here.
  it("paints an opaque background, which is why an ancestor cannot mark it", () => {
    expect(rule(".wf-card").background).toBe("rgb(var(--v-theme-surface))");
  });
});
