// AppBarButton - the flat bar dialect (docs/design/buttons.md).

import { describe, it, expect, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: {
    name: "v-icon",
    props: ["size"],
    template: '<i :data-size="size"><slot /></i>',
  },
  VTooltip: {
    name: "v-tooltip",
    props: ["activator"],
    template: '<span class="tip" :data-activator="activator"><slot /></span>',
  },
}));

import AppBarButton from "./AppBarButton.vue";

describe("AppBarButton", () => {
  it("is a square icon-only control with a 24px glyph when it has no label", () => {
    const w = mount(AppBarButton, { props: { icon: "close" } });
    const btn = w.find("button");
    expect(btn.classes()).toContain("bar-btn--icon");
    expect(btn.find("i").attributes("data-size")).toBe("24");
    expect(btn.find("i").text()).toBe("mdi-close");
  });

  it("takes an 18px glyph beside a label and is not icon-only", () => {
    const w = mount(AppBarButton, {
      props: { icon: "mdi-sort" },
      slots: { default: "Date added" },
    });
    expect(w.find("button").classes()).not.toContain("bar-btn--icon");
    expect(w.find("i").attributes("data-size")).toBe("18");
    expect(w.find("i").text()).toBe("mdi-sort");
  });

  it("maps shape and state props onto the bar-btn family", () => {
    const w = mount(AppBarButton, {
      props: {
        icon: "delete",
        shape: "round",
        danger: true,
        open: true,
        active: true,
      },
    });
    const c = w.find("button").classes();
    for (const cls of [
      "bar-btn",
      "bar-btn--round",
      "bar-btn--danger",
      "bar-btn--open",
      "bar-btn--active",
    ]) {
      expect(c).toContain(cls);
    }
  });

  it("overlays a badge on the icon, and not a null one", async () => {
    const w = mount(AppBarButton, { props: { icon: "filter", badge: 3 } });
    expect(w.find(".bar-filter-badge").text()).toBe("3");
    await w.setProps({ badge: null });
    expect(w.find(".bar-filter-badge").exists()).toBe(false);
  });

  it("is busy and disabled while pending, and swaps the glyph for the spinner", async () => {
    const w = mount(AppBarButton, { props: { icon: "close" } });
    await w.setProps({ loading: true });
    const btn = w.find("button");
    expect(btn.attributes("disabled")).toBeDefined();
    expect(btn.attributes("aria-busy")).toBe("true");
    expect(btn.find("i").text()).toBe("mdi-loading");
  });

  it("forwards attributes such as a menu activator's to the button", () => {
    const w = mount(AppBarButton, {
      props: { icon: "filter" },
      attrs: { "aria-label": "Filters", "aria-expanded": "false" },
    });
    expect(w.find("button").attributes("aria-label")).toBe("Filters");
    expect(w.find("button").attributes("aria-expanded")).toBe("false");
  });

  // One string, both jobs (buttons.md, "Tooltips"): the tip of an icon-only
  // control is its accessible name too, so the two cannot drift.
  it("names an icon-only button from its tooltip and renders the tip", async () => {
    const w = mount(AppBarButton, {
      props: { icon: "close", tooltip: "Close" },
    });
    await flushPromises();
    const btn = w.find("button");
    expect(btn.attributes("aria-label")).toBe("Close");
    await btn.trigger("mouseenter");
    expect(btn.attributes("title")).toBeUndefined();
    const tip = w.find(".tip");
    expect(tip.text()).toBe("Close");
    expect(tip.attributes("data-activator")).toBe("parent");
    // The tip is the name, so it must not also describe: said twice otherwise.
    expect(btn.attributes("aria-description")).toBeUndefined();
  });

  it("lets an explicit aria-label win over the tooltip", () => {
    const w = mount(AppBarButton, {
      props: { icon: "close", tooltip: "Dismiss v1.2 update alert" },
      attrs: { "aria-label": "Dismiss update alert" },
    });
    expect(w.find("button").attributes("aria-label")).toBe(
      "Dismiss update alert",
    );
  });

  it("describes rather than renames a labelled button", async () => {
    const w = mount(AppBarButton, {
      props: { icon: "sort", tooltip: "Sort order (S)" },
      slots: { default: "Date added" },
    });
    await flushPromises();
    expect(w.find("button").attributes("aria-label")).toBeUndefined();
    expect(w.find("button").attributes("aria-description")).toBe(
      "Sort order (S)",
    );
  });

  it("renders no tip without a tooltip", () => {
    const w = mount(AppBarButton, { props: { icon: "close" } });
    expect(w.find(".tip").exists()).toBe(false);
  });
});
