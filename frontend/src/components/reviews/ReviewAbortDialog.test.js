// ReviewAbortDialog - the Keep / Undo / Cancel confirmation that aborting an
// open review goes through (opened from the sidebar row's menu).
//
// Aborting with decided changes asks first; Cancel calls nothing; Keep calls
// abortSession and Undo calls undoChangesAndAbort, both with that review's id.
// A review with no decided changes aborts at once with no dialog. `closed`
// fires after the store call, so the opener can tell whether its row survived.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";

// The real VDialog: its focus handling (focus into the box on open, pulled
// back in when it leaves) is what these tests check.
const vuetify = createVuetify({ components });

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  isReadOnly: { value: false },
}));

import ReviewAbortDialog from "./ReviewAbortDialog.vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";

const globalOpts = {
  plugins: [vuetify],
  stubs: {
    Tooltip: true,
  },
};

// Seed one open review with `done` decided rows (progress.done is what the
// store reads for the decided-change count when the receipt is not loaded).
function seed(done) {
  const store = useReviewSessionsStore();
  store.sessions = [
    { id: 5, tag: "hair", scope: {}, stats: { found: 4 }, progress: { done } },
  ];
  store.archived = [];
  return store;
}

function mountDialog() {
  return mount(ReviewAbortDialog, { global: globalOpts, attachTo: document.body });
}

// The dialog is AppDialog, a v-dialog teleported to the body: its footer
// buttons are found by their words.
function dialog() {
  return document.querySelector(".app-dialog");
}

// Shown means its overlay is active: the box can linger through the leave
// transition, which the test renderer runs for real.
function isShown() {
  return document.querySelector(".v-overlay--active .app-dialog") != null;
}

function action(label) {
  return [...document.querySelectorAll(".app-dialog__footer button")].find((b) =>
    b.textContent.trim().startsWith(label),
  );
}

beforeEach(() => {
  setActivePinia(createPinia());
  document.body.innerHTML = "";
});

describe("ReviewAbortDialog", () => {
  it("asks Keep / Undo / Cancel with the decided change count", async () => {
    seed(2);
    const w = mountDialog();
    await w.vm.open(5);
    await flushPromises();

    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(action("Keep").textContent).toContain("Keep 2 changes");
    expect(action("Undo").textContent).toContain("Undo 2 changes");
    w.unmount();
  });

  it("Keep calls abortSession with that review's id, then reports closed", async () => {
    const store = seed(2);
    const abort = vi.spyOn(store, "abortSession").mockResolvedValue();
    const undo = vi.spyOn(store, "undoChangesAndAbort").mockResolvedValue();
    const w = mountDialog();
    await w.vm.open(5);
    await flushPromises();

    action("Keep").click();
    await flushPromises();

    expect(abort).toHaveBeenCalledTimes(1);
    expect(abort).toHaveBeenCalledWith(5);
    expect(undo).not.toHaveBeenCalled();
    expect(w.emitted("closed")).toHaveLength(1);
    expect(isShown()).toBe(false);
    w.unmount();
  });

  it("Undo calls undoChangesAndAbort with that review's id", async () => {
    const store = seed(2);
    const abort = vi.spyOn(store, "abortSession").mockResolvedValue();
    const undo = vi.spyOn(store, "undoChangesAndAbort").mockResolvedValue();
    const w = mountDialog();
    await w.vm.open(5);
    await flushPromises();

    action("Undo").click();
    await flushPromises();

    expect(undo).toHaveBeenCalledTimes(1);
    expect(undo).toHaveBeenCalledWith(5);
    expect(abort).not.toHaveBeenCalled();
    expect(w.emitted("closed")).toHaveLength(1);
    w.unmount();
  });

  it("Cancel calls neither store abort and reports closed", async () => {
    const store = seed(2);
    const abort = vi.spyOn(store, "abortSession").mockResolvedValue();
    const undo = vi.spyOn(store, "undoChangesAndAbort").mockResolvedValue();
    const w = mountDialog();
    await w.vm.open(5);
    await flushPromises();

    action("Cancel").click();
    await flushPromises();

    expect(abort).not.toHaveBeenCalled();
    expect(undo).not.toHaveBeenCalled();
    expect(w.emitted("closed")).toHaveLength(1);
    expect(isShown()).toBe(false);
    w.unmount();
  });

  it("Esc cancels it, and goes no further than the dialog", async () => {
    const store = seed(2);
    const abort = vi.spyOn(store, "abortSession").mockResolvedValue();
    const w = mountDialog();
    await w.vm.open(5);
    await flushPromises();
    const beyond = vi.fn();
    window.addEventListener("keydown", beyond);
    document.activeElement.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    await flushPromises();
    window.removeEventListener("keydown", beyond);

    expect(isShown()).toBe(false);
    expect(abort).not.toHaveBeenCalled();
    expect(w.emitted("closed")).toHaveLength(1);
    expect(beyond).not.toHaveBeenCalled();
    w.unmount();
  });

  it("opens with focus on Cancel, so Enter cannot discard by default", async () => {
    seed(1);
    const w = mountDialog();
    await w.vm.open(5);
    await flushPromises();

    expect(document.activeElement).toBe(action("Cancel"));
    w.unmount();
  });

  it("aborts straight away with no dialog when nothing was decided", async () => {
    const store = seed(0);
    const abort = vi.spyOn(store, "abortSession").mockResolvedValue();
    const w = mountDialog();
    await w.vm.open(5);
    await flushPromises();

    expect(abort).toHaveBeenCalledWith(5);
    expect(dialog()).toBeNull();
    expect(w.emitted("closed")).toHaveLength(1);
    w.unmount();
  });

  it("focus that moves outside the dialog is pulled back in", async () => {
    seed(2);
    const w = mountDialog();
    await w.vm.open(5);
    await flushPromises();

    const outside = document.createElement("button");
    outside.textContent = "outside";
    document.body.appendChild(outside);
    outside.focus();

    expect(dialog().contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(outside);
    expect(w.emitted("closed")).toBeUndefined();
    w.unmount();
  });
});
