// A filter whose output the library already holds.
//
// Re-running a deterministic filter reproduces a byte-identical image, the
// importer refuses it as a duplicate, and no picture is created. Nothing on the
// grid moves, so the only thing separating that from a filter that silently did
// nothing is the notice asserted here.
//
// Driven through `handlePluginRunRequest` - the handler behind `@run-plugin`,
// which is what both the selection bar and the lightbox emit. Calling the inner
// function directly would assert against an argument shape no caller produces.
//
// Two things beyond the sentence are pinned, because both are wrong by default:
// the level (an `error` is sticky and would leave the card on screen) and the
// key (a global one lets the next plugin's run overwrite this text while
// wearing a count badge that then contradicts it).

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";
import { useNoticeStore } from "../../stores/useNoticeStore.js";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef, computed: makeComputed } = await import("vue");
  return {
    onSessionReset: () => () => {},
    apiClient: {
      get: (...args) => apiGet(...args),
      post: (...args) => apiPost(...args),
      patch: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
    activateShareToken: vi.fn(),
    appendShareToken: (url) => url,
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated: makeRef(true),
    isReadOnly: makeComputed(() => false),
    login: vi.fn(),
    logout: vi.fn(),
    sessionContext: makeRef({ scope: "ALL" }),
    setRequestClientId: vi.fn(),
    API_BASE_URL: "/api/v1",
  };
});

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: "grid" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    currentRoute: ref({ query: {} }),
  }),
}));

import ImageGrid from "./ImageGrid.vue";

/** Keyed by plugin name - what `POST /pictures/plugins/{name}` answers. */
let pluginResponses = {};

function mountGrid() {
  const wrapper = mount(ImageGrid, {
    shallow: true,
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
    props: { backendUrl: "/api/v1" },
  });
  return wrapper;
}

/** Emit what SelectionBar/ImageOverlay emit, and let the run settle. */
async function runPlugin(wrapper, pluginName, pictureIds = [42]) {
  // Let the mount's own registry fetch land first, so the run reads the same
  // plugin list the menus do.
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
  wrapper.vm.handlePluginRunRequest({ pluginName, pictureIds, parameters: {} });
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}

function notices() {
  return useNoticeStore().notices;
}

beforeEach(() => {
  setActivePinia(createPinia());
  apiGet.mockReset();
  apiPost.mockReset();
  apiGet.mockImplementation(async (url) => {
    // The registry the plugin menus render from: the user picked "Auto Crop",
    // so that is the name they are owed back.
    if (String(url ?? "").endsWith("/pictures/plugins")) {
      return {
        data: {
          plugins: [
            { name: "auto_crop", display_name: "Auto Crop" },
            { name: "blur", display_name: "Blur" },
          ],
        },
      };
    }
    return { data: { pictures: [], count: 0, total: 0 } };
  });
  apiPost.mockImplementation(async (url) => {
    const match = /\/pictures\/plugins\/([^/?]+)/.exec(String(url ?? ""));
    if (match) return { data: pluginResponses[match[1]] ?? {} };
    return { data: {} };
  });
  pluginResponses = {};
});

describe("ImageGrid - plugin run whose output already exists", () => {
  it("says so, by the plugin's display name, when the output was a duplicate", async () => {
    pluginResponses.auto_crop = {
      status: "success",
      created_picture_ids: [],
      duplicate_picture_ids: [7],
      output_picture_ids: [7],
    };
    const wrapper = mountGrid();
    await runPlugin(wrapper, "auto_crop");

    expect(notices()).toHaveLength(1);
    expect(notices()[0].text).toBe(
      "Auto Crop: 1 image is already in your library",
    );
    // Not `error`: nothing failed, and an error card carries no dismiss timer.
    expect(notices()[0].level).toBe("info");
    expect(notices()[0].timeout).toBeGreaterThan(0);
    wrapper.unmount();
  });

  it("counts pictures, not outputs", async () => {
    // The backend walks output hashes, so two sources that filter down to the
    // same image name the same picture id twice. One picture, one image.
    pluginResponses.auto_crop = {
      status: "success",
      created_picture_ids: [],
      duplicate_picture_ids: [7, 7],
      output_picture_ids: [7, 7],
    };
    const wrapper = mountGrid();
    await runPlugin(wrapper, "auto_crop", [42, 43]);

    expect(notices()[0].text).toBe(
      "Auto Crop: 1 image is already in your library",
    );
    wrapper.unmount();
  });

  it("reports the duplicates in a run that also created something", async () => {
    pluginResponses.auto_crop = {
      status: "success",
      created_picture_ids: [9],
      duplicate_picture_ids: [7, 8],
      output_picture_ids: [9, 7, 8],
    };
    const wrapper = mountGrid();
    await runPlugin(wrapper, "auto_crop", [42, 43, 44]);

    expect(notices()[0].text).toBe(
      "Auto Crop: 2 images are already in your library",
    );
    wrapper.unmount();
  });

  it("says nothing when every output was new", async () => {
    pluginResponses.auto_crop = {
      status: "success",
      created_picture_ids: [9],
      duplicate_picture_ids: [],
      output_picture_ids: [9],
    };
    const wrapper = mountGrid();
    await runPlugin(wrapper, "auto_crop");

    expect(notices()).toEqual([]);
    wrapper.unmount();
  });

  it("says nothing when the field is absent altogether", async () => {
    // An older backend, or any response that simply does not carry the list.
    pluginResponses.auto_crop = { status: "success", created_picture_ids: [9] };
    const wrapper = mountGrid();
    await runPlugin(wrapper, "auto_crop");

    expect(notices()).toEqual([]);
    wrapper.unmount();
  });

  it("keeps one plugin's report from overwriting another's", async () => {
    // A global key would coalesce these: the Auto Crop sentence would be
    // replaced by the Blur one and badged ×2, claiming a count it never had.
    pluginResponses.auto_crop = {
      status: "success",
      created_picture_ids: [],
      duplicate_picture_ids: [7, 8],
      output_picture_ids: [7, 8],
    };
    pluginResponses.blur = {
      status: "success",
      created_picture_ids: [],
      duplicate_picture_ids: [11],
      output_picture_ids: [11],
    };
    const wrapper = mountGrid();
    await runPlugin(wrapper, "auto_crop", [42, 43]);
    await runPlugin(wrapper, "blur");

    expect(notices().map((n) => n.text)).toEqual([
      "Auto Crop: 2 images are already in your library",
      "Blur: 1 image is already in your library",
    ]);
    expect(notices().map((n) => n.count)).toEqual([1, 1]);
    wrapper.unmount();
  });

  it("coalesces a repeat run of the same plugin", async () => {
    pluginResponses.auto_crop = {
      status: "success",
      created_picture_ids: [],
      duplicate_picture_ids: [7],
      output_picture_ids: [7],
    };
    const wrapper = mountGrid();
    await runPlugin(wrapper, "auto_crop");
    await runPlugin(wrapper, "auto_crop");

    expect(notices()).toHaveLength(1);
    expect(notices()[0].count).toBe(2);
    wrapper.unmount();
  });
});
