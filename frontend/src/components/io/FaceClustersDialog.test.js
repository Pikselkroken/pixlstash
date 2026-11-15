// "Faces and reference cluster…": the rail picks a cluster to inspect, the
// slider trims its rim, and saving sends exactly the faces that count.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const api = vi.hoisted(() => ({
  getFaceClusters: vi.fn(),
  setReferenceCluster: vi.fn(),
}));
vi.mock("../../api/characters", () => api);
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
}));

import FaceClustersDialog from "./FaceClustersDialog.vue";

const member = (picture_id, centrality, score = null) => ({
  picture_id,
  centrality,
  score,
  format: "png",
});

function clusters({ reference }) {
  const one = {
    members: [member(1, 0.88, 2), member(2, 0.8), member(3, 0.7), member(4, 0.52, 5)],
    size: 4,
    cohesion: 0.61,
    reference_share: reference ? 0.75 : 0,
    is_reference: reference,
    similarity_to_reference: 1,
    from_sheets: false,
  };
  const two = {
    members: [member(11, 0.84), member(12, 0.7), member(13, 0.6)],
    size: 3,
    cohesion: 0.57,
    reference_share: 0,
    is_reference: false,
    similarity_to_reference: 0.41,
    from_sheets: true,
  };
  return {
    threshold: 0.45,
    faces: 8,
    reference_cluster: reference ? [1, 2, 3] : null,
    clusters: [one, two],
    unclustered: [{ picture_id: 99, score: null, similarity_to_reference: 0.1 }],
  };
}

function mountDialog() {
  return mount(FaceClustersDialog, {
    props: { open: true, character: { id: 7, name: "Wren" } },
    global: { stubs: { teleport: true } },
    attachTo: document.body,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  api.setReferenceCluster.mockResolvedValue({ picture_ids: [] });
});

describe("FaceClustersDialog", () => {
  it("opens on the reference cluster with the slider at its lowest saved face", async () => {
    api.getFaceClusters.mockResolvedValue(clusters({ reference: true }));
    const wrapper = mountDialog();
    await flushPromises();
    const rows = wrapper.findAll('[data-testid="fc-row"]');
    expect(rows.map((r) => r.attributes("aria-selected"))).toEqual(["true", "false"]);
    expect(rows[0].text()).toContain("Reference");
    expect(rows[1].text()).toContain("New · from sheets");
    // Saved 1-3; face 4 sits under the cut, so 3 of 4 count and nothing to save.
    expect(wrapper.find('[data-testid="fc-count"]').text()).toContain("3 of 4");
    expect(wrapper.find('[data-testid="fc-save"]').exists()).toBe(false);
    const faces = wrapper.findAll('[data-testid="fc-face"]');
    expect(faces[3].classes()).toContain("fc-face--out");
    expect(faces[0].attributes("aria-label")).toBe(
      "Face 1 of 4 · 0.88 to the centre · 2★",
    );
    wrapper.unmount();
  });

  it("saves only the faces above the cut, and Clear sends none", async () => {
    api.getFaceClusters.mockResolvedValue(clusters({ reference: true }));
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="fc-cut"]').setValue("0.75");
    await wrapper.find('[data-testid="fc-save"]').trigger("click");
    await flushPromises();
    expect(api.setReferenceCluster).toHaveBeenLastCalledWith(7, [1, 2]);
    expect(wrapper.emitted("changed")).toHaveLength(1);
    await wrapper.find('[data-testid="fc-clear"]').trigger("click");
    await flushPromises();
    expect(api.setReferenceCluster).toHaveBeenLastCalledWith(7, []);
    wrapper.unmount();
  });

  it("picks another cluster with all of its faces, saying what that does", async () => {
    api.getFaceClusters.mockResolvedValue(clusters({ reference: true }));
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.findAll('[data-testid="fc-row"]')[1].trigger("click");
    const compare = wrapper.find('[data-testid="fc-compare"]');
    expect(compare.text()).toContain("Below Normal (0.45), so this counts as someone else.");
    expect(compare.text()).toContain("It would join the reference at Loose.");
    expect(compare.text()).toContain("The 3 in Cluster 1 then rank as low likeness.");
    await wrapper.find('[data-testid="fc-pick"]').trigger("click");
    await flushPromises();
    expect(api.setReferenceCluster).toHaveBeenLastCalledWith(7, [11, 12, 13]);
    wrapper.unmount();
  });

  it("selects a face on a click, zooms on a double-click, and Escape closes the zoom", async () => {
    api.getFaceClusters.mockResolvedValue(clusters({ reference: true }));
    const wrapper = mountDialog();
    await flushPromises();
    const faces = () => wrapper.findAll('[data-testid="fc-face"]');
    await faces()[2].trigger("click");
    expect(faces()[2].attributes("aria-pressed")).toBe("true");
    expect(wrapper.find('[data-testid="fc-picked"]').text()).toContain("Face 3 of 4");
    expect(document.querySelector('[data-testid="zoom-popout"]')).toBeNull();
    await faces()[2].trigger("dblclick");
    await flushPromises();
    expect(document.querySelector('[data-testid="zoom-popout"]')).not.toBeNull();
    // Focus left on the face: Escape still closes the zoom, not the dialog.
    faces()[2].element.focus();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(document.querySelector('[data-testid="zoom-popout"]')).toBeNull();
    expect(wrapper.emitted("close")).toBeUndefined();
    wrapper.unmount();
  });

  it("asks again at another strictness", async () => {
    api.getFaceClusters.mockResolvedValue(clusters({ reference: false }));
    const wrapper = mountDialog();
    await flushPromises();
    expect(api.getFaceClusters).toHaveBeenLastCalledWith(7, 0.45);
    expect(wrapper.find('[data-testid="fc-compare"]').exists()).toBe(false);
    const strict = wrapper
      .find('[data-testid="fc-strictness"]')
      .findAll("button")
      .find((b) => b.text() === "Strict");
    await strict.trigger("click");
    await flushPromises();
    expect(api.getFaceClusters).toHaveBeenLastCalledWith(7, 0.55);
    wrapper.unmount();
  });
});
