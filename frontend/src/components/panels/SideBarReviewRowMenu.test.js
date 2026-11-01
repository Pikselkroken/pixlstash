import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";

const apiGet = vi.fn();

vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef } = await import("vue");
  return {
    apiClient: {
      get: (...args) => apiGet(...args),
      post: vi.fn().mockResolvedValue({ data: {} }),
      patch: vi.fn().mockResolvedValue({ data: {} }),
      put: vi.fn().mockResolvedValue({ data: {} }),
      delete: vi.fn().mockResolvedValue({ data: {} }),
    },
    onSessionReset: () => () => {},
    activateShareToken: vi.fn(),
    appendShareToken: (url) => url,
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated: makeRef(true),
    isReadOnly: makeRef(false),
    sessionContext: makeRef(null),
    login: vi.fn(),
    logout: vi.fn(),
    newOperationBatchId: () => "cli-test",
    operationBatchHeaders: () => undefined,
    setRequestClientId: vi.fn(),
    notifySessionReset: vi.fn(),
    toBackendWebSocketUrl: () => "",
    API_BASE_URL: "/api/v1",
  };
});

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

// Mutable, so a test can put the sidebar on a route other than the default.
const nav = vi.hoisted(() => ({ route: null }));
vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const { vi: vitest } = await import("vitest");
  nav.route = reactive({
    query: {},
    params: {},
    path: "/",
    name: "all-pictures",
  });
  return {
    useRoute: () => nav.route,
    useRouter: () => ({
      push: vitest.fn(),
      replace: vitest.fn(),
      currentRoute: { value: { query: {} } },
    }),
  };
});

import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import SideBar from "./SideBar.vue";

// The sidebar's open-review rows: the row menu (right-click and Shift+F10) with
// its Abort review… and Archive review verbs. Abort goes through the shared
// ReviewAbortDialog; Archive is the store call with no confirm.
//
// The harness is SideBarCharacterRowActive's: the same module mocks and a
// shallow mount. The menu and the dialog are Teleported to the body, so these
// helpers read the document rather than the wrapper.

const REVIEW = { id: 5, tag: "hair", scope: {}, stats: { found: 4 }, progress: { done: 2 } };

function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/characters")) return { data: [] };
  if (u.includes("/projects")) return { data: [] };
  if (u.includes("/picture_sets")) return { data: [] };
  if (u.includes("/summary")) return { data: { image_count: 0 } };
  return { data: [] };
}

async function mountWithReviews(sessions = [REVIEW]) {
  apiGet.mockImplementation(async (url) => respond(url));
  const reviews = useReviewSessionsStore();
  reviews.overlayOpen = true;
  reviews.sessions = sessions;
  const abort = vi.spyOn(reviews, "abortSession").mockResolvedValue();
  const undo = vi.spyOn(reviews, "undoChangesAndAbort").mockResolvedValue();
  const archive = vi.spyOn(reviews, "archiveSession").mockResolvedValue();
  const wrapper = mount(SideBar, {
    shallow: true,
    props: { backendUrl: "/api/v1" },
    attachTo: document.body,
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
      // Teleports are flattened in place, and the abort dialog is the real one.
      stubs: {
        teleport: { template: '<div class="tp"><slot /></div>' },
        ReviewAbortDialog: false,
      },
    },
  });
  for (let i = 0; i < 5; i += 1) await flushPromises();
  return { wrapper, reviews, abort, undo, archive };
}

function rowOf(wrapper, tag) {
  return wrapper
    .findAll(".sidebar-review-session:not(.sidebar-review-new)")
    .find((b) => b.text().includes(tag));
}

function menuItem(label) {
  return [...document.querySelectorAll(".sidebar-ctx-menu .ctx-item")].find(
    (b) => b.textContent.includes(label),
  );
}

function dialog() {
  return document.querySelector('.rs-abort[role="dialog"]');
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("sidebar review row menu", () => {
  it("right-click offers Abort review… and Archive review", async () => {
    const { wrapper } = await mountWithReviews();
    await rowOf(wrapper, "hair").trigger("contextmenu");
    await flushPromises();

    expect(menuItem("Abort review")).toBeDefined();
    expect(menuItem("Archive review")).toBeDefined();
    wrapper.unmount();
  });

  it("Abort review… opens the dialog; Keep calls abortSession with that id", async () => {
    const { wrapper, abort, undo } = await mountWithReviews();
    await rowOf(wrapper, "hair").trigger("contextmenu");
    await flushPromises();
    menuItem("Abort review").click();
    await flushPromises();

    expect(dialog()).not.toBeNull();
    expect(dialog().textContent).toContain("hair");
    dialog().querySelector(".rs-abort-btn--keep").click();
    await flushPromises();

    expect(abort).toHaveBeenCalledTimes(1);
    expect(abort).toHaveBeenCalledWith(5);
    expect(undo).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("Cancel in the abort dialog calls nothing", async () => {
    const { wrapper, abort, undo } = await mountWithReviews();
    await rowOf(wrapper, "hair").trigger("contextmenu");
    await flushPromises();
    menuItem("Abort review").click();
    await flushPromises();

    const cancel = [...dialog().querySelectorAll(".rs-abort-btn")].find(
      (b) => b.textContent.trim() === "Cancel",
    );
    cancel.click();
    await flushPromises();

    expect(dialog()).toBeNull();
    expect(abort).not.toHaveBeenCalled();
    expect(undo).not.toHaveBeenCalled();
    // Focus goes back to the row the menu was opened from.
    expect(document.activeElement).toBe(rowOf(wrapper, "hair").element);
    wrapper.unmount();
  });

  it("Archive review calls archiveSession with that row's id, with no dialog", async () => {
    const { wrapper, archive, abort } = await mountWithReviews();
    await rowOf(wrapper, "hair").trigger("contextmenu");
    await flushPromises();
    menuItem("Archive review").click();
    await flushPromises();

    expect(archive).toHaveBeenCalledTimes(1);
    expect(archive).toHaveBeenCalledWith(5);
    expect(abort).not.toHaveBeenCalled();
    expect(dialog()).toBeNull();
    wrapper.unmount();
  });

  it("Shift+F10 on the row opens the same menu", async () => {
    const { wrapper } = await mountWithReviews();
    await rowOf(wrapper, "hair").trigger("keydown", { key: "F10", shiftKey: true });
    await flushPromises();

    expect(document.querySelector(".sidebar-ctx-menu")).not.toBeNull();
    expect(menuItem("Abort review")).toBeDefined();
    wrapper.unmount();
  });
});
