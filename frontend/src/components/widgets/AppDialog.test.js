// AppDialog - the dialog keyboard contract (owner decision, 2026-07-29).
//
// Escape dismisses and plain Enter accepts, handled on the dialog's own
// subtree so no page-level Escape owner is consulted first. Enter is inert
// wherever the key already has a meaning: multiline fields, buttons and links
// (native activation must win, so Enter on a focused Cancel cancels), selects,
// summaries, and anything that already handled the event.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { h } from "vue";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
}));

import AppDialog from "./AppDialog.vue";

const BodyStub = {
  template: `
    <div>
      <input class="txt" type="text" />
      <textarea class="area"></textarea>
      <button class="btn" type="button">Cancel</button>
      <div class="row" role="radio" tabindex="0"></div>
    </div>
  `,
};

function mountDialog(props = {}) {
  return mount(AppDialog, {
    props: { open: true, title: "T", ...props },
    slots: { default: BodyStub },
  });
}

describe("AppDialog keyboard contract", () => {
  it("emits close on Escape from anywhere inside", async () => {
    const w = mountDialog();
    await w.find(".txt").trigger("keydown", { key: "Escape" });
    expect(w.emitted("close")).toBeTruthy();
  });

  it("does not close on Escape while persistent", async () => {
    const w = mountDialog({ persistent: true });
    await w.find(".txt").trigger("keydown", { key: "Escape" });
    expect(w.emitted("close")).toBeFalsy();
  });

  it("emits accept on plain Enter from a single-line input", async () => {
    const w = mountDialog();
    await w.find(".txt").trigger("keydown", { key: "Enter" });
    expect(w.emitted("accept")).toBeTruthy();
  });

  it("leaves Enter alone in a textarea - newlines beat accept", async () => {
    const w = mountDialog();
    await w.find(".area").trigger("keydown", { key: "Enter" });
    expect(w.emitted("accept")).toBeFalsy();
  });

  it("leaves Enter alone on a button - native activation wins", async () => {
    const w = mountDialog();
    await w.find(".btn").trigger("keydown", { key: "Enter" });
    expect(w.emitted("accept")).toBeFalsy();
  });

  it("respects a descendant that already handled Enter", async () => {
    const w = mountDialog();
    const row = w.find(".row").element;
    row.addEventListener("keydown", (e) => e.preventDefault());
    await w.find(".row").trigger("keydown", { key: "Enter" });
    expect(w.emitted("accept")).toBeFalsy();
  });

  it("ignores modified Enter - Ctrl+Enter stays a dialog-local shortcut", async () => {
    const w = mountDialog();
    await w.find(".txt").trigger("keydown", { key: "Enter", ctrlKey: true });
    expect(w.emitted("accept")).toBeFalsy();
  });
});

describe("AppDialog backdrop", () => {
  it("closes once when Vuetify closes it, and ignores click:outside itself", async () => {
    // Vuetify emits update:model-value=false on a scrim click or Escape
    // exactly when the dialog is not persistent, and click:outside always.
    // Only the former is wired, so a non-persistent dialog closes once and a
    // persistent one - the folder-mapping wizard - not at all on a stray
    // click; wiring both used to close the former twice and the latter once.
    const w = mountDialog();
    const dialog = w.findComponent({ name: "v-dialog" });
    await dialog.vm.$emit("click:outside");
    await dialog.vm.$emit("update:modelValue", false);
    expect(w.emitted("close")).toHaveLength(1);

    const p = mountDialog({ persistent: true });
    await p.findComponent({ name: "v-dialog" }).vm.$emit("click:outside");
    expect(p.emitted("close")).toBeUndefined();
  });
});
