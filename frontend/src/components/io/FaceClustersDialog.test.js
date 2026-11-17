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

// Every face of Wren against face 12 (Cluster 2): face 2 of Cluster 1 sits
// above the 0.45 cut, face 3 below it.
const CENTRED_ON_12 = [
  member(12, 1),
  member(11, 0.8),
  member(2, 0.6),
  member(13, 0.5),
  member(3, 0.4),
  member(1, 0.35, 2),
  member(4, 0.2, 5),
  member(99, 0.1),
];

/** The clusters, plus `centred` when a centre is asked for. */
function answer(reference, centre) {
  const body = clusters({ reference });
  body.centred = centre == null ? null : CENTRED_ON_12;
  return body;
}

/** Cluster 2, face 12 picked in the field, then "Centre on this face". */
async function centreOn12(wrapper) {
  await wrapper.findAll('[data-testid="fc-row"]')[1].trigger("click");
  await wrapper.findAll('[data-testid="fc-face"]')[1].trigger("click");
  expect(wrapper.find('[data-testid="fc-picked"]').text()).toContain("0.70 to the centre");
  await wrapper.find('[data-testid="fc-centre"]').trigger("click");
  await flushPromises();
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
    expect(wrapper.find('[data-testid="fc-save"]').attributes("disabled")).toBeDefined();
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

  it("zooms the sidebar thumbnails, stepping through the list each sits in", async () => {
    api.getFaceClusters.mockResolvedValue(clusters({ reference: true }));
    const wrapper = mountDialog();
    await flushPromises();
    const popout = () => document.querySelector('[data-testid="zoom-popout"]');
    const caption = () => popout()?.querySelector(".zp-cap")?.textContent.trim();
    const key = async (k) => {
      popout().dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
      await flushPromises();
    };

    // The rail: cluster centres, in rail order.
    await wrapper.findAll('[data-testid="fc-row-face"]')[0].trigger("dblclick");
    await flushPromises();
    expect(caption()).toBe(
      "Cluster 1 (reference) · face 1 of 4, most typical · 0.88 to the centre · 2★",
    );
    await key("ArrowRight");
    expect(caption()).toBe("Cluster 2 · face 1 of 3, most typical · 0.84 to the centre · unrated");
    await key("Escape");
    expect(popout()).toBeNull();

    // Matched no one. A plain wheel scrolls; Ctrl+wheel up zooms.
    const strip = wrapper.find('[data-testid="fc-strip-face"]');
    const wheel = async (init) => {
      strip.element.dispatchEvent(
        new WheelEvent("wheel", { deltaY: -100, bubbles: true, cancelable: true, ...init }),
      );
      await flushPromises();
    };
    await wheel({});
    expect(popout()).toBeNull();
    await wheel({ ctrlKey: true });
    expect(caption()).toBe("Matched no one · face 1 of 1 · unrated");
    // The picture takes no focus, so closing lands on the field, not <body>.
    await key("Escape");
    expect(document.activeElement.dataset.testid).toBe("fc-face");

    // Left out: face 4 sits under the saved cut.
    await wrapper.find('[data-testid="fc-rim-face"]').trigger("dblclick");
    await flushPromises();
    expect(caption()).toBe(
      "Cluster 1 (reference) · face 4 of 4, least typical · 0.52 to the centre · 5★",
    );
    await key("Escape");

    // Z on the rail zooms the selected cluster's centre.
    await wrapper.findAll('[data-testid="fc-row"]')[1].trigger("click");
    await wrapper.find('[data-testid="fc-rail"]').trigger("keydown", { key: "z" });
    await flushPromises();
    expect(caption()).toBe("Cluster 2 · face 1 of 3, most typical · 0.84 to the centre · unrated");
    await key("Escape");
    expect(wrapper.emitted("close")).toBeUndefined();
    wrapper.unmount();
  });

  it("zooms the left-out faces behind +n, from the fifth one", async () => {
    const many = clusters({ reference: false });
    many.clusters[0].members = Array.from({ length: 10 }, (_, i) =>
      member(100 + i, Number((0.9 - i * 0.02).toFixed(2))),
    );
    many.clusters[0].size = 10;
    api.getFaceClusters.mockResolvedValue(many);
    const wrapper = mountDialog();
    await flushPromises();
    // Below 0.85, faces 4-10 are left out: four shown, +3.
    await wrapper.find('[data-testid="fc-cut"]').setValue("0.85");
    const more = wrapper.find('[data-testid="fc-more"]');
    expect(more.text()).toBe("+3");
    await more.trigger("click");
    await flushPromises();
    const caption = () =>
      document.querySelector('[data-testid="zoom-popout"] .zp-cap').textContent.trim();
    expect(caption()).toBe("Cluster 1 · face 8 of 10 · 0.76 to the centre · unrated");
    // Stepping back stops at the first face left out, not the field's faces.
    const popout = document.querySelector('[data-testid="zoom-popout"]');
    for (let i = 0; i < 6; i++) {
      popout.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
      await flushPromises();
    }
    expect(caption()).toBe("Cluster 1 · face 4 of 10 · 0.84 to the centre · unrated");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    wrapper.unmount();
  });

  it("lets the slider reach every face of a cluster longer than the field shows", async () => {
    const many = clusters({ reference: false });
    // 70 faces, 0.90 down to 0.21: only 60 are drawn, the last ten below them.
    many.clusters[0].members = Array.from({ length: 70 }, (_, i) =>
      member(100 + i, Number((0.9 - i * 0.01).toFixed(2))),
    );
    many.clusters[0].size = 70;
    api.getFaceClusters.mockResolvedValue(many);
    const wrapper = mountDialog();
    await flushPromises();
    const slider = wrapper.find('[data-testid="fc-cut"]');
    expect(Number(slider.attributes("min"))).toBeLessThanOrEqual(0.21);
    expect(wrapper.find('[data-testid="fc-count"]').text()).toContain("70 of 70");
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

  it("centres on a face: every face of the person, ranked against it", async () => {
    api.getFaceClusters.mockImplementation(async (_id, _t, centre) => answer(true, centre));
    const wrapper = mountDialog();
    await flushPromises();
    await centreOn12(wrapper);
    expect(api.getFaceClusters).toHaveBeenLastCalledWith(7, 0.45, 12);
    const faces = wrapper.findAll('[data-testid="fc-face"]');
    expect(faces).toHaveLength(8);
    expect(faces[0].attributes("aria-label")).toBe("Face 1 of 8 · 1.00 to the chosen face");
    expect(wrapper.find('[data-testid="fc-centred-title"]').text()).toContain("Centred on face");
    expect(wrapper.find('[data-testid="fc-uncentre"]').text()).toBe("Back to Cluster 2's centre");
    expect(wrapper.find('[data-testid="fc-compare"]').exists()).toBe(false);
    // The chosen face offers no centring on itself; the others do.
    expect(wrapper.find('[data-testid="fc-centre"]').exists()).toBe(false);
    await faces[2].trigger("click");
    expect(wrapper.find('[data-testid="fc-centre"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("starts the centred slider at the strictness and saves the faces above it", async () => {
    api.getFaceClusters.mockImplementation(async (_id, _t, centre) => answer(true, centre));
    const wrapper = mountDialog();
    await flushPromises();
    await centreOn12(wrapper);
    expect(Number(wrapper.find('[data-testid="fc-cut"]').element.value)).toBe(0.45);
    expect(wrapper.find('[data-testid="fc-count"]').text()).toContain("4 of 8 of Wren's faces");
    const pick = wrapper.find('[data-testid="fc-save-centred"]');
    expect(pick.text()).toBe("Make these 4 faces Wren's reference");
    await pick.trigger("click");
    await flushPromises();
    // Face 2 is Cluster 1's: the centred view reaches across clusters.
    expect(api.setReferenceCluster).toHaveBeenLastCalledWith(7, [12, 11, 2, 13]);
    // Saving reloads: back on the clusters.
    expect(wrapper.find('[data-testid="fc-uncentre"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("goes back to the cluster by Back, the selected rail row, or strictness", async () => {
    api.getFaceClusters.mockImplementation(async (_id, _t, centre) => answer(true, centre));
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.findAll('[data-testid="fc-row"]')[1].trigger("click");
    await wrapper.find('[data-testid="fc-cut"]').setValue("0.65");
    const count = () => wrapper.find('[data-testid="fc-count"]').text();
    expect(count()).toContain("2 of 3 pictures count");

    const centred = async () => {
      await centreOn12(wrapper);
      await wrapper.find('[data-testid="fc-cut"]').setValue("0.15");
      expect(count()).toContain("7 of 8");
    };
    await centred();
    await wrapper.find('[data-testid="fc-uncentre"]').trigger("click");
    expect(count()).toContain("2 of 3 pictures count");
    expect(wrapper.find('[data-testid="fc-cut"]').element.value).toBe("0.65");

    await centred();
    await wrapper.findAll('[data-testid="fc-row"]')[1].trigger("click");
    expect(count()).toContain("2 of 3 pictures count");
    expect(wrapper.find('[data-testid="fc-cut"]').element.value).toBe("0.65");

    await centred();
    const loose = wrapper
      .find('[data-testid="fc-strictness"]')
      .findAll("button")
      .find((b) => b.text() === "Loose");
    await loose.trigger("click");
    await flushPromises();
    expect(api.getFaceClusters).toHaveBeenLastCalledWith(7, 0.35);
    expect(wrapper.find('[data-testid="fc-uncentre"]').exists()).toBe(false);
    expect(count()).toContain("3 of 4 pictures count"); // a reload: Cluster 1 at its saved cut
    wrapper.unmount();
  });

  it("centres on the focused face with C, and drops an answer a strictness change outran", async () => {
    let release;
    api.getFaceClusters.mockImplementation((_id, _t, centre) =>
      centre == null
        ? Promise.resolve(answer(true, null))
        : new Promise((resolve) => (release = () => resolve(answer(true, centre)))),
    );
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.findAll('[data-testid="fc-row"]')[1].trigger("click");
    const faces = () => wrapper.findAll('[data-testid="fc-face"]');
    expect(faces()[0].attributes("aria-keyshortcuts")).toBe("Z Enter C");
    await faces()[1].trigger("click");
    faces()[1].element.focus();
    await faces()[1].trigger("keydown", { key: "c" });
    expect(api.getFaceClusters).toHaveBeenLastCalledWith(7, 0.45, 12);
    release();
    await flushPromises();
    expect(faces()).toHaveLength(8);
    // Focus was in the field, so it follows the new centre.
    expect(document.activeElement).toBe(faces()[0].element);

    // Back, centre again, and change strictness before the answer lands.
    await wrapper.find('[data-testid="fc-uncentre"]').trigger("click");
    await faces()[1].trigger("click");
    await faces()[1].trigger("keydown", { key: "C" });
    const strict = wrapper
      .find('[data-testid="fc-strictness"]')
      .findAll("button")
      .find((b) => b.text() === "Strict");
    await strict.trigger("click");
    await flushPromises();
    release();
    await flushPromises();
    expect(wrapper.find('[data-testid="fc-uncentre"]').exists()).toBe(false);
    expect(faces()).toHaveLength(4);
    wrapper.unmount();
  });
});
