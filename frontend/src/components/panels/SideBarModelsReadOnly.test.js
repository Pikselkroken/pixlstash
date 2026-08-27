// The sidebar's two Models entries against a READ/share credential (#1014).
//
// Every route the shelf calls is owner-only, so the destination was live for a
// session the backend refuses on all of it. The rule it now follows is the
// demo site's, stated in `e2e/specs/read-only-features.spec.js`: show-but-
// disable, never hide - the demo IS a read-only session, and a hidden feature
// there advertises a smaller product than PixlStash is. So the row stays, goes
// inert, and says why, exactly like Duplicates beside it.
//
// Both entries are covered because they are separate markup: the expanded rail
// and the collapsed dock. The dock half was written twice before this file
// existed and neither version was tested.
//
// `useAppNavigationModels.test.js` covers the other half - that a pasted
// `/models` URL never mounts the shelf.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
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

vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const route = reactive({
    query: {},
    params: {},
    path: "/",
    name: "all-pictures",
  });
  const { vi: vitest } = await import("vitest");
  return {
    useRoute: () => route,
    useRouter: () => ({
      push: vitest.fn(),
      replace: vitest.fn(),
      currentRoute: { value: { query: {} } },
    }),
  };
});

import { isReadOnly, sessionContext } from "../../utils/apiClient";
import { useSidebarStore } from "../../stores/useSidebarStore";
import SideBar from "./SideBar.vue";

function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/characters")) return { data: [] };
  if (u.includes("/projects")) return { data: [] };
  if (u.includes("/picture_sets")) return { data: [] };
  if (u.includes("/summary")) return { data: { image_count: 0 } };
  return { data: [] };
}

/**
 * Mount the sidebar. `docked` renders the collapsed dock instead of the
 * expanded rail - a separate branch of the template with its own copy of both
 * destinations.
 */
async function mountSidebar({ docked = false } = {}) {
  if (docked) useSidebarStore().sidebarDocked = true;
  const wrapper = mount(SideBar, {
    shallow: true,
    props: { backendUrl: "/api/v1" },
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
  });
  wrapper.vm.refreshSidebar();
  for (let i = 0; i < 5; i += 1) await flushPromises();
  return wrapper;
}

const SHELF_HINT = "The model shelf is only available in your own library";
const DEDUP_HINT = "Duplicate review is only available in your own library";

/** The expanded rail's Models button, by its label. */
function expandedModels(wrapper) {
  return wrapper
    .findAll("button.sidebar-list-item")
    .find((b) => b.text().includes("Models"));
}

/**
 * The collapsed dock's Models button. Located by class, not by title: the title
 * is the thing under test and swaps to the refusal hint for a READ session.
 */
function dockedModels(wrapper) {
  return wrapper.find("button.sidebar-collapsed-item.sidebar-destination-btn");
}

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  sessionContext.value = null;
  apiGet.mockReset().mockImplementation((url) => Promise.resolve(respond(url)));
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the expanded rail's Models entry", () => {
  it("is visible, inert and explained for a READ session", async () => {
    isReadOnly.value = true;
    const wrapper = await mountSidebar();

    // The anchor. A read-only render that produced no rail at all would leave
    // every "the entry is not clickable" assertion below passing for the wrong
    // reason - and Duplicates being inert-not-hidden is the very contract this
    // change is following, so it is worth pinning in the same breath.
    const duplicates = wrapper
      .findAll(".sidebar-list-item")
      .find((el) => el.text().includes("Duplicates"));
    expect(duplicates).toBeTruthy();
    expect(duplicates.attributes("aria-disabled")).toBe("true");
    expect(duplicates.attributes("title")).toBe(DEDUP_HINT);

    const models = expandedModels(wrapper);
    expect(models).toBeTruthy();
    expect(models.attributes("aria-disabled")).toBe("true");
    expect(models.attributes("title")).toBe(SHELF_HINT);
    // `aria-disabled`, not the native attribute: the control stays tabbable so
    // a keyboard user reaches the explanation too.
    expect(models.attributes("disabled")).toBeUndefined();

    await models.trigger("click");
    expect(wrapper.emitted("select-models")).toBeUndefined();

    wrapper.unmount();
  });

  it("still navigates for the owner", async () => {
    // The positive control: an inert-for-everybody row is its own regression.
    const wrapper = await mountSidebar();

    const models = expandedModels(wrapper);
    expect(models).toBeTruthy();
    expect(models.attributes("aria-disabled")).toBeUndefined();
    expect(models.attributes("title")).toBeUndefined();

    await models.trigger("click");
    expect(wrapper.emitted("select-models")).toHaveLength(1);

    wrapper.unmount();
  });
});

describe("the collapsed dock's Models entry", () => {
  it("is visible, inert and explained for a READ session", async () => {
    isReadOnly.value = true;
    const wrapper = await mountSidebar({ docked: true });

    const duplicates = wrapper
      .findAll(".sidebar-collapsed-item")
      .find((el) => String(el.attributes("title") ?? "") === DEDUP_HINT);
    expect(duplicates).toBeTruthy();

    const models = dockedModels(wrapper);
    expect(models.exists()).toBe(true);
    expect(models.attributes("aria-disabled")).toBe("true");
    expect(models.attributes("title")).toBe(SHELF_HINT);

    await models.trigger("click");
    expect(wrapper.emitted("select-models")).toBeUndefined();

    wrapper.unmount();
  });

  it("still navigates for the owner", async () => {
    const wrapper = await mountSidebar({ docked: true });

    const models = dockedModels(wrapper);
    expect(models.exists()).toBe(true);
    expect(models.attributes("title")).toBe("Models");
    expect(models.attributes("aria-disabled")).toBeUndefined();

    await models.trigger("click");
    expect(wrapper.emitted("select-models")).toHaveLength(1);

    wrapper.unmount();
  });
});
