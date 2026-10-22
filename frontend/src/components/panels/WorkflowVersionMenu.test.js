// The version menu of the Workflow tab's head (#1850), and what can be done
// with an earlier version from it (#1792).
//
// What can silently go wrong here: a reason re-worded on the way to the row,
// the current version said by colour alone, and focus left on the page body
// when the menu closes over the row that held it.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { h, nextTick, watch } from "vue";
import { mount } from "@vue/test-utils";

import WorkflowVersionMenu, { versionDay } from "./WorkflowVersionMenu.vue";

// Vuetify is not installed in the test app, so `v-menu` is stubbed with the
// two behaviours the component relies on: the activator's click toggles it,
// and the rows are in the page only while it is open.
// As `v-menu` does, an arrow on the activator opens it too, before any other
// keydown handler on the same button runs, and `afterEnter` follows once the
// rows are drawn.
const VMenuStub = {
  name: "VMenu",
  props: { modelValue: { type: Boolean, default: false } },
  emits: ["update:modelValue", "afterEnter"],
  setup(props, { slots, emit }) {
    watch(
      () => props.modelValue,
      (open) => open && nextTick(() => emit("afterEnter")),
    );
    return () =>
      h("div", { class: "v-menu-stub" }, [
        slots.activator?.({
          props: {
            onClick: () => emit("update:modelValue", !props.modelValue),
            onKeydown: (event) => {
              if (["ArrowDown", "ArrowUp"].includes(event.key)) {
                emit("update:modelValue", true);
              }
            },
          },
        }),
        props.modelValue ? slots.default?.() : null,
      ]);
  },
};

// Carries its text on the button it sits in: the real tip needs Vuetify.
const TooltipStub = {
  name: "Tooltip",
  props: ["text"],
  template: '<span class="tip" :data-text="text" />',
};

const ROWS = [
  { version: 3, created_at: "2026-10-02T12:00:00+00:00", reason: "models changed", current: true },
  { version: 2, created_at: "2026-09-28T12:00:00+00:00", reason: "LoRAs edited", current: false },
  { version: 1, created_at: "2026-09-20T12:00:00+00:00", reason: null, current: false },
];

const mounted = [];
afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  vi.useRealTimers();
});

beforeEach(() => {
  // Only the clock's reading: the menu's `nextTick` and the tests' own awaits
  // run on real timers.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00+00:00"));
});

function mountMenu(props = {}) {
  const wrapper = mount(WorkflowVersionMenu, {
    props: {
      number: 3,
      at: "2026-10-02T12:00:00+00:00",
      versions: ROWS,
      ...props,
    },
    attachTo: document.body,
    global: {
      stubs: {
        VMenu: VMenuStub,
        VIcon: true,
        Tooltip: TooltipStub,
        ComfyuiIcon: true,
      },
    },
  });
  mounted.push(wrapper);
  return wrapper;
}

const chip = (wrapper) => wrapper.find('[data-testid="wftab-version"]');
const row = (wrapper, number) => wrapper.find(`[data-version="${number}"]`);
const text = (el) => el.text().replace(/\s+/g, " ");
const meta = (wrapper, number) => row(wrapper, number).find(".ctx-meta").text();
const name = (wrapper, number) => row(wrapper, number).attributes("aria-label");

async function openMenu(wrapper) {
  await chip(wrapper).trigger("click");
}

const actions = (wrapper) => wrapper.find('[data-testid="wftab-version-actions"]');
const action = (wrapper, verb) => wrapper.find(`[data-action="${verb}"]`);

describe("versionDay", () => {
  const now = new Date("2026-10-05T12:00:00+00:00");

  it("says today for today, and day and month for this year", () => {
    expect(versionDay("2026-10-05T09:00:00+00:00", now)).toBe("today");
    expect(versionDay("2026-10-02T12:00:00+00:00", now)).toMatch(
      /^(2 Oct|Oct 2)$/,
    );
  });

  it("carries the year once it is not this one", () => {
    expect(versionDay("2025-10-02T12:00:00+00:00", now)).toContain("2025");
  });

  it("reads a stamp with no zone as UTC", () => {
    // Either side of midnight, so a zone east or west of UTC tells a stamp
    // read as local time from one read as UTC. (In UTC itself they agree.)
    for (const clock of ["00:30:00", "23:30:00"]) {
      expect(versionDay(`2026-09-02T${clock}`, now)).toBe(
        versionDay(`2026-09-02T${clock}Z`, now),
      );
    }
  });

  it("is empty for a stamp that is missing or will not parse", () => {
    expect(versionDay(null, now)).toBe("");
    expect(versionDay("not a date", now)).toBe("");
  });
});

describe("the chip", () => {
  it("says the version and its day, and that it opens a menu", () => {
    const wrapper = mountMenu();
    expect(text(chip(wrapper))).toMatch(/^Version 3 · (2 Oct|Oct 2)$/);
    expect(chip(wrapper).attributes("aria-haspopup")).toBe("menu");
    expect(chip(wrapper).attributes("aria-expanded")).toBe("false");
    // The chip says what the menu is for.
    expect(chip(wrapper).find(".tip").attributes("data-text")).toBe(
      "Go back to, duplicate or open an earlier version",
    );
  });

  it("says today for a version stored today", () => {
    const wrapper = mountMenu({ number: 4, at: "2026-10-05T08:00:00+00:00" });
    expect(text(chip(wrapper))).toBe("Version 4 · today");
  });

  it("is the number alone when the day is unknown", () => {
    const wrapper = mountMenu({ at: null });
    expect(text(chip(wrapper))).toBe("Version 3");
  });

  it("says it is open, and asks for the list, when opened", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(chip(wrapper).attributes("aria-expanded")).toBe("true");
    expect(wrapper.emitted("open")).toHaveLength(1);
  });
});

describe("the rows", () => {
  it("are in the order given, each with its day and the reason as sent", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    const rows = wrapper.findAll("[data-version]");
    expect(rows.map((entry) => entry.attributes("data-version"))).toEqual([
      "3",
      "2",
      "1",
    ]);
    expect(row(wrapper, 3).find(".ctx-label-text").text()).toBe("Version 3");
    expect(meta(wrapper, 3)).toMatch(/^(2 Oct|Oct 2) · models changed$/);
    expect(meta(wrapper, 2)).toMatch(/^(28 Sep|Sep 28) · LoRAs edited$/);
    // Said whole: the two spans alone would be read run together.
    expect(name(wrapper, 3)).toMatch(/^Version 3, (2 Oct|Oct 2), models changed$/);
  });

  it("print a reason they have never heard of exactly as the server sent it", async () => {
    const wrapper = mountMenu({
      versions: [
        { ...ROWS[0], reason: "from an assistant" },
        { ...ROWS[1], reason: "as PixlStash first read it" },
      ],
    });
    await openMenu(wrapper);
    expect(meta(wrapper, 3)).toMatch(/ · from an assistant$/);
    expect(meta(wrapper, 2)).toMatch(/ · as PixlStash first read it$/);
  });

  it("are the number and the day alone for a version with no reason", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(meta(wrapper, 1)).toMatch(/^(20 Sep|Sep 20)$/);
    expect(name(wrapper, 1)).toMatch(/^Version 1, (20 Sep|Sep 20)$/);
  });

  it("say which version is current to a screen reader, not by the check alone", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(row(wrapper, 3).attributes("role")).toBe("menuitemradio");
    expect(row(wrapper, 3).attributes("aria-checked")).toBe("true");
    // An earlier one is not a choice: it opens what can be done with it.
    for (const number of [2, 1]) {
      expect(row(wrapper, number).attributes("role")).toBe("menuitem");
      expect(row(wrapper, number).attributes("aria-haspopup")).toBe("menu");
      expect(row(wrapper, number).attributes("aria-checked")).toBeUndefined();
    }
    // The leading slot is on every row, the check in the current one only.
    expect(wrapper.findAll("[data-version] .ctx-icon")).toHaveLength(3);
    expect(row(wrapper, 3).find("v-icon-stub.ctx-icon").exists()).toBe(true);
    expect(row(wrapper, 2).find("v-icon-stub.ctx-icon").exists()).toBe(false);
  });

  it("open an earlier version's actions on a click, and do nothing yet", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(actions(wrapper).exists()).toBe(false);
    await row(wrapper, 2).trigger("click");
    expect(row(wrapper, 2).attributes("aria-expanded")).toBe("true");
    expect(actions(wrapper).attributes("aria-label")).toBe("Version 2");
    expect(wrapper.emitted("go-back")).toBeUndefined();
    // Still open: going back is never one click.
    expect(row(wrapper, 2).exists()).toBe(true);
  });

  it.each([
    ["go-back", "Go back to this version"],
    ["duplicate", "Duplicate"],
    ["open-comfyui", "Open in ComfyUI"],
  ])("ask for %s on the version chosen, and close", async (verb, label) => {
    const wrapper = mountMenu({ canOpenComfyui: true });
    await openMenu(wrapper);
    await row(wrapper, 1).trigger("click");
    expect(action(wrapper, verb).text()).toBe(label);
    await action(wrapper, verb).trigger("click");
    expect(wrapper.emitted(verb)).toEqual([[ROWS[2]]]);
    for (const other of ["go-back", "duplicate", "open-comfyui"]) {
      if (other !== verb) expect(wrapper.emitted(other)).toBeUndefined();
    }
    expect(row(wrapper, 1).exists()).toBe(false);
  });

  it("offer no Open in ComfyUI where there is none to open", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    await row(wrapper, 2).trigger("click");
    expect(action(wrapper, "duplicate").exists()).toBe(true);
    expect(action(wrapper, "open-comfyui").exists()).toBe(false);
  });

  it("only close on the version the workflow is on", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    await row(wrapper, 3).trigger("click");
    expect(wrapper.emitted("go-back")).toBeUndefined();
    expect(row(wrapper, 3).exists()).toBe(false);
  });

  it("say the list is being read, or could not be, instead of an empty menu", async () => {
    const wrapper = mountMenu({ versions: null });
    await openMenu(wrapper);
    const unread = wrapper.find('[data-testid="wftab-version-unread"]');
    expect(unread.text()).toBe("Reading the versions…");
    expect(unread.attributes("aria-disabled")).toBe("true");
    await wrapper.setProps({ failed: true });
    expect(unread.text()).toBe("Could not read the versions just now.");
  });
});

describe("focus", () => {
  it("goes back to the chip when a row closes the menu", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    await row(wrapper, 2).trigger("click");
    action(wrapper, "go-back").element.focus();
    expect(document.activeElement).toBe(action(wrapper, "go-back").element);
    await action(wrapper, "go-back").trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(chip(wrapper).element);
  });

  it("stays on another control the closing click landed on", async () => {
    const elsewhere = document.createElement("button");
    document.body.appendChild(elsewhere);
    try {
      const wrapper = mountMenu();
      await openMenu(wrapper);
      elsewhere.focus();
      // The click outside, as `v-menu` reports it.
      wrapper.findComponent(VMenuStub).vm.$emit("update:modelValue", false);
      await wrapper.vm.$nextTick();
      await wrapper.vm.$nextTick();
      expect(document.activeElement).toBe(elsewhere);
    } finally {
      elsewhere.remove();
    }
  });

  it("goes to the first row when ArrowDown opened the menu, and the last for ArrowUp", async () => {
    const wrapper = mountMenu();
    chip(wrapper).element.focus();
    await chip(wrapper).trigger("keydown", { key: "ArrowDown" });
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(row(wrapper, 3).element);

    await row(wrapper, 3).trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    await chip(wrapper).trigger("keydown", { key: "ArrowUp" });
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(row(wrapper, 1).element);
  });

  it("stays on the chip when a click opened the menu", async () => {
    const wrapper = mountMenu();
    chip(wrapper).element.focus();
    await openMenu(wrapper);
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(chip(wrapper).element);
    // An arrow pressed in the open menu is the menu's own business, and is
    // forgotten with it: the next click-open is still a click-open.
    await chip(wrapper).trigger("keydown", { key: "ArrowDown" });
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(chip(wrapper).element);
    await openMenu(wrapper);
    await wrapper.vm.$nextTick();
    await openMenu(wrapper);
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(row(wrapper, 3).exists()).toBe(true);
    expect(document.activeElement).toBe(chip(wrapper).element);
  });

  it("lands on the menu itself when an arrow opened it with the list unread", async () => {
    const wrapper = mountMenu({ versions: null });
    chip(wrapper).element.focus();
    await chip(wrapper).trigger("keydown", { key: "ArrowDown" });
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="wftab-version-menu"]').element,
    );
  });

  it("opens an earlier version's actions on its first row with →, and ← goes back", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    row(wrapper, 2).element.focus();
    await row(wrapper, 2).trigger("keydown", { key: "ArrowRight" });
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(action(wrapper, "go-back").element);
    await actions(wrapper).trigger("keydown", { key: "ArrowDown" });
    expect(document.activeElement).toBe(action(wrapper, "duplicate").element);
    await action(wrapper, "duplicate").trigger("keydown", { key: "ArrowLeft" });
    expect(actions(wrapper).exists()).toBe(false);
    expect(document.activeElement).toBe(row(wrapper, 2).element);
    expect(wrapper.emitted("go-back")).toBeUndefined();
  });

  it("can be put on the chip from outside", () => {
    const wrapper = mountMenu();
    wrapper.vm.focus();
    expect(document.activeElement).toBe(chip(wrapper).element);
  });
});
