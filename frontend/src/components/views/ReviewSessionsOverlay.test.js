// The New-review dialog's scope prefill. A review started from a board row must
// cover what that row's numbers describe - the board's scope - never whatever
// happens to be selected in the grid, or a high-Priority tag opens a review of
// 3 pictures that finds nothing.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { h } from "vue";

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

import ReviewSessionsOverlay from "./ReviewSessionsOverlay.vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useSelectionStore } from "../../stores/useSelectionStore";

const Blank = { name: "Blank", render: () => h("div") };
const BoardStub = {
  name: "TagHealthBoard",
  emits: ["start-review"],
  render: () => h("div"),
};
const DialogStub = {
  name: "NewReviewDialog",
  props: ["preset", "initialScope"],
  render: () => h("div"),
};

const UNSCOPED = { projectId: null, setId: null, characterId: null };

async function mountOverlay() {
  const w = mount(ReviewSessionsOverlay, {
    global: {
      stubs: {
        Tooltip: Blank,
        "v-icon": Blank,
        ReviewSessionView: Blank,
        ReviewArchivedReceipt: Blank,
        RailTags: Blank,
        TagHealthBoard: BoardStub,
        NewReviewDialog: DialogStub,
      },
    },
  });
  await flushPromises();
  return w;
}

describe("ReviewSessionsOverlay - New-review scope prefill", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    // A set is selected in the grid behind the overlay.
    useSelectionStore().selectedSet = 42;
  });

  it("a board row's Start review takes the unscoped board, not the grid selection", async () => {
    const w = await mountOverlay();
    w.findComponent(BoardStub).vm.$emit("start-review", "watermark");
    await flushPromises();

    const dialog = w.findComponent(DialogStub);
    expect(dialog.props("preset")).toBe("watermark");
    expect(dialog.props("initialScope")).toEqual(UNSCOPED);
    w.unmount();
  });

  it("a board row's Start review takes a scoped board's scope", async () => {
    const w = await mountOverlay();
    useReviewSessionsStore().healthScope = { ...UNSCOPED, projectId: 3 };
    w.findComponent(BoardStub).vm.$emit("start-review", "watermark");
    await flushPromises();

    expect(w.findComponent(DialogStub).props("initialScope")).toEqual({
      ...UNSCOPED,
      projectId: 3,
    });
    w.unmount();
  });

  it("the bare New review on an unscoped board still prefills from the grid selection", async () => {
    const w = await mountOverlay();
    useReviewSessionsStore().newReviewTick += 1;
    await flushPromises();

    expect(w.findComponent(DialogStub).props("initialScope")).toEqual({
      ...UNSCOPED,
      setId: 42,
    });
    w.unmount();
  });
});
