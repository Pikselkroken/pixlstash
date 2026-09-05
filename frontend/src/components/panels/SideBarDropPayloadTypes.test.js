// Sidebar drop targets accept a payload kind or they refuse it (issue #757).
//
// Two defects lived here. The rows bound `@dragover.prevent`, and the modifier
// runs preventDefault() BEFORE the handler body and regardless of what the
// handler decides, so every row accepted every drag on the page and painted the
// full `droppable` highlight for payloads it would do nothing with. And because
// all internal payloads shared one MIME key (`application/json`, whose body is
// unreadable during dragover), the drop handlers could only key off `imageIds`
// - which a face-bbox payload also carries - so a face drag landed in a set as
// if it were a picture drag.
//
// Both directions matter. Over-blocking a picture drag is its own regression,
// so every assertion that a face drag is refused is paired with the picture
// drag still being accepted on the same row.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { ref } from "vue";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef } = await import("vue");
  return {
    apiClient: {
      get: (...args) => apiGet(...args),
      post: (...args) => apiPost(...args),
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

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: "all-pictures" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    currentRoute: ref({ query: {} }),
  }),
}));

import { isReadOnly, sessionContext } from "../../utils/apiClient";
import { FACE_DRAG_MIME, PICTURE_DRAG_MIME } from "../../utils/media.js";
import SideBar from "./SideBar.vue";
import FolderTreeNode from "../editors/FolderTreeNode.vue";

const ADA = { id: 7, name: "Ada", image_count: 3, project_image_count: 3 };
const SET = { id: 11, name: "Shoot", image_count: 2 };
const PROJECT = { id: 3, name: "Book", image_count: 118 };
const REF_FOLDER = { id: 21, folder: "/photos/ref", label: "Ref" };

function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/reference-folders")) {
    return { data: { folders: [REF_FOLDER], in_docker: false } };
  }
  if (u.includes("/characters")) return { data: [ADA] };
  if (u.includes("/projects")) return { data: [PROJECT] };
  if (u.includes("/picture_sets")) return { data: [SET] };
  if (u.includes("/summary")) return { data: { image_count: 3 } };
  return { data: [] };
}

async function mountSidebar() {
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

function rowByTitle(wrapper, prefix, selector = ".sidebar-list-item") {
  const row = wrapper
    .findAll(selector)
    .find((el) => String(el.attributes("title") ?? "").startsWith(prefix));
  if (!row) throw new Error(`no ${selector} titled ${prefix}`);
  return row;
}

/** Switch sidebar tabs, which is where the other drop targets live. */
async function openTab(wrapper, label) {
  const tab = wrapper
    .findAll("button.sidebar-view-tab")
    .find((el) => el.text().includes(label));
  if (!tab) throw new Error(`no ${label} tab`);
  await tab.trigger("click");
  for (let i = 0; i < 5; i += 1) await flushPromises();
}

/** A dataTransfer whose `types` is all a dragover handler may read. */
function transfer(payload) {
  const json = JSON.stringify(payload);
  const marker =
    payload.type === "face-bbox" ? FACE_DRAG_MIME : PICTURE_DRAG_MIME;
  return {
    types: ["application/json", marker],
    dropEffect: "",
    effectAllowed: "move",
    files: [],
    // Protected during dragover, readable on drop - the same asymmetry the
    // browser enforces, so a handler that cheats fails here.
    getData: (type) => (type === "application/json" ? json : ""),
  };
}

/** Dispatch a real cancelable event so `defaultPrevented` can be asserted. */
async function fire(row, name, dataTransfer) {
  const event = new Event(name, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "dataTransfer", { value: dataTransfer });
  row.element.dispatchEvent(event);
  await flushPromises();
  return event;
}

const PICTURES = { type: "image-ids", imageIds: [101, 102] };
// The shape useMultiSelect sends: it carries imageIds too, which is exactly why
// "has imageIds" was never a safe test for "is a picture drag".
const FACES = {
  type: "face-bbox",
  faceIds: [55],
  imageIds: [101],
  faces: [{ imageId: 101, faceIdx: 0, faceId: 55 }],
};

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  sessionContext.value = null;
  apiGet.mockReset().mockImplementation((url) => Promise.resolve(respond(url)));
  apiPost.mockReset().mockResolvedValue({ data: {} });
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("sidebar rows judge the drag payload during dragover", () => {
  it("accepts a picture drag on a set row and highlights it", async () => {
    const wrapper = await mountSidebar();
    const row = rowByTitle(wrapper, `${SET.name} (`);

    const event = await fire(row, "dragover", transfer(PICTURES));

    expect(event.defaultPrevented).toBe(true);
    expect(row.classes()).toContain("droppable");
    expect(row.classes()).not.toContain("not-droppable");

    wrapper.unmount();
  });

  it("refuses a face drag on a set row instead of inviting the drop", async () => {
    const wrapper = await mountSidebar();
    const row = rowByTitle(wrapper, `${SET.name} (`);

    const event = await fire(row, "dragover", transfer(FACES));

    // Not preventing default is what makes the browser refuse the drop.
    expect(event.defaultPrevented).toBe(false);
    expect(row.classes()).not.toContain("droppable");
    expect(row.classes()).toContain("not-droppable");

    wrapper.unmount();
  });

  it("accepts both pictures and faces on a character row", async () => {
    const wrapper = await mountSidebar();
    const row = rowByTitle(wrapper, `${ADA.name} (`);

    for (const payload of [PICTURES, FACES]) {
      const event = await fire(row, "dragover", transfer(payload));
      expect(event.defaultPrevented).toBe(true);
      expect(row.classes()).toContain("droppable");
      await fire(row, "dragleave", transfer(payload));
    }

    wrapper.unmount();
  });

  it("takes pictures but not faces on a project row", async () => {
    const wrapper = await mountSidebar();
    await openTab(wrapper, "Projects");
    const row = rowByTitle(
      wrapper,
      `${PROJECT.name} (`,
      ".sidebar-project-tree-row",
    );

    const accepted = await fire(row, "dragover", transfer(PICTURES));
    expect(accepted.defaultPrevented).toBe(true);
    expect(row.classes()).toContain("droppable");

    await fire(row, "dragleave", transfer(PICTURES));
    const refused = await fire(row, "dragover", transfer(FACES));
    expect(refused.defaultPrevented).toBe(false);
    expect(row.classes()).toContain("not-droppable");

    wrapper.unmount();
  });

  it("takes pictures but not faces on a reference-folder row", async () => {
    const wrapper = await mountSidebar();
    await openTab(wrapper, "Folders");
    const row = rowByTitle(
      wrapper,
      REF_FOLDER.folder,
      ".sidebar-folder-root-row",
    );

    const accepted = await fire(row, "dragover", transfer(PICTURES));
    expect(accepted.defaultPrevented).toBe(true);
    expect(row.classes()).toContain("droppable");

    await fire(row, "dragleave", transfer(PICTURES));
    const refused = await fire(row, "dragover", transfer(FACES));
    expect(refused.defaultPrevented).toBe(false);
    expect(row.classes()).toContain("not-droppable");

    wrapper.unmount();
  });

  it("refuses on a NESTED folder row too, not only the root", async () => {
    // The root row got the refused branch; FolderTreeNode never declared
    // `dropRejected`, so it landed as a DOM attribute and every nested row lit
    // up with the full accept highlight for a payload it would not take.
    const entry = { path: "/photos/ref/kids", name: "kids", image_count: 0 };
    const node = mount(FolderTreeNode, {
      props: {
        entry,
        rfId: REF_FOLDER.id,
        folderBrowseCache: {},
        expandedFolderIds: new Set(),
        dropTargetKey: `path-${entry.path}`,
        dropRejected: true,
      },
      global: {
        config: {
          compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
        },
      },
    });
    const row = node.find(".sidebar-folder-child-row");

    expect(row.classes()).toContain("not-droppable");
    expect(row.classes()).not.toContain("droppable");

    // The accepted direction still paints, so this is not over-blocking.
    await node.setProps({ dropRejected: false });
    expect(row.classes()).toContain("droppable");
    expect(row.classes()).not.toContain("not-droppable");

    node.unmount();
  });

  it("keeps the highlight when the pointer crosses into a child element", async () => {
    // dragleave fires on the way into the row's own label/icon; only a leave
    // that lands outside the row is a real leave.
    const wrapper = await mountSidebar();
    const row = rowByTitle(wrapper, `${SET.name} (`);
    await fire(row, "dragover", transfer(PICTURES));
    expect(row.classes()).toContain("droppable");

    const child = row.element.querySelector("*") ?? row.element;
    const leave = new Event("dragleave", { bubbles: true, cancelable: true });
    Object.defineProperty(leave, "relatedTarget", { value: child });
    row.element.dispatchEvent(leave);
    await flushPromises();

    expect(row.classes()).toContain("droppable");

    // Leaving the row for good does clear it.
    const out = new Event("dragleave", { bubbles: true, cancelable: true });
    Object.defineProperty(out, "relatedTarget", { value: document.body });
    row.element.dispatchEvent(out);
    await flushPromises();

    expect(row.classes()).not.toContain("droppable");

    wrapper.unmount();
  });
});

describe("sidebar drops read the payload kind, not just imageIds", () => {
  it("adds pictures to a set when the payload is a picture drag", async () => {
    const wrapper = await mountSidebar();
    const row = rowByTitle(wrapper, `${SET.name} (`);

    await fire(row, "drop", transfer(PICTURES));

    expect(wrapper.emitted("images-moved")?.[0]?.[0]).toEqual({
      imageIds: PICTURES.imageIds,
    });

    wrapper.unmount();
  });

  it("files nothing into a set when the payload is a face drag", async () => {
    const wrapper = await mountSidebar();
    const row = rowByTitle(wrapper, `${SET.name} (`);

    await fire(row, "drop", transfer(FACES));

    expect(wrapper.emitted("images-moved")).toBeUndefined();
    const setPosts = apiPost.mock.calls.filter(([url]) =>
      String(url ?? "").includes("picture_sets"),
    );
    expect(setPosts).toEqual([]);

    wrapper.unmount();
  });
});
