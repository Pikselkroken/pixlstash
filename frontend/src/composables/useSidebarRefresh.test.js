// useSidebarRefresh - a refresh asked for before the sidebar exists is owed.
//
// `<v-app>` renders its content inside a Suspense boundary, so on a route whose
// view is an async component the sidebar's template ref is still null when
// App.vue's mounted hook asks for the start-up refresh. That refresh used to be
// dropped on the floor: the shared lists still loaded, so the sidebar showed
// every person by name and never fetched a thumbnail, a count or a share mark.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { nextTick, ref } from "vue";
import { mount } from "@vue/test-utils";

import { useSidebarRefresh } from "./useSidebarRefresh";

// The stores this composable also nudges, as spies: a refresh that is owed to
// the sidebar must still reach them at once, since the shared lists are what
// put the names on screen in the meantime.
const stores = vi.hoisted(() => ({
  invalidate: vi.fn(),
  fetchLocked: vi.fn(),
  invalidateScopeCounts: vi.fn(),
  refreshCounts: vi.fn(),
}));
vi.mock("../stores/useEntityListsStore", () => ({
  useEntityListsStore: () => ({ invalidate: stores.invalidate }),
}));
vi.mock("../stores/useLockedSetsStore", () => ({
  useLockedSetsStore: () => ({ fetch: stores.fetchLocked }),
}));
vi.mock("../stores/useDedupStore", () => ({
  useDedupStore: () => ({
    invalidateScopeCounts: stores.invalidateScopeCounts,
    refreshCounts: stores.refreshCounts,
  }),
}));

let wrapper;
let sidebarRef;
let api;

beforeEach(() => {
  vi.clearAllMocks();
  sidebarRef = ref(null);
  wrapper = mount({
    setup() {
      api = useSidebarRefresh({ sidebarRef });
      return () => null;
    },
  });
});

afterEach(() => {
  wrapper.unmount();
});

describe("useSidebarRefresh", () => {
  it("runs a refresh that arrived before the sidebar, once it mounts", async () => {
    api.refreshSidebar({ flashCounts: true });
    api.refreshSidebar();

    // Owing the sidebar does not hold back the stores.
    expect(stores.invalidate).toHaveBeenCalledTimes(2);
    expect(stores.fetchLocked).toHaveBeenCalledTimes(2);
    expect(stores.refreshCounts).toHaveBeenCalledTimes(2);

    const sidebar = { refreshSidebar: vi.fn() };
    sidebarRef.value = sidebar;
    await nextTick();

    // Once, however many were missed, and no option is lost on the way.
    expect(sidebar.refreshSidebar).toHaveBeenCalledTimes(1);
    expect(sidebar.refreshSidebar).toHaveBeenCalledWith({ flashCounts: true });

    // Paid once: a later sidebar instance owes nothing.
    const remounted = { refreshSidebar: vi.fn() };
    sidebarRef.value = remounted;
    await nextTick();
    expect(remounted.refreshSidebar).not.toHaveBeenCalled();
  });

  it("does not refresh a sidebar that missed nothing", async () => {
    const sidebar = { refreshSidebar: vi.fn() };
    sidebarRef.value = sidebar;
    await nextTick();
    expect(sidebar.refreshSidebar).not.toHaveBeenCalled();

    api.refreshSidebar();
    expect(sidebar.refreshSidebar).toHaveBeenCalledTimes(1);
    expect(stores.invalidate).toHaveBeenCalledTimes(1);

    // Delivered directly, so nothing is owed to a later instance.
    const remounted = { refreshSidebar: vi.fn() };
    sidebarRef.value = remounted;
    await nextTick();
    expect(remounted.refreshSidebar).not.toHaveBeenCalled();
  });
});
