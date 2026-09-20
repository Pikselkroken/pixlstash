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

const read = (rel) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

const LONG = "a-checkpoint-name-that-is-far-too-long-for-any-card-column_v12";

const BARE = {
  key: "w0",
  name: "Outpaint to 16:9",
  models: [{ name: "flux1-fill-dev", kind: "checkpoint" }],
  loras: [],
  type: "outpaint",
  picture_count: 22,
  rating: 3.4,
  covers: ["/a.webp", "/b.webp", "/c.webp"],
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

  it("says what each row's chips are with the design's glyphs", () => {
    // The rows carry no labels, so the glyph is the only thing separating a
    // checkpoint from a LoRA from a slot the recipe fills.
    const rows = mountCard(CROWDED).findAll(".wf-card__meta > .wf-card__row");
    const glyphs = (row) =>
      row
        .findAll(".chip-row > .chip-row__chip .chip-row__icon")
        .map((i) => i.text());
    expect(glyphs(rows[1])).toEqual(["mdi-cube-outline"]);
    expect(glyphs(rows[2])).toEqual([
      ...Array(4).fill("mdi-layers"),
      "mdi-plus",
    ]);
    // A fact is not a model: no glyph, and no fill either.
    expect(glyphs(rows[3])).toEqual([]);
    for (const chip of rows[3].findAll(".chip-row > .chip-row__chip")) {
      expect(chip.classes()).toContain("chip-row__chip--fact");
    }
  });

  it("says No LoRAs rather than leaving the row blank", () => {
    expect(mountCard(BARE).findAll(".wf-card__row")[2].text()).toBe("No LoRAs");
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
    [1, ["/a.webp"]],
    [2, ["/a.webp", "/b.webp"]],
    [3, ["/a.webp", "/b.webp", "/c.webp"]],
    [3, ["/a.webp", "/b.webp", "/c.webp", "/d.webp"]],
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
    // `workflowCoverUrl` does not guard an empty entry - it would join one into
    // the truthy `/api/v1null`, a broken-image glyph - and since the
    // arrangement is counted from this list, such an entry would also buy
    // itself a cell.
    const cover = mountCard({
      ...BARE,
      covers: ["/a.webp", null, ""],
    }).find(".wf-card__cover");

    expect(cover.findAll(".wf-card__pic")).toHaveLength(1);
    expect(cover.classes()).toContain("wf-card__cover--1");
    expect(cover.find(".wf-card__pic img").attributes("src")).toContain(
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
    // The card's own label already reads all four rows, including the chips
    // "+N" clipped; leaving them exposed reads the card out twice.
    const wrapper = mountCard(CROWDED);
    const visible = [
      ...wrapper.findAll(".chip-row"),
      ...wrapper.findAll(".wf-card__name"),
      ...wrapper.findAll(".wf-card__none"),
    ];
    expect(visible.length).toBeGreaterThan(4);
    for (const node of visible) {
      expect(node.attributes("aria-hidden"), node.classes().join(" ")).toBe(
        "true",
      );
    }
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
// `GET /workflows/cards` sends `/pictures/thumbnails/{id}.webp?v=…`, and the
// real route is under `/api/v1`. An `<img src>` never reaches the apiClient
// interceptor that prepends the prefix, so a card that used the payload
// verbatim asked the page origin for a path nothing serves — every cover on
// the Workflows grid broke at once. It shipped that way in F1a because the
// grid was only reachable by typing `/workflows-next`.
describe("cover thumbnail URLs", () => {
  it("prepends the API base, so the src is a route that exists", () => {
    const wrapper = mountCard({
      ...CROWDED,
      covers: ["/pictures/thumbnails/12.webp?v=3"],
    });

    const src = wrapper.find(".wf-card__pic img").attributes("src");
    expect(src).toContain("/api/v1/pictures/thumbnails/12.webp?v=3");
    // The bare payload path is what broke it; it must not be the whole src.
    expect(src).not.toMatch(/^\/pictures\//);
  });
});

// ── The cover crop is top-anchored ───────────────────────────────────────
//
// jsdom applies no scoped CSS, so this reads the SFC's own `<style>` text -
// the shape `Toolbar.test.js` uses to pin a bar recipe. Worth pinning because
// the defect is invisible to every other test: a centred crop renders, it
// just takes the heads off, and the two short cells are far wider than tall.
describe("the cover crop", () => {
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
