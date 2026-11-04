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
// its Continue review and End review… verbs. End goes through the shared
// ReviewEndDialog, whose Keep files the receipt (archiveSession).
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

// AppDialog without Vuetify: its slots while open, and the title it is given.
const AppDialogStub = {
  props: ["open", "title"],
  emits: ["close"],
  template: `<div v-if="open" class="app-dialog"><h2>{{ title }}</h2><slot /><div class="app-dialog__footer"><slot name="footer" /></div></div>`,
};

async function mountWithReviews(sessions = [REVIEW]) {
  apiGet.mockImplementation(async (url) => respond(url));
  const reviews = useReviewSessionsStore();
  reviews.overlayOpen = true;
  reviews.sessions = sessions;
  const abort = vi.spyOn(reviews, "abortSession").mockResolvedValue();
  const undo = vi.spyOn(reviews, "undoChangesAndAbort").mockResolvedValue();
  const archive = vi.spyOn(reviews, "archiveSession").mockResolvedValue();
  const open = vi.spyOn(reviews, "openSession").mockImplementation(() => {});
  const wrapper = mount(SideBar, {
    shallow: true,
    props: { backendUrl: "/api/v1" },
    attachTo: document.body,
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
      // Teleports are flattened in place, and the end dialog is the real one
      // over a passthrough AppDialog (its Vuetify overlay is tested in
      // ReviewEndDialog.test.js): open renders its slots, close is its emit.
      stubs: {
        teleport: { template: '<div class="tp"><slot /></div>' },
        ReviewEndDialog: false,
        AppButton: false,
        AppDialog: AppDialogStub,
      },
    },
  });
  for (let i = 0; i < 5; i += 1) await flushPromises();
  return { wrapper, reviews, abort, undo, archive, open };
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
  return document.querySelector(".app-dialog");
}

function dialogAction(label) {
  return [...document.querySelectorAll(".app-dialog__footer button")].find((b) =>
    b.textContent.trim().startsWith(label),
  );
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("sidebar review row menu", () => {
  it("right-click offers Continue review and End review…, and no Abort or Archive", async () => {
    const { wrapper } = await mountWithReviews();
    await rowOf(wrapper, "hair").trigger("contextmenu");
    await flushPromises();

    expect(menuItem("Continue review")).toBeDefined();
    expect(menuItem("End review")).toBeDefined();
    expect(menuItem("Abort review")).toBeUndefined();
    expect(menuItem("Archive review")).toBeUndefined();
    wrapper.unmount();
  });

  it("Continue review opens that review", async () => {
    const { wrapper, open } = await mountWithReviews();
    await rowOf(wrapper, "hair").trigger("contextmenu");
    await flushPromises();
    menuItem("Continue review").click();
    await flushPromises();

    expect(open).toHaveBeenCalledWith(5);
    expect(dialog()).toBeNull();
    wrapper.unmount();
  });

  it("End review… opens the dialog; Keep files the receipt for that id", async () => {
    const { wrapper, abort, undo, archive } = await mountWithReviews();
    await rowOf(wrapper, "hair").trigger("contextmenu");
    await flushPromises();
    menuItem("End review").click();
    for (let i = 0; i < 5; i += 1) await flushPromises();

    expect(dialog()).not.toBeNull();
    expect(dialog().textContent).toContain("hair");
    dialogAction("Keep").click();
    await flushPromises();

    expect(archive).toHaveBeenCalledTimes(1);
    expect(archive).toHaveBeenCalledWith(5);
    expect(abort).not.toHaveBeenCalled();
    expect(undo).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("Cancel in the end dialog calls nothing", async () => {
    const { wrapper, abort, undo, archive } = await mountWithReviews();
    await rowOf(wrapper, "hair").trigger("contextmenu");
    await flushPromises();
    menuItem("End review").click();
    for (let i = 0; i < 5; i += 1) await flushPromises();

    dialogAction("Cancel").click();
    for (let i = 0; i < 5; i += 1) await flushPromises();

    expect(dialog()).toBeNull();
    expect(abort).not.toHaveBeenCalled();
    expect(undo).not.toHaveBeenCalled();
    expect(archive).not.toHaveBeenCalled();
    // Focus goes back to the row the menu was opened from.
    expect(document.activeElement).toBe(rowOf(wrapper, "hair").element);
    wrapper.unmount();
  });

  it("Shift+F10 on the row opens the same menu", async () => {
    const { wrapper } = await mountWithReviews();
    await rowOf(wrapper, "hair").trigger("keydown", { key: "F10", shiftKey: true });
    await flushPromises();

    expect(document.querySelector(".sidebar-ctx-menu")).not.toBeNull();
    expect(menuItem("End review")).toBeDefined();
    wrapper.unmount();
  });
});
