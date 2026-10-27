// The shortcuts table spells each chord the way the platform's keyboard does,
// so a macOS reader is not taught Ctrl for a key the menus spell ⌘.

import { describe, it, expect, vi, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return { isReadOnly: ref(false) };
});

import ShortcutsDialog from "./ShortcutsDialog.vue";

const AppDialogStub = { template: "<div><slot /></div>" };

function stackRows(agent) {
  vi.stubGlobal("navigator", { userAgent: agent });
  const wrapper = mount(ShortcutsDialog, {
    props: { modelValue: true },
    global: { plugins: [createPinia()], stubs: { AppDialog: AppDialogStub } },
  });
  return wrapper
    .findAll("tr")
    .filter((row) => row.text().includes("In an open stack"))
    .map((row) => row.find("td").text());
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ShortcutsDialog: the stack reorder keys", () => {
  it("spells the modifier ⌘ on Apple platforms", () => {
    expect(stackRows("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)")).toEqual([
      "⌘+← →",
      "⌘+Home",
    ]);
  });

  it("spells it Ctrl elsewhere", () => {
    expect(stackRows("Mozilla/5.0 (X11; Linux x86_64)")).toEqual([
      "Ctrl+← →",
      "Ctrl+Home",
    ]);
  });
});

describe("ShortcutsDialog: the shelf's cut and paste (#2204)", () => {
  it("lists cut, paste and putting a cut back", () => {
    vi.stubGlobal("navigator", { userAgent: "Mozilla/5.0 (X11; Linux x86_64)" });
    const wrapper = mount(ShortcutsDialog, {
      props: { modelValue: true },
      global: { plugins: [createPinia()], stubs: { AppDialog: AppDialogStub } },
    });
    const rows = wrapper
      .findAll("tr")
      .filter((row) => row.text().includes("On Models"))
      .map((row) => row.find("td").text());
    expect(rows).toEqual(["Ctrl+X", "Ctrl+V", "Esc"]);
  });
});
