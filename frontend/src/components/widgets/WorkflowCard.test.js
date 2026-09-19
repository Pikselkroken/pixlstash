// The uniform workflow card (v1.12 Workflows & Recipes, F0).
//
// jsdom has no layout, so "every card is exactly --wf-card-h" is pinned in two
// halves: the stylesheet's own sum, resolved against the shipped token values,
// and the mounted card's structure, which must not change with what it holds
// (0 or 5 LoRAs, long names). Together they say the content has no way to grow
// the card: the height is fixed, the four rows are fixed, and a row clips.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

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

/** The declarations of one rule in the card's stylesheet. */
function rule(selector) {
  const css = read("./WorkflowCard.vue")
    .split("<style scoped>")[1]
    .replace(/\/\*[\s\S]*?\*\//g, "");
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
  it("adds up to exactly --wf-card-h", () => {
    const t = tokens();
    const card = rule(".wf-card");
    const cover = rule(".wf-card__cover");
    const meta = rule(".wf-card__meta");

    expect(card.height).toBe("var(--wf-card-h)");
    expect(card["box-sizing"]).toBe("border-box");
    expect(card.overflow).toBe("hidden");
    expect(cover.flex).toBe("none");
    expect(meta.flex).toBe("none");

    const rows = meta["grid-template-rows"].match(/^repeat\((\d+), (.+)\)$/);
    expect(Number(rows[1])).toBe(4);
    const border = px(card.border.split(" ")[0]);
    const total =
      2 * border +
      px(cover.height) +
      2 * px(meta.padding) +
      4 * px(rows[2]) +
      3 * px(meta["row-gap"]);
    expect(t["--wf-card-h"]).toBe(252);
    expect(total).toBe(t["--wf-card-h"]);
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

  it("keeps the layered count on a stack with no pictures", () => {
    const wrapper = mountCard({ ...CROWDED, covers: [], picture_count: 0 });
    expect(wrapper.find(".wf-card__cover--empty").exists()).toBe(true);
    expect(wrapper.find(".wf-card__badge--start").text()).toContain("6");
  });

  it("does not claim no pictures while covers are still missing", () => {
    const wrapper = mountCard({ ...BARE, covers: [] });
    expect(wrapper.find(".wf-card__cover--empty").exists()).toBe(false);
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
