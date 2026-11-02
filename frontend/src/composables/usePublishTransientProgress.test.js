// The bridge from a grid's sort/search wait to the title bar: what it writes
// reaches the tasks store, and its unmount clears only its own bar.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { defineComponent, h, nextTick, reactive } from "vue";

import { usePublishTransientProgress } from "./usePublishTransientProgress";
import { useTasksStore } from "../stores/useTasksStore";

vi.mock("../api/workers", () => ({ getWorkerProgress: vi.fn() }));

function publisher(state) {
  return defineComponent({
    setup() {
      usePublishTransientProgress(() => ({ ...state }));
      return () => h("div");
    },
  });
}

const SORTING = {
  visible: true,
  status: "running",
  message: "Sorting by smart score",
  percent: 30,
};

let pinia;
beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
});

function mountPublisher(state) {
  return mount(publisher(state), { global: { plugins: [pinia] } });
}

describe("usePublishTransientProgress", () => {
  it("publishes the wait and follows it as it moves", async () => {
    const state = reactive({ ...SORTING });
    mountPublisher(state);
    const store = useTasksStore();
    expect(store.transientProgress).toEqual(SORTING);

    state.percent = 80;
    await nextTick();
    expect(store.transientProgress.percent).toBe(80);
  });

  it("clears the bar when the grid goes away", () => {
    const wrapper = mountPublisher(reactive({ ...SORTING }));
    wrapper.unmount();
    expect(useTasksStore().transientProgress.visible).toBe(false);
    expect(useTasksStore().transientProgress.status).toBe("idle");
  });

  it("leaves a newer grid's bar alone when the old one unmounts", () => {
    const old = mountPublisher(reactive({ ...SORTING }));
    const searching = { ...SORTING, message: "Searching", percent: 10 };
    mountPublisher(reactive(searching));
    old.unmount();
    expect(useTasksStore().transientProgress).toEqual(searching);
  });
});
