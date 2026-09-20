// The open stack's panel (v1.12 F2) — the List view and the member menu.
//
// The parity test is the point of the file. "The same menu in ⋯ and in
// right-click and in both views" is four combinations, and a component that
// builds them from one list passes it by construction — which is exactly why
// it is asserted: the next person to add an item can make them differ in one
// edit, and nothing else in the suite would notice.

import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  const stubs = vuetifyComponentStubs();
  // The menu's content is rendered inline rather than teleported, so the
  // items are findable; `modelValue` still gates it, so "the menu is open"
  // stays a real assertion.
  const VMenu = {
    name: "VMenu",
    props: ["modelValue"],
    template: `<div><slot name="activator" :props="{}" /><slot v-if="modelValue" /></div>`,
  };
  return new Proxy(stubs, {
    get: (target, prop) => (prop === "VMenu" ? VMenu : target[prop]),
  });
});

import StackPanel from "./StackPanel.vue";
import WorkflowCard from "../widgets/WorkflowCard.vue";
import { workflowCoverUrl } from "../../api/workflows";
import { coverCellRatio } from "../../utils/workflowCard";
import { useWorkflowPrefsStore } from "../../stores/useWorkflowPrefsStore";

const member = (key, extra = {}) => ({
  key,
  name: key,
  models: [{ name: "realvisXL_v5", kind: "checkpoint" }],
  loras: [],
  differs_by: [],
  picture_count: 4,
  rating: 4.25,
  // A `covers` entry is an object since #1465: the URL, plus the stored
  // face-weighted rectangle where the picture has one. These carry none, so
  // they are also the fallback case the strip must keep drawing as before.
  //
  // `picture_id` is on each of them because a member card is read through
  // `GET /workflows/cards/{key}`, which serves the shape the grid does. The
  // fixture had the URLs alone, so every test here exercised the INERT cover
  // that shipped before #1455 and none would have noticed a member card
  // losing its click.
  covers: [
    { url: `/thumb/${key}/1`, picture_id: Number(`${key.length}1`) },
    { url: `/thumb/${key}/2`, picture_id: Number(`${key.length}2`) },
  ],
  stack_size: 3,
  member_keys: [],
  ...extra,
});

const MEMBERS = [
  // The cover carries the UNION of what its members differ by — that is what
  // the grid draws on the one tile — so the panel has to blank it rather than
  // list a row's siblings' differences as its own.
  member("cover", { differs_by: ["+ face detailer", "other checkpoint"] }),
  member("second", { differs_by: ["+ face detailer"] }),
  member("third", {
    models: [{ name: "juggernautXL_v9", kind: "checkpoint" }],
    differs_by: ["other checkpoint"],
  }),
];

/** The band itself: the component has more than one root node. */
const band = (wrapper) => wrapper.find('[data-testid="stack-panel"]');

function makePanel(props = {}) {
  return mount(StackPanel, {
    props: {
      panelId: "panel",
      name: "Cinematic portrait",
      members: MEMBERS,
      size: 3,
      columns: 3,
      canReorder: true,
      ...props,
    },
  });
}

/** The menu's items, in order, as a reader would read them. */
function menuLabels(wrapper) {
  const menu = wrapper.find('[data-testid="member-menu"]');
  if (!menu.exists()) return null;
  // The label, not the whole item: the stubbed `v-icon` renders its name as
  // text and would be compared as part of the item.
  return menu
    .findAll(".ctx-item")
    .map((item) => item.find(".ctx-label-text").text());
}

/** Right-click one member row. */
async function rightClick(wrapper, key) {
  await wrapper.find(`[data-key="${key}"]`).trigger("contextmenu", {
    clientX: 10,
    clientY: 20,
  });
}

/** Press the ⋯ button on one member row. */
async function pressMore(wrapper, key) {
  const row = wrapper.find(`[data-key="${key}"]`);
  await row.find('button[aria-label*="What you can do"]').trigger("click");
}

describe("StackPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.spyOn(console, "warn").mockImplementation(() => {});
    // jsdom's own `localStorage`, emptied: stubbing the whole `window` takes
    // its event constructors with it and `trigger("contextmenu")` then throws.
    window.localStorage.clear();
  });

  it("opens on Grid and draws the members as cards", () => {
    const wrapper = makePanel();
    expect(wrapper.find(".stack-panel__grid").exists()).toBe(true);
    expect(wrapper.find(".stack-panel__list").exists()).toBe(false);
    expect(wrapper.findAll('[data-testid="workflow-card"]')).toHaveLength(3);
  });

  it("draws List as treegrid rows with column gridcells and no table", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel();
    await wrapper.vm.$nextTick();

    expect(wrapper.find("table").exists()).toBe(false);
    const rows = wrapper.findAll(".stack-panel__row");
    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(row.attributes("role")).toBe("row");
      expect(row.attributes("aria-level")).toBe("2");
      // Six columns: workflow, checkpoint, differs by, pictures, rating, ⋯.
      expect(row.findAll('[role="gridcell"]')).toHaveLength(6);
    }
    expect(
      wrapper.findAll('[role="columnheader"]').map((cell) => cell.text()),
    ).toEqual([
      "Workflow",
      "Checkpoint",
      "Differs by",
      "Pictures",
      "Rating",
      "",
    ]);
  });

  it("names the cover and, in Checkpoint, shows only what differs", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel();
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll(".stack-panel__row");
    expect(rows[0].find(".stack-panel__pill").text()).toBe("Cover");
    expect(rows[1].find(".stack-panel__pill").exists()).toBe(false);

    // The cover is what the column is measured against, and `second` shares
    // its checkpoint — so only `third` names one.
    // The first visible chip's label: `ChipRow` keeps a hidden copy of every
    // chip to measure against, so the cell's own `text()` reads each one twice.
    const checkpoints = rows.map((row) => {
      const chip = row.find(".stack-panel__ckpt .chip-row__label");
      return chip.exists() ? chip.text() : "";
    });
    expect(checkpoints).toEqual(["", "", "juggernautXL_v9"]);
  });

  it("names the differing checkpoint the way the SHELF names it", async () => {
    // Same preference as the card's own name row (#1454): a panel chip saying
    // the filename while the grid card above it says the shelf name is one
    // model called two things, one click apart.
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel({
      members: [
        MEMBERS[0],
        {
          ...MEMBERS[2],
          models: [
            {
              name: "juggernautxl_v9.safetensors",
              title: "Juggernaut XL",
              kind: "checkpoint",
            },
          ],
        },
      ],
    });
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll(".stack-panel__row");
    const chip = rows[1].find(".stack-panel__ckpt .chip-row__label");
    expect(chip.text()).toBe("Juggernaut XL");
  });

  it("draws a thumbnail per cover the member has, and no empty images", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel({
      members: [MEMBERS[0], { ...MEMBERS[1], covers: [] }],
    });
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll(".stack-panel__row");
    // ONE CELL PER PICTURE, as the card's cover does since #1456: a fixed
    // three put this member's two pictures beside a painted box, and made the
    // same stack's Grid and List views disagree about the same workflow.
    // The `<img>` is still conditional on top of that. A `v-show` here made
    // this assertion vacuous: three empty tags whatever `covers` held, in a
    // browser that draws a broken-image glyph for each.
    expect(rows[0].findAll(".stack-panel__thumb")).toHaveLength(2);
    expect(rows[0].find(".stack-panel__thumbs").classes()).toContain(
      "stack-panel__thumbs--2",
    );
    const images = rows[0].findAll("img");
    expect(images).toHaveLength(2);
    for (const image of images) expect(image.attributes("alt")).toBe("");
    // Its covers have not arrived, so it keeps the arrangement its four
    // pictures are about to land in - and no <img> in any of those cells.
    expect(rows[1].findAll(".stack-panel__thumb")).toHaveLength(3);
    expect(rows[1].findAll("img")).toHaveLength(0);

    // **Joined, not the payload path.** `covers` arrives API-relative and an
    // `<img src>` bypasses Axios, so the raw value asks the PAGE origin for a
    // path no route serves — every thumbnail a broken image. `toBeTruthy()`
    // was here and passed on exactly that. Asserted against the api layer's
    // own helper so this does not become a second spelling of the prefix, and
    // against the raw value so the helper cannot be a no-op.
    const raw = MEMBERS[0].covers[0];
    expect(images[0].attributes("src")).toBe(workflowCoverUrl(raw));
    expect(images[0].attributes("src")).not.toBe(raw.url);
  });

  it("crops a row's thumbnail around the stored rectangle, as the card does", async () => {
    // The same helper the card uses, so a stack's row and its tile frame the
    // same picture the same way (#1465). Two pictures here, so each cell is
    // 3:5: 384/0.6 = 640 rows wanted from a 561-row bitmap, so it takes all of
    // them and trims the sides around the face's own x instead.
    useWorkflowPrefsStore().setStackView("list");
    const cropped = {
      ...MEMBERS[0],
      covers: MEMBERS[0].covers.map((cover) => ({
        ...cover,
        thumbnail_width: 384,
        thumbnail_height: 561,
        square_crop_x: 0,
        square_crop_y: 120,
        square_crop_side: 384,
      })),
    };
    const wrapper = makePanel({ members: [cropped, MEMBERS[1]] });
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll(".stack-panel__row");
    const style = rows[0].find("img").attributes("style");
    expect(style).toContain("top: 0%");
    expect(style).toContain(`width: ${(384 / (561 * (3 / 5))) * 100}%`);
    // The member whose covers carry no rectangle keeps the stylesheet's own
    // top-anchored crop — the look every cover had before this existed.
    expect(rows[1].find("img").attributes("style")).toBeUndefined();
  });

  it("keeps the cover's union out of the row's name as well as its cells", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel();
    await wrapper.vm.$nextTick();

    // Every cell but ⋯ is `aria-hidden`, so the label is the whole of what a
    // screen reader hears. Blanking only the cells would leave it saying the
    // cover differs from itself by what its siblings differ from IT by.
    const label = wrapper.find(".stack-panel__row").attributes("aria-label");
    expect(label).toContain("the stack's cover");
    expect(label).not.toContain("face detailer");
    expect(label).not.toContain("other checkpoint");
    // The rest of the name is untouched, so this is a narrowing and not a
    // silencing: the row still identifies its workflow.
    expect(label).toContain("cover");
    expect(label).toContain("realvisXL_v5");

    // …and a member row still says what it differs by, which is its point.
    const second = wrapper
      .findAll(".stack-panel__row")[1]
      .attributes("aria-label");
    expect(second).toContain("face detailer");
  });

  it("blanks the cover row's Differs by, which is the others' union", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel();
    await wrapper.vm.$nextTick();

    const chips = wrapper
      .findAll(".stack-panel__row")
      .map((row) =>
        row
          .findAll(".stack-panel__facts .chip-row__label")
          .map((chip) => chip.text()),
      );
    // Row 1 is the cover: the column says what is NOT shared, and the cover's
    // own chips are every OTHER row's differences. Listing them there beside
    // a Checkpoint cell blanked for that same reason is the column arguing
    // with itself.
    expect(chips[0]).toEqual([]);
    expect(chips[1]).toContain("+ face detailer");
    expect(chips[2]).toContain("other checkpoint");
  });

  it("draws no checkpoint chip for a model whose name was forgotten", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel({
      members: [
        MEMBERS[0],
        { ...MEMBERS[1], models: [{ name: null, kind: "checkpoint" }] },
        { ...MEMBERS[2], models: [{ name: null, kind: "checkpoint" }] },
      ],
    });
    await wrapper.vm.$nextTick();

    // A chip with no label is a bordered glyph reading "differs, by nothing
    // in particular", and two forgotten names are not evidence of a shared
    // model either.
    expect(wrapper.findAll(".stack-panel__ckpt .chip-row__chip")).toHaveLength(
      0,
    );
  });

  it("offers the same menu from ⋯ and right-click, in Grid and in List", async () => {
    const seen = [];
    for (const view of ["grid", "list"]) {
      for (const open of [pressMore, rightClick]) {
        setActivePinia(createPinia());
        useWorkflowPrefsStore().setStackView(view);
        const wrapper = makePanel();
        await wrapper.vm.$nextTick();
        expect(menuLabels(wrapper)).toBeNull();
        await open(wrapper, "second");
        seen.push(menuLabels(wrapper));
      }
    }
    expect(seen[0]).toEqual([
      // A member card's covers are buttons (#1455), so its menu is where a
      // keyboard reaches them — the tiles are at `tabindex="-1"`. The label
      // is positional when a TILE was right-clicked ("Open picture 2 of 3");
      // these four opens all land on the row rather than a tile, so all four
      // read the plain form and stay comparable.
      "Open picture",
      "Make it the cover",
      "Move earlier",
      "Move later",
      "Unstack",
      "Hide",
    ]);
    // All four the same list, compared against each other rather than only
    // against the literal: a change to the items keeps this honest.
    for (const labels of seen) expect(labels).toEqual(seen[0]);
  });

  it("emits the menu's verbs for the member under the pointer", async () => {
    // By `data-item`, not by position: the rows are a list somebody will add
    // to, and an index-based assertion silently starts testing its neighbour
    // when they do — which is exactly what happened when *Open picture* was
    // added at the top.
    const row = (wrapper, id) => wrapper.find(`.ctx-item[data-item="${id}"]`);
    const wrapper = makePanel();

    await rightClick(wrapper, "third");
    await row(wrapper, "cover").trigger("click");
    expect(wrapper.emitted("make-cover")).toEqual([["third"]]);
    await rightClick(wrapper, "third");
    await row(wrapper, "earlier").trigger("click");
    expect(wrapper.emitted("move")).toEqual([["third", -1]]);
    await rightClick(wrapper, "third");
    await row(wrapper, "unstack").trigger("click");
    expect(wrapper.emitted("unstack")).toEqual([["third"]]);
  });

  it("refuses the moves that have nowhere to go, and all of them with no stack id", async () => {
    // Keyed by `data-item` rather than by position, for the reason above.
    const refused = (wrapper) =>
      Object.fromEntries(
        wrapper
          .findAll(".ctx-item")
          .map((item) => [
            item.attributes("data-item"),
            item.attributes("disabled") !== undefined,
          ]),
      );
    const wrapper = makePanel();
    await rightClick(wrapper, "cover");
    // The cover cannot become the cover, nor move earlier; later it can.
    expect(refused(wrapper)).toEqual({
      "open-picture": false,
      cover: true,
      earlier: true,
      later: false,
      unstack: false,
      hide: false,
    });

    // …and the last member is the mirror of it: nowhere later to go.
    await rightClick(wrapper, "third");
    expect(refused(wrapper)).toEqual({
      "open-picture": false,
      cover: false,
      earlier: false,
      later: true,
      unstack: false,
      hide: false,
    });

    await wrapper.setProps({ canReorder: false });
    await rightClick(wrapper, "second");
    // Nothing to address a reorder by: the three ordering verbs go, the rest
    // — which name a card alone — stay.
    expect(refused(wrapper)).toEqual({
      "open-picture": false,
      cover: true,
      earlier: true,
      later: true,
      unstack: false,
      hide: false,
    });
  });

  it("tears the menu down and back up when another row is right-clicked", async () => {
    const wrapper = makePanel();
    await rightClick(wrapper, "second");
    expect(wrapper.find('[data-testid="member-menu"]').exists()).toBe(true);

    // A right button press fires `mousedown` and `contextmenu` but no
    // `click`, so Vuetify's click-outside does not dismiss the open menu —
    // and its location strategy reads the anchor on OPEN, never on change.
    // Left in place it would stay at the first row's coordinates while its
    // verbs acted on the second. Position is not observable in jsdom; that
    // the menu closes and reopens, which is what re-anchors it, is.
    const reopening = rightClick(wrapper, "third");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="member-menu"]').exists()).toBe(false);
    await reopening;
    expect(wrapper.find('[data-testid="member-menu"]').exists()).toBe(true);

    await wrapper.find('.ctx-item[data-item="unstack"]').trigger("click");
    expect(wrapper.emitted("unstack")).toEqual([["third"]]);
  });

  it("opens on the row named, when the keyboard asks rather than a pointer", async () => {
    const wrapper = makePanel();
    // The grid's roving cursor owns Tab and both ⋯ buttons sit at
    // `tabindex="-1"`, so this method is the only route Unstack and Hide have
    // for somebody not using a pointer.
    wrapper.vm.openMenuAt("third");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="member-menu"]').exists()).toBe(true);
    await wrapper.find('.ctx-item[data-item="hide"]').trigger("click");
    expect(wrapper.emitted("hide")).toEqual([["third"]]);
  });

  it("wears one mark for a whole selected stack, and none on its rows", async () => {
    const whole = MEMBERS.map((entry) => entry.key);
    const wrapper = makePanel({ selectedKeys: whole, selected: true });

    // The band takes the hook the wash and the ring are keyed on.
    expect(band(wrapper).classes()).toContain("stack-panel--selected");
    // In Grid the member mark is the CARD's own, so the cards stop being told
    // they are selected — two marks over one stack is what this replaces.
    for (const card of wrapper.findAllComponents(WorkflowCard)) {
      expect(card.props("selected")).toBe(false);
    }
    // The rows still say so, which is what a screen reader reads.
    for (const key of whole) {
      expect(
        wrapper.find(`[data-key="${key}"]`).attributes("aria-selected"),
      ).toBe("true");
    }
  });

  it("leaves the rows their own marks when only part of the stack is in", async () => {
    // `selected` false is a PARTIAL selection, and then the rows are the only
    // thing that can say which members are in.
    const wrapper = makePanel({
      selectedKeys: ["cover", "third"],
      selected: false,
    });
    expect(band(wrapper).classes()).not.toContain("stack-panel--selected");
    expect(
      wrapper
        .findAllComponents(WorkflowCard)
        .filter((card) => card.props("selected"))
        .map((card) => card.props("card").key),
    ).toEqual(["cover", "third"]);
  });

  it("switches view without touching the selection", async () => {
    const prefs = useWorkflowPrefsStore();
    const wrapper = makePanel({ selectedKeys: ["second"] });
    expect(
      wrapper.find('[data-key="second"]').attributes("aria-selected"),
    ).toBe("true");

    prefs.setStackView("list");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".stack-panel__list").exists()).toBe(true);
    expect(
      wrapper.find('[data-key="second"]').attributes("aria-selected"),
    ).toBe("true");
  });
});

// ---------------------------------------------------------------------------
// The List row's thumbnail trio, read out of the stylesheet.
//
// jsdom has no layout, so a mounted row cannot say how big anything is and
// every assertion about the trio's SHAPE has to come from the source. This is
// `WorkflowCard.test.js`'s harness pointed at this file: the card pins its
// cover's 6:5 and its cells' 4:5 the same way, and that is the pair this row
// got wrong — a 56×36 box made all three cells square, and a centred crop took
// the heads off the ones that are much wider than they are tall.
// ---------------------------------------------------------------------------

// Read from the project root: the suite runs in jsdom, where `import.meta.url`
// is an http: URL and `fileURLToPath` refuses it.
const read = (path) => readFileSync(`${process.cwd()}/${path}`, "utf8");

const readSource = () => read("src/components/panels/StackPanel.vue");

/** Token name → px, from the shipped sheet and this component's local ones. */
function tokens() {
  const values = {};
  const sources = read("src/styles/design-tokens.css") + readSource();
  for (const [, name, value] of sources.matchAll(/(--[\w-]+):\s*(\d+)px;/g)) {
    values[name] = Number(value);
  }
  return values;
}

/** The declarations of one rule in the panel's stylesheet. */
function rule(selector) {
  const css = readSource()
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

/**
 * The cell shapes one arrangement produces, as width ÷ height.
 *
 * `WorkflowCard.test.js`'s own helper, over this panel's strip: the box is 6:5
 * at every count, so a column track worth `c` of the columns' total and `r` of
 * the rows' total is `c·W` wide by `r·(5/6)W` tall. With two rows the first
 * cell spans both of them — the mosaic's big cell — and the second column
 * holds one cell per row.
 */
function cellRatios(count) {
  const box = rule(".stack-panel__thumbs");
  const tracks = rule(`.stack-panel__thumbs--${count}`);
  const [w, h] = box["aspect-ratio"].split("/").map(Number);
  const boxH = h / w; // the box's height as a fraction of its width
  const fr = (key) => tracks[key].split(" ").map((n) => parseFloat(n));
  const cols = fr("grid-template-columns");
  const rows = fr("grid-template-rows");
  const sum = (list) => list.reduce((total, one) => total + one, 0);
  const shape = (col, rowSpan) =>
    cols[col] / sum(cols) / ((rowSpan / sum(rows)) * boxH);

  if (rows.length === 1) return cols.map((_, col) => shape(col, sum(rows)));
  return [shape(0, sum(rows)), ...rows.map((row) => shape(1, row))];
}

describe("StackPanel List thumbnails", () => {
  // The box is 6:5 at every count and only the tracks inside it change, so
  // what each arrangement is worth is the shape of the cells it produces —
  // the same three numbers the card's cover is asserted at (#1456), because
  // the row is the card's arrangement one size down and a row and a card
  // showing the same workflow differently is the bug both fixed.
  //
  // The 56×36 box this replaces was 14:9, at which the mosaic's cells came out
  // 1:1 and 1.06:1 — square, for a library of portrait pictures.
  it.each([
    ["1", [1.2]],
    ["2", [0.6, 0.6]],
    ["3", [0.8, 0.8, 0.8]],
  ])("gives %s picture(s) cells of %s (width ÷ height)", (count, expected) => {
    expect(cellRatios(count)).toEqual(
      expected.map((ratio) => expect.closeTo(ratio, 5)),
    );
  });

  // The crop is computed from `coverCellRatio` (#1465), a hand-copy of this
  // arithmetic in a third file. The card asserts the same agreement against
  // its own stylesheet; both are needed, because these two stylesheets can
  // drift from each other as easily as either can from the map — and a crop
  // computed for the wrong shape renders perfectly and is simply cut in the
  // wrong place.
  it.each(["1", "2", "3"])(
    "crops %s picture(s) to the ratio this strip's tracks produce",
    (count) => {
      for (const ratio of cellRatios(count)) {
        expect(coverCellRatio(Number(count))).toBeCloseTo(ratio, 5);
      }
    },
  );

  it("spans the mosaic's big cell over both rows, and only there", () => {
    // Unscoped, this rule puts every arrangement's first cell across two rows
    // that only the mosaic has.
    expect(
      rule(".stack-panel__thumbs--3 .stack-panel__thumb:first-child"),
    ).toEqual({ "grid-row": "1 / 3" });
  });

  // The height is a real number here, unlike the card's, and it is a NAMED
  // one: the row's height follows the strip, so a bare literal is the panel's
  // row height written where nothing can find it.
  it("takes its height from the panel's own named size, big enough to read", () => {
    const t = tokens();
    const box = rule(".stack-panel__thumbs");

    expect(box.height).toBe("var(--stack-thumb-h)");
    expect(box["aspect-ratio"]).toBe("6 / 5");
    expect(t["--stack-thumb-h"]).toBe(80);
    // The mosaic's small cells are the smallest thing the strip ever draws:
    // half the box's height less the gap. Below ~30px they stop being
    // pictures and become grey chips, which is the defect. At 36 they were 17.
    expect((t["--stack-thumb-h"] - t["--space-1"]) / 2).toBeGreaterThanOrEqual(
      30,
    );
  });

  // The app's shipped crop (`ImageGrid.css`, `utils/squareCrop.js`, and the
  // card's own cover): a centred crop takes the same slice off the top and the
  // bottom, so on a picture of a person the head goes.
  it("crops from the top, not the centre", () => {
    expect(rule(".stack-panel__thumb img")["object-position"]).toBe(
      "top center",
    );
    expect(rule(".stack-panel__thumb img")["object-fit"]).toBe("cover");
  });

  // The strip cannot shrink, so the column it sits in has to clip; without
  // this it paints over Checkpoint on a narrow panel.
  it("clips the workflow column like the two columns beside it", () => {
    expect(rule(".stack-panel__ident").overflow).toBe("hidden");
    expect(rule(".stack-panel__thumbs").flex).toBe("none");
  });

  // The row's waiting cell and the card's are the same grey. At 36px nobody
  // could tell; at 80 two different greys sat side by side on one screen.
  it("paints a waiting cell in the card's own fill", () => {
    expect(rule(".stack-panel__thumb").background).toBe(
      "rgb(var(--v-theme-input-background))",
    );
  });

  // The CSS half of #1465's crop, pinned here as it is on the card:
  // `coverCellStyle` emits percentages, and a percentage only means the CELL
  // if the cell is the containing block and the img is out of flow. Either
  // rule can be deleted with every mounted assertion still green — the strip
  // renders, it simply crops against the wrong box or ignores the offsets.
  it("makes the cell the box the crop is computed against", () => {
    expect(rule(".stack-panel__thumb").position).toBe("relative");
    expect(rule(".stack-panel__thumb").overflow).toBe("hidden");
  });

  it("takes the cropped img out of flow so its offsets apply", () => {
    const img = rule(".stack-panel__thumb img");

    expect(img.position).toBe("absolute");
    expect(img.top).toBe("0");
    expect(img.left).toBe("0");
  });
});

// ── The member menu opens pictures too (#1455) ────────────────────────────
//
// A member card's covers are buttons at `tabindex="-1"`, so this menu is the
// keyboard's only route to them — the same gap the grid's own menu had — and
// the pointer's way of saying WHICH tile it meant.
describe("a member's menu opens its pictures", () => {
  beforeEach(() => useWorkflowPrefsStore().setStackView("grid"));

  const openRow = (wrapper) =>
    wrapper.find('.ctx-item[data-item="open-picture"]');

  it("names and opens the tile the menu was opened on", async () => {
    const wrapper = makePanel();
    const tile = band(wrapper).findAll(
      '.stack-panel__member[data-key="second"] .wf-card__pic',
    )[1];
    await tile.trigger("contextmenu", { clientX: 1, clientY: 2 });
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    expect(openRow(wrapper).find(".ctx-label-text").text()).toBe(
      "Open picture 2 of 2",
    );
    await openRow(wrapper).trigger("click");
    expect(wrapper.emitted("open-picture")?.at(-1)).toEqual([62]);
  });

  it("falls back to the member's cover when no tile was pointed at", async () => {
    const wrapper = makePanel();
    // Shift+F10 has no pointer at all, so "this row's picture" is its cover.
    wrapper.vm.openMenuAt("second");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    expect(openRow(wrapper).find(".ctx-label-text").text()).toBe(
      "Open picture",
    );
    await openRow(wrapper).trigger("click");
    expect(wrapper.emitted("open-picture")?.at(-1)).toEqual([61]);
  });
});
