// AppButton - the pending (busy) state added for #647.
//
// A submit button that stays live while its create request is in flight lets a
// double-click post twice. `loading` is the shared visual half of the fix
// (`useSubmitGuard` is the behavioural half): it disables the button, marks it
// busy for assistive tech, and swaps the leading icon for the app's spinner.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
}));

import AppButton from "./AppButton.vue";

function mountButton(props = {}) {
  return mount(AppButton, { props, slots: { default: "Save" } });
}

describe("AppButton loading state", () => {
  it("is enabled and unmarked by default", () => {
    const w = mountButton();
    expect(w.find("button").attributes("disabled")).toBeUndefined();
    expect(w.find("button").attributes("aria-busy")).toBeUndefined();
  });

  // The whole point: a pending button cannot be clicked a second time.
  it("disables itself and reports busy while loading", () => {
    const w = mountButton({ loading: true });
    expect(w.find("button").attributes("disabled")).toBeDefined();
    expect(w.find("button").attributes("aria-busy")).toBe("true");
  });

  it("shows the spinner in place of the leading icon while loading", () => {
    const w = mountButton({ iconLeft: "check", loading: true });
    const icon = w.find("i");
    expect(icon.text()).toBe("mdi-loading");
    expect(icon.classes()).toContain("mdi-spin");
  });

  it("shows a spinner even without a leading icon", () => {
    const w = mountButton({ loading: true });
    expect(w.find("i").exists()).toBe(true);
  });

  it("renders the plain icon and no spin class when idle", () => {
    const w = mountButton({ iconLeft: "check" });
    const icon = w.find("i");
    expect(icon.text()).toBe("mdi-check");
    expect(icon.classes()).not.toContain("mdi-spin");
  });

  // The label must not move under the cursor mid-click.
  it("keeps its label while loading", () => {
    expect(mountButton({ loading: true }).text()).toContain("Save");
  });

  it("stays disabled when disabled without loading", () => {
    const w = mountButton({ disabled: true });
    expect(w.find("button").attributes("disabled")).toBeDefined();
    expect(w.find("button").attributes("aria-busy")).toBeUndefined();
  });
});

// A natively-disabled button cannot hold focus, so going pending drops focus to
// <body>, stranding a keyboard user far from where they were - permanently if
// the request fails and the form stays open.
describe("AppButton focus continuity", () => {
  it("takes focus back when the request settles", async () => {
    // Stands in for wherever the browser sends focus when the button disables.
    const elsewhere = document.createElement("input");
    document.body.appendChild(elsewhere);
    const w = mount(AppButton, {
      props: { loading: false },
      slots: { default: "Save" },
      attachTo: document.body,
    });
    const el = w.find("button").element;
    el.focus();
    expect(document.activeElement).toBe(el);

    // The watcher runs pre-flush, so it records "this button had focus" before
    // the DOM is disabled. jsdom does not drop focus off a disabled element the
    // way a browser does, so the drop - the whole reason this exists - is
    // emulated by moving focus away.
    await w.setProps({ loading: true });
    elsewhere.focus();
    expect(document.activeElement).toBe(elsewhere);

    await w.setProps({ loading: false });
    await nextTick();
    expect(document.activeElement).toBe(el);
    w.unmount();
    elsewhere.remove();
  });

  it("does not steal focus it never had", async () => {
    const other = document.createElement("input");
    document.body.appendChild(other);
    const w = mount(AppButton, {
      props: { loading: false },
      slots: { default: "Save" },
      attachTo: document.body,
    });
    other.focus();

    await w.setProps({ loading: true });
    await w.setProps({ loading: false });
    await nextTick();

    expect(document.activeElement).toBe(other);
    w.unmount();
    other.remove();
  });
});
