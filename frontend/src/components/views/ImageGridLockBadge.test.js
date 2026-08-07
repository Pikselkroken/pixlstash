// Grid lock-badge visibility (picture-set locking, plan §3.4).
//
// ImageGrid.vue (~8.7k lines) is impractical to mount, so this test exercises
// the exact contract the grid's badge relies on: a v-if bound to
// `lockedSetsStore.isLocked(img.id)` and a :title bound to
// `lockedSetsStore.lockReason(img.id)`, against the REAL store. It proves the
// badge shows only for pictures the store reports as locked, carries the lock
// reason, and reacts when the store's locked set membership changes.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn().mockResolvedValue({ data: { sets: [] } }) },
  onSessionReset: () => () => {},
}));

import { useLockedSetsStore } from "../../stores/useLockedSetsStore";

// A minimal stand-in reproducing the grid's lock-badge markup verbatim.
const LockBadge = {
  props: { img: { type: Object, required: true } },
  setup() {
    return { lockedSetsStore: useLockedSetsStore() };
  },
  template: `
    <div
      v-if="lockedSetsStore.isLocked(img.id)"
      class="thumbnail-lock-badge thumbnail-badge"
      :title="lockedSetsStore.lockReason(img.id)"
    >
      <i>lock</i>
    </div>
  `,
};

let store;

beforeEach(() => {
  setActivePinia(createPinia());
  store = useLockedSetsStore();
  store.sets = [{ id: 3, name: "Eval slice", picture_ids: [1, 2] }];
});

describe("grid lock badge visibility", () => {
  it("renders the badge for a locked picture with the lock reason as title", () => {
    const wrapper = mount(LockBadge, { props: { img: { id: 1 } } });
    const badge = wrapper.find(".thumbnail-lock-badge");
    expect(badge.exists()).toBe(true);
    expect(badge.attributes("title")).toContain("the locked set 'Eval slice'");
  });

  it("renders no badge for an unlocked picture", () => {
    const wrapper = mount(LockBadge, { props: { img: { id: 999 } } });
    expect(wrapper.find(".thumbnail-lock-badge").exists()).toBe(false);
  });

  it("reacts when the store's locked membership changes", async () => {
    const wrapper = mount(LockBadge, { props: { img: { id: 5 } } });
    expect(wrapper.find(".thumbnail-lock-badge").exists()).toBe(false);

    // Picture 5 becomes locked by a set → badge appears reactively.
    store.sets = [{ id: 9, name: "Frozen v2", picture_ids: [5] }];
    await wrapper.vm.$nextTick();
    const badge = wrapper.find(".thumbnail-lock-badge");
    expect(badge.exists()).toBe(true);
    expect(badge.attributes("title")).toContain("the locked set 'Frozen v2'");
  });
});
