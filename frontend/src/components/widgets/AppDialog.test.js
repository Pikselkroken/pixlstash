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
