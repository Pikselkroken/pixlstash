// One user gesture, one undo step.
//
// A tag gesture in the lightbox and in the toolbar tag panel fans out over
// several requests: deleting a chip is a `remove_all` AND a `reject`, a bulk
// drag is N removals AND N rejects, a confirm-on-all is N confirms. Each
// request records its own operation, so before this the first Ctrl+Z reverted
// only the ledger and read as a no-op.
//
// The fix is a per-gesture correlation id (`X-Operation-Batch-Id`, minted by
// `newOperationBatchId`) shared by every request of one gesture; the backend
// stores it as the operations' `batch_id` and undo expands to the whole batch
// (docs/backend_architecture.md §21.2). What these tests pin is the client half:
// every request of one gesture carries the SAME id, and different gestures
// never share one.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

const tagsApi = vi.hoisted(() => ({
  listTags: vi.fn(async () => []),
  addPictureTag: vi.fn(async () => ({})),
  removePictureTag: vi.fn(async () => ({})),
  bulkFetchTags: vi.fn(async () => []),
  removeTagEverywhere: vi.fn(async () => ({})),
  listTagPredictions: vi.fn(async () => []),
  confirmTagPrediction: vi.fn(async () => ({})),
  rejectTagPrediction: vi.fn(async () => ({})),
}));

vi.mock("../../api/tags", () => tagsApi);
vi.mock("../../api/pictures", () => ({
  resetPicturesTags: vi.fn(async () => ({})),
}));
vi.mock("../../api/taggers", () => ({
  listTaggers: vi.fn(async () => ({ plugins: [] })),
}));
vi.mock("../../api/config", () => ({ getUserConfig: vi.fn(async () => ({})) }));
vi.mock("../../api/users", () => ({ getPenalisedTags: vi.fn(async () => []) }));

import OverlayTagsPanel from "../views/OverlayTagsPanel.vue";
import TbTagPanel from "./TbTagPanel.vue";

const STUBS = { "v-icon": true, "v-btn": true, "v-progress-circular": true };

/** Every `batchId` the mocked api calls were given, in call order. */
function batchIdsOf(...mocks) {
  return mocks.flatMap((mock) =>
    mock.mock.calls.map((args) => args[args.length - 1]?.batchId),
  );
}

beforeEach(() => {
  Object.values(tagsApi).forEach((mock) => mock.mockClear());
});

describe("the lightbox chip delete", () => {
  it("sends one gesture batch id on the removal and the reject it makes durable", async () => {
    const wrapper = mount(OverlayTagsPanel, {
      props: {
        image: { id: 7, tags: [{ id: 1, tag: "sunset" }] },
        backendUrl: "http://backend",
      },
      global: { stubs: STUBS },
    });
    await flushPromises();

    await wrapper.find("button.tag-delete-btn").trigger("click");
    await flushPromises();

    expect(tagsApi.removeTagEverywhere).toHaveBeenCalledTimes(1);
    expect(tagsApi.rejectTagPrediction).toHaveBeenCalledTimes(1);
    const ids = batchIdsOf(
      tagsApi.removeTagEverywhere,
      tagsApi.rejectTagPrediction,
    );
    expect(new Set(ids).size).toBe(1);
    // The namespace the backend accepts from a client; `srv-` is the server's.
    expect(ids[0]).toMatch(/^cli-[A-Za-z0-9_-]{4,76}$/);
    wrapper.unmount();
  });

  it("gives a second chip delete its own id - two gestures, two undo steps", async () => {
    const wrapper = mount(OverlayTagsPanel, {
      props: {
        image: {
          id: 7,
          tags: [
            { id: 1, tag: "sunset" },
            { id: 2, tag: "beach" },
          ],
        },
        backendUrl: "http://backend",
      },
      global: { stubs: STUBS },
    });
    await flushPromises();

    const buttons = wrapper.findAll("button.tag-delete-btn");
    await buttons[0].trigger("click");
    await flushPromises();
    await buttons[1].trigger("click");
    await flushPromises();

    const ids = batchIdsOf(tagsApi.removeTagEverywhere);
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);
    wrapper.unmount();
  });
});

describe("the tag panel's bulk gestures", () => {
  async function mountPanel() {
    tagsApi.bulkFetchTags.mockResolvedValue([
      { id: 1, tags: [{ id: 11, tag: "sunset" }] },
      { id: 2, tags: [{ id: 22, tag: "sunset" }] },
    ]);
    // A different tag from the one already on every picture: a prediction for a
    // tag confirmed everywhere is filtered out of the aggregation.
    tagsApi.listTagPredictions.mockResolvedValue({
      tag_predictions: [
        {
          tag: "beach",
          confidence: 0.9,
          status: "REJECTED",
          model_version: "test-v1",
        },
      ],
      meta: { acceptance_threshold: 0.95, label_thresholds: {} },
    });
    const wrapper = mount(TbTagPanel, {
      props: {
        backendUrl: "http://backend",
        open: true,
        selectedCount: 2,
        selectedImageIds: [1, 2],
        allGridImages: [{ id: 1 }, { id: 2 }],
      },
      global: { stubs: STUBS },
    });
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it("confirm-on-all fans out over every picture under ONE batch id", async () => {
    const wrapper = await mountPanel();

    const chip = wrapper.find("button.tag-chip--prediction");
    expect(chip.exists()).toBe(true);
    await chip.trigger("click");
    await flushPromises();

    expect(tagsApi.confirmTagPrediction).toHaveBeenCalledTimes(2);
    const ids = batchIdsOf(tagsApi.confirmTagPrediction);
    expect(new Set(ids).size).toBe(1);
    expect(ids[0]).toMatch(/^cli-/);
    wrapper.unmount();
  });

  it("dragging a tag to Rejected shares one id across the removals AND the rejects", async () => {
    const wrapper = await mountPanel();

    const current = wrapper.findAll("button.tag-chip").find((btn) => {
      return !btn.classes().includes("tag-chip--prediction");
    });
    expect(current).toBeTruthy();
    await current.trigger("dragstart", { dataTransfer: { setData() {} } });
    await wrapper.find(".tag-chips-row--drop-target").trigger("drop");
    await flushPromises();

    expect(tagsApi.removePictureTag).toHaveBeenCalledTimes(2);
    expect(tagsApi.rejectTagPrediction).toHaveBeenCalledTimes(2);
    const ids = batchIdsOf(
      tagsApi.removePictureTag,
      tagsApi.rejectTagPrediction,
    );
    expect(ids).toHaveLength(4);
    expect(new Set(ids).size).toBe(1);
    wrapper.unmount();
  });
});
