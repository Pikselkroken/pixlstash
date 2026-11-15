// Edit person: the reference cluster is picked from a drop-down in the
// Reference Images header, a whole cluster at a time, or none.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const api = vi.hoisted(() => ({
  createCharacter: vi.fn(),
  patchCharacter: vi.fn(),
  getReferencePictures: vi.fn(async () => ({ reference_picture_ids: [] })),
  getFaceClusters: vi.fn(),
  setReferenceCluster: vi.fn(async () => ({ picture_ids: [] })),
}));
vi.mock("../../api/characters", () => api);
vi.mock("../../api/pictures", () => ({
  listPicturesByIds: vi.fn(async () => []),
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
}));
vi.mock("../../api/modelShelf", () => ({ listAdapters: vi.fn(async () => []) }));

import CharacterEditor from "./CharacterEditor.vue";

const cluster = (ids, extra = {}) => ({
  members: ids.map((picture_id) => ({ picture_id, centrality: 0.8, score: null })),
  size: ids.length,
  cohesion: 0.6,
  reference_share: 0,
  is_reference: false,
  similarity_to_reference: 0.5,
  from_sheets: false,
  ...extra,
});

beforeEach(() => {
  vi.clearAllMocks();
  setActivePinia(createPinia());
});

describe("CharacterEditor reference cluster", () => {
  it("sets a whole cluster, or none, from the drop-down", async () => {
    api.getFaceClusters.mockResolvedValue({
      faces: 7,
      reference_cluster: [1, 2, 3],
      clusters: [cluster([1, 2, 3], { is_reference: true }), cluster([4, 5, 6])],
      unclustered: [],
    });
    const wrapper = mount(CharacterEditor, {
      props: { open: true, character: { id: 7, name: "Wren", project_ids: [] } },
      global: { stubs: { teleport: true } },
      attachTo: document.body,
    });
    await flushPromises();
    expect(api.getFaceClusters).toHaveBeenCalledWith(7, 0.45);
    const select = wrapper.find('[data-testid="editor-reference-cluster"] select');
    expect(select.element.value).toBe("c0");
    await select.setValue("c1");
    await flushPromises();
    expect(api.setReferenceCluster).toHaveBeenLastCalledWith(7, [4, 5, 6]);
    await select.setValue("auto");
    await flushPromises();
    expect(api.setReferenceCluster).toHaveBeenLastCalledWith(7, []);
    wrapper.unmount();
  });
});
