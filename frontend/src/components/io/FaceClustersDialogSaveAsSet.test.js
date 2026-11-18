// "Save as set…": the faces that count in a cluster become a picture set under
// the person, named in a small dialog, filled in one bulk call.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const api = vi.hoisted(() => ({
  getFaceClusters: vi.fn(),
  setReferenceCluster: vi.fn(),
}));
const sets = vi.hoisted(() => ({
  createPictureSet: vi.fn(),
  bulkAddPicturesToSet: vi.fn(),
}));
const stores = vi.hoisted(() => ({
  entityLists: { characters: [], invalidate: vi.fn() },
  notice: { success: vi.fn(), error: vi.fn() },
  operation: { refresh: vi.fn() },
}));
vi.mock("../../api/characters", () => api);
vi.mock("../../api/pictureSets", () => sets);
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
}));
vi.mock("../../stores/useEntityListsStore", () => ({
  useEntityListsStore: () => stores.entityLists,
}));
vi.mock("../../stores/useNoticeStore", () => ({
  useNoticeStore: () => stores.notice,
}));
vi.mock("../../stores/useOperationStore", () => ({
  useOperationStore: () => stores.operation,
}));

import FaceClustersDialog from "./FaceClustersDialog.vue";

const member = (picture_id, centrality) => ({
  picture_id,
  centrality,
  score: null,
  format: "png",
});

const DATA = {
  threshold: 0.45,
  faces: 7,
  reference_cluster: null,
  clusters: [
    {
      members: [member(1, 0.88), member(2, 0.8), member(3, 0.7), member(4, 0.52)],
      size: 4,
      cohesion: 0.61,
      is_reference: false,
      similarity_to_reference: null,
      from_sheets: false,
    },
    {
      members: [member(11, 0.84), member(12, 0.7), member(13, 0.6)],
      size: 3,
      cohesion: 0.57,
      is_reference: false,
      similarity_to_reference: null,
      from_sheets: false,
    },
  ],
  unclustered: [],
};

let wrapper;
function mountDialog() {
  wrapper = mount(FaceClustersDialog, {
    props: { open: true, character: { id: 7, name: "Wren" } },
    global: { stubs: { teleport: true } },
    attachTo: document.body,
  });
  return wrapper;
}
afterEach(() => wrapper?.unmount());

const nameField = () => wrapper.find('[data-testid="fc-set-name"] input');
const create = () => wrapper.find('[data-testid="fc-set-create"]').trigger("click");

beforeEach(() => {
  vi.clearAllMocks();
  stores.entityLists.characters = [{ id: 7, name: "Wren", project_ids: [3] }];
  api.getFaceClusters.mockResolvedValue(DATA);
  sets.createPictureSet.mockResolvedValue({ picture_set: { id: 40, name: "Wren keepers" } });
  sets.bulkAddPicturesToSet.mockResolvedValue({ added: 3 });
});

describe("FaceClustersDialog: Save as set…", () => {
  it("creates a set under the person with only the faces above the cut", async () => {
    mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="fc-cut"]').setValue("0.75");
    await wrapper.find('[data-testid="fc-save-set"]').trigger("click");
    await flushPromises();
    expect(nameField().exists()).toBe(true);
    expect(nameField().element.value).toBe("Wren, cluster 1");
    await nameField().setValue("Wren keepers");
    await create();
    await flushPromises();
    expect(sets.createPictureSet).toHaveBeenCalledWith({
      name: "Wren keepers",
      parent_character_id: 7,
      project_ids: [3],
    });
    expect(sets.bulkAddPicturesToSet).toHaveBeenCalledWith(40, [1, 2]);
    // The server's count: a stack-mate came along.
    expect(stores.notice.success).toHaveBeenCalledWith(
      "Created Wren keepers, added 3 pictures.",
      expect.anything(),
    );
    expect(stores.entityLists.invalidate).toHaveBeenCalledWith(["sets"]);
    // The reference is a separate act: saving a set never touches it.
    expect(api.setReferenceCluster).not.toHaveBeenCalled();
    expect(nameField().exists()).toBe(false);
  });

  it("takes the selected cluster's faces, and is off when none count", async () => {
    mountDialog();
    await flushPromises();
    await wrapper.findAll('[data-testid="fc-row"]')[1].trigger("click");
    await wrapper.find('[data-testid="fc-save-set"]').trigger("click");
    await flushPromises();
    expect(nameField().element.value).toBe("Wren, cluster 2");
    await create();
    await flushPromises();
    expect(sets.bulkAddPicturesToSet).toHaveBeenCalledWith(40, [11, 12, 13]);
    // Past the top face nothing counts, so there is nothing to save.
    await wrapper.find('[data-testid="fc-cut"]').setValue("0.9");
    expect(
      wrapper.find('[data-testid="fc-save-set"]').attributes("disabled"),
    ).toBeDefined();
  });

  it("keeps the name dialog open with the reason when the set cannot be created", async () => {
    sets.createPictureSet.mockRejectedValue({
      response: { status: 409, data: { detail: "A picture set named 'x' already exists." } },
    });
    mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="fc-save-set"]').trigger("click");
    await flushPromises();
    await create();
    await flushPromises();
    expect(nameField().exists()).toBe(true);
    expect(wrapper.find('[data-testid="fc-set-name"]').text()).toContain("already exists");
    expect(sets.bulkAddPicturesToSet).not.toHaveBeenCalled();
  });

  it("says the new set is empty when the pictures cannot be added", async () => {
    sets.bulkAddPicturesToSet.mockRejectedValue(new Error("boom"));
    mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="fc-save-set"]').trigger("click");
    await create();
    await flushPromises();
    expect(stores.notice.error).toHaveBeenCalledWith(
      expect.stringContaining("Created Wren keepers, but it is empty"),
      expect.anything(),
    );
    expect(stores.notice.success).not.toHaveBeenCalled();
    expect(nameField().exists()).toBe(false);
  });

  it("drops a pending name prompt when the dialog is closed and opened again", async () => {
    mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="fc-save-set"]').trigger("click");
    expect(nameField().exists()).toBe(true);
    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    await flushPromises();
    expect(nameField().exists()).toBe(false);
  });
});
