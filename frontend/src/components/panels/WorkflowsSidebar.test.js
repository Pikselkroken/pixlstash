// The Workflows workspace's own rail (#2165): the places, the ComfyUI
// templates with their "N new" pill, the Type and Base model facets, and the
// footer's Add workflow. The thing that can silently go wrong is which state a
// row writes: a place must clear the facets and leave Templates, and the pill
// must count what was not seen, not what is listed.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const listWorkflowCards = vi.fn();
vi.mock("../../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  getWorkflowCard: vi.fn(),
  workflowCoverUrl: (cover) => cover?.url ?? "",
}));

const listTemplates = vi.fn();
const addTemplates = vi.fn();
vi.mock("../../api/comfyui", () => ({
  listTemplates: (...args) => listTemplates(...args),
  addTemplates: (...args) => addTemplates(...args),
}));

import WorkflowsSidebar from "./WorkflowsSidebar.vue";
import ModelMark from "../widgets/ModelMark.vue";
import SidebarResizeHandle from "./SidebarResizeHandle.vue";
import { useFilterStore } from "../../stores/useFilterStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useWorkflowsStore, RECENT_DAYS } from "../../stores/useWorkflowsStore";
import { useWorkflowTemplatesStore } from "../../stores/useWorkflowTemplatesStore";
import { sessionContext } from "../../utils/apiClient";

const DAY = 24 * 60 * 60 * 1000;
const daysAgo = (days) => new Date(Date.now() - days * DAY).toISOString();

function card(overrides = {}) {
  return {
    id: "auto:" + "a".repeat(64),
    name: "Cinematic portrait",
    type: "txt2img",
    type_label: "Text to Image",
    imported: false,
    models: [{ name: "realvisXL_v5.safetensors", kind: "checkpoint" }],
    loras: [],
    picture_count: 3,
    rating: 4,
    covers: [],
    saved_recipe_count: 0,
    defaults: [],
    base_topology: "d".repeat(64),
    topologies: ["d".repeat(64)],
    variant_count: 1,
    last_used: null,
    rank: 4,
    ghosts: 0,
    model_ghosts: 0,
    origin_category: "pictures",
    ...overrides,
  };
}

const CARDS = [
  card({
    id: "auto:1",
    name: "Portrait",
    type: "txt2img",
    type_label: "Text to Image",
    models: [{ name: "realvisXL_v5.safetensors", kind: "checkpoint" }],
    last_used: daysAgo(2),
  }),
  card({
    id: "auto:2",
    name: "Upscale pass",
    type: "upscale",
    type_label: "Upscale",
    models: [{ name: "flux1-dev.safetensors", kind: "checkpoint" }],
    last_used: daysAgo(RECENT_DAYS + 30),
  }),
  card({
    id: "auto:3",
    name: "Sketch",
    type: "txt2img",
    type_label: "Text to Image",
    models: [{ name: "flux1-dev.safetensors", kind: "checkpoint" }],
    last_used: null,
  }),
];

const template = (name) => ({
  key: `default/${name}`,
  module: "default",
  name,
  source: "builtin",
  title: name,
  media_type: "image",
  tags: [],
  model_names: [],
  files: [],
});

function listing(names, extra = {}) {
  return { comfyui_url: "http://comfy", reachable: true, templates: names.map(template), ...extra };
}

const LABEL_KEY = "pixlstash:workflowTemplatesSeen";
const STUBS = { "v-icon": true, WorkflowsComfyuiSlot: true };

async function mountSidebar({ configured = true, configRead = true, stubs = STUBS } = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const store = useWorkflowsStore();
  const templates = useWorkflowTemplatesStore();
  const filterStore = useFilterStore();
  filterStore.comfyuiConfigured = configured;
  filterStore.comfyuiConfigRead = configRead;
  filterStore.comfyuiUrl = configured ? "http://127.0.0.1:8188/" : "";
  await store.fetchCards();
  // The header slot has its own suite (WorkflowsComfyuiSlot.test.js).
  const wrapper = mount(WorkflowsSidebar, {
    global: { plugins: [pinia], stubs },
    attachTo: document.body,
  });
  await flushPromises();
  return { wrapper, store, templates, filterStore };
}

/** The button whose label is `label` (the row's text, count excluded). */
function row(wrapper, label) {
  const found = wrapper
    .findAll("button")
    .find((button) => button.find(".sidebar-list-label").exists() && button.find(".sidebar-list-label").text() === label);
  if (!found) throw new Error(`no row ${label}`);
  return found;
}

function templatesRow(wrapper) {
  return wrapper.findAll("button").find((button) => button.text().startsWith("Templates"));
}

// Every rail a test mounts (attached to the document) is taken down after it,
// so no test's rail, or its watchers, outlives it into the next.
enableAutoUnmount(afterEach);

beforeEach(() => {
  localStorage.clear();
  listWorkflowCards.mockReset();
  listWorkflowCards.mockResolvedValue({ cards: CARDS, one_offs: 0, hidden: 0 });
  listTemplates.mockReset();
  listTemplates.mockResolvedValue(listing(["a", "b"]));
  addTemplates.mockReset();
});

describe("frame", () => {
  // The library sidebar can be dragged wider or narrower; a rail fixed at its
  // default moved the edge, the toolbar and the header slot on every switch.
  it("takes the library sidebar's width", async () => {
    const { wrapper } = await mountSidebar();
    useUserPrefsStore().sidebarWidth = 300;
    await flushPromises();
    expect(wrapper.element.style.width).toBe("300px");
  });

  it("can be resized, as the Media sidebar can", async () => {
    const { wrapper } = await mountSidebar();
    expect(wrapper.findComponent(SidebarResizeHandle).exists()).toBe(true);
  });
});

describe("places", () => {
  it("lights All workflows at rest, and counts every card read", async () => {
    const { wrapper } = await mountSidebar();
    const all = row(wrapper, "All workflows");
    expect(all.attributes("aria-current")).toBe("page");
    expect(all.classes()).toContain("active");
    expect(all.find(".sidebar-list-count").text()).toBe("3");
  });

  it("picking Favourites sets the filter and lights only that row", async () => {
    const { wrapper, store } = await mountSidebar();
    await row(wrapper, "Favourites").trigger("click");
    expect(store.filters.favouritesOnly).toBe(true);
    expect(store.place).toBe("favourites");
    expect(row(wrapper, "Favourites").attributes("aria-current")).toBe("page");
    expect(row(wrapper, "All workflows").attributes("aria-current")).toBeUndefined();

    // The place is the only control for the flag now, so it must also turn it off.
    await row(wrapper, "All workflows").trigger("click");
    expect(store.filters.favouritesOnly).toBe(false);
    expect(row(wrapper, "Favourites").attributes("aria-current")).toBeUndefined();
  });

  it("Recently used keeps only cards used in the last RECENT_DAYS", async () => {
    const { wrapper, store } = await mountSidebar();
    expect(row(wrapper, "Recently used").find(".sidebar-list-count").text()).toBe("1");
    await row(wrapper, "Recently used").trigger("click");
    expect(store.filters.recent).toBe(true);
    expect(store.filteredCards.map((c) => c.name)).toEqual(["Portrait"]);
  });

  it("picking a place clears the Type and Base model facets", async () => {
    const { wrapper, store } = await mountSidebar();
    await row(wrapper, "Text to Image").trigger("click");
    await row(wrapper, "flux1-dev.safetensors").trigger("click");
    await row(wrapper, "Recently used").trigger("click");
    expect(store.filters.type).toBeNull();
    expect(store.filters.checkpoint).toBeNull();
    expect(store.filters.recent).toBe(true);
  });
});

describe("place counts before the grid is read", () => {
  it("are blank rather than 0 while the first read is out", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    listWorkflowCards.mockReturnValue(new Promise(() => {}));
    const wrapper = mount(WorkflowsSidebar, {
      global: { plugins: [pinia], stubs: { "v-icon": true } },
    });
    await flushPromises();
    expect(row(wrapper, "All workflows").find(".sidebar-list-count").exists()).toBe(
      false,
    );
    wrapper.unmount();
  });
});

describe("facets", () => {
  it("toggles the Type facet, and a second click takes it off", async () => {
    const { wrapper, store } = await mountSidebar();
    const type = row(wrapper, "Text to Image");
    await type.trigger("click");
    expect(store.filters.type).toBe("txt2img");
    expect(row(wrapper, "Text to Image").classes()).toContain("active");
    // The place stays where it was: a facet narrows, it does not move the place.
    expect(row(wrapper, "All workflows").attributes("aria-current")).toBe("page");
    await row(wrapper, "Text to Image").trigger("click");
    expect(store.filters.type).toBeNull();
    expect(row(wrapper, "Text to Image").attributes("aria-current")).toBeUndefined();
  });

  it("toggles the Base model facet by its file name, shown by its label", async () => {
    const { wrapper, store } = await mountSidebar();
    await row(wrapper, "flux1-dev.safetensors").trigger("click");
    expect(store.filters.checkpoint).toBe("flux1-dev.safetensors");
    expect(row(wrapper, "flux1-dev.safetensors").attributes("aria-current")).toBe("page");
  });

  // An icon is a name in a font: the glyph slot carries the name as its text.
  it("gives each Type row its own glyph, and an unknown type the generic one", async () => {
    listWorkflowCards.mockResolvedValue({
      cards: [...CARDS, card({ id: "auto:4", type: "relight", type_label: "Relight" })],
    });
    const { wrapper } = await mountSidebar({
      stubs: { ...STUBS, "v-icon": { template: "<i><slot /></i>" } },
    });
    const glyph = (label) => row(wrapper, label).find(".sidebar-list-icon").text();
    expect(glyph("Text to Image")).toBe("mdi-text-box-outline");
    expect(glyph("Upscale")).toBe("mdi-magnify-plus-outline");
    expect(glyph("Relight")).toBe("mdi-sitemap-outline");
  });

  // The rail draws the card strip's tile, so a row and the cards it narrows
  // to wear one mark: the same payload, named, from the card's own model.
  it("draws each Base model row with that model's card mark", async () => {
    listWorkflowCards.mockResolvedValue({
      cards: [
        card({
          id: "auto:1",
          models: [
            {
              name: "flux1-dev.safetensors",
              title: "FLUX.1 dev",
              kind: "checkpoint",
              mark: ["FLUX.1", "dev"],
              mark_colors: ["#333333"],
              mark_icon: "flux",
              mark_band: true,
            },
          ],
        }),
      ],
    });
    const { wrapper } = await mountSidebar();
    const glyph = row(wrapper, "FLUX.1 dev").find(".sidebar-list-icon");
    const mark = glyph.findComponent(ModelMark);
    expect(mark.props("named")).toBe(true);
    expect(mark.props("row")).toMatchObject({
      filename: "flux1-dev.safetensors",
      mark: ["FLUX.1", "dev"],
      mark_colors: ["#333333"],
      mark_icon: "flux",
      mark_band: true,
    });
    // The release tile itself, not the generated initials it falls back to.
    expect(glyph.find(".mmark-named--release").exists()).toBe(true);
  });

  it("picking a facet while Templates is on goes back to the grid", async () => {
    const { wrapper, store, templates } = await mountSidebar();
    await templatesRow(wrapper).trigger("click");
    expect(templates.shown).toBe(true);
    await row(wrapper, "Text to Image").trigger("click");
    expect(templates.shown).toBe(false);
    expect(store.filters.type).toBe("txt2img");
  });
});

describe("Base model order", () => {
  const SORT_KEY = "pixlstash:workflowsBaseModelSort";
  // Most-used is not first alphabetically, and one name is lower case and
  // carries a number, so case-folding and numeric order both show.
  const cardsUsing = (...names) =>
    names.map((name, i) => card({ id: `auto:${i}`, models: [{ name, kind: "checkpoint" }] }));
  const baseLabels = (wrapper) =>
    wrapper.findAll(".sidebar-list-label").map((label) => label.text()).filter((text) => text.endsWith(".safetensors"));

  beforeEach(() => {
    listWorkflowCards.mockResolvedValue({
      cards: cardsUsing("Zeta.safetensors", "Zeta.safetensors", "model10.safetensors", "Alpha.safetensors", "model9.safetensors"),
    });
  });

  it("reads A-Z by default, ignoring case and counting numbers as numbers", async () => {
    const { wrapper } = await mountSidebar();
    expect(baseLabels(wrapper)).toEqual([
      "Alpha.safetensors",
      "model9.safetensors",
      "model10.safetensors",
      "Zeta.safetensors",
    ]);
  });

  it("the toggle sorts by count and remembers it", async () => {
    const { wrapper } = await mountSidebar();
    await wrapper.find(".wfs-sort").trigger("click");
    expect(baseLabels(wrapper)[0]).toBe("Zeta.safetensors");
    expect(localStorage.getItem(SORT_KEY)).toBe("count");
    await wrapper.find(".wfs-sort").trigger("click");
    expect(baseLabels(wrapper)[0]).toBe("Alpha.safetensors");
    expect(localStorage.getItem(SORT_KEY)).toBe("name");
  });

  it("opens in the remembered order", async () => {
    localStorage.setItem(SORT_KEY, "count");
    const { wrapper } = await mountSidebar();
    expect(baseLabels(wrapper)[0]).toBe("Zeta.safetensors");
  });
});

describe("the Templates row", () => {
  it("sets the shown flag, and lights only Templates", async () => {
    const { wrapper, templates } = await mountSidebar();
    await templatesRow(wrapper).trigger("click");
    expect(templates.shown).toBe(true);
    expect(templatesRow(wrapper).attributes("aria-current")).toBe("page");
    expect(row(wrapper, "All workflows").attributes("aria-current")).toBeUndefined();
  });

  it("counts the templates once read", async () => {
    const { wrapper } = await mountSidebar();
    expect(templatesRow(wrapper).find(".sidebar-list-count").text()).toBe("2");
  });

  it("shows a dash, not a zero, when ComfyUI did not answer", async () => {
    listTemplates.mockResolvedValue(listing([], { reachable: false, detail: "down" }));
    const { wrapper } = await mountSidebar();
    expect(templatesRow(wrapper).find(".sidebar-list-count").text()).toBe("–");
    expect(templatesRow(wrapper).find(".wfs-new").exists()).toBe(false);
  });

  it("shows a dash and no new pill with no ComfyUI set up, whatever the default address serves", async () => {
    const { wrapper } = await mountSidebar({ configured: false });
    expect(templatesRow(wrapper).find(".sidebar-list-count").text()).toBe("–");
    expect(templatesRow(wrapper).find(".wfs-new").exists()).toBe(false);
  });

  it("shows nothing rather than a dash before the config is read", async () => {
    const { wrapper } = await mountSidebar({ configured: false, configRead: false });
    expect(templatesRow(wrapper).find(".sidebar-list-count").text()).toBe("");
  });

  it("does not read the list again when the config's first read names the address", async () => {
    const { filterStore } = await mountSidebar({ configured: false, configRead: false });
    filterStore.comfyuiConfigured = true;
    filterStore.comfyuiUrl = "http://127.0.0.1:8188/";
    filterStore.comfyuiConfigRead = true;
    await flushPromises();
    expect(listTemplates).toHaveBeenCalledTimes(1);
  });

  it("reads the list again, past the server's keep, when the address changes", async () => {
    const { filterStore } = await mountSidebar();
    expect(listTemplates).toHaveBeenCalledTimes(1);
    filterStore.comfyuiUrl = "http://192.0.2.20:8188/";
    await flushPromises();
    expect(listTemplates).toHaveBeenCalledTimes(2);
    expect(listTemplates).toHaveBeenLastCalledWith({ refresh: true });
  });

  it("reads the list once when the rail mounts, and not again on a later visit", async () => {
    const { wrapper } = await mountSidebar();
    expect(listTemplates).toHaveBeenCalledTimes(1);
    // App.vue mounts the rail with v-if, so each visit to /workflows is a new mount.
    const pinia = wrapper.vm.$pinia;
    wrapper.unmount();
    mount(WorkflowsSidebar, { global: { plugins: [pinia], stubs: STUBS } });
    await flushPromises();
    expect(listTemplates).toHaveBeenCalledTimes(1);
  });
});

describe("the N new pill", () => {
  it("does not announce the whole list on the very first read", async () => {
    const { wrapper } = await mountSidebar();
    expect(templatesRow(wrapper).find(".wfs-new").exists()).toBe(false);
  });

  it("counts the keys the browser has not seen, and marks them seen when Templates opens", async () => {
    // Seen so far: a, b. ComfyUI now also serves c.
    localStorage.setItem(LABEL_KEY, JSON.stringify(["default/a", "default/b"]));
    listTemplates.mockResolvedValue(listing(["a", "b", "c"]));
    const { wrapper, templates } = await mountSidebar();
    expect(templatesRow(wrapper).find(".wfs-new").text()).toBe("1 new");

    await templatesRow(wrapper).trigger("click");
    await flushPromises();
    expect(templatesRow(wrapper).find(".wfs-new").exists()).toBe(false);
    expect(JSON.parse(localStorage.getItem(LABEL_KEY))).toEqual(
      expect.arrayContaining(["default/a", "default/b", "default/c"]),
    );

    // Leaving Templates and a later re-read that finds nothing new keeps it at none.
    await row(wrapper, "All workflows").trigger("click");
    expect(templates.shown).toBe(false);
    listTemplates.mockResolvedValue(listing(["a", "b", "c"]));
    await templates.fetchTemplates({ refresh: true });
    await flushPromises();
    expect(templatesRow(wrapper).find(".wfs-new").exists()).toBe(false);
  });

  it("does not take an offline first read as having seen the catalogue", async () => {
    listTemplates.mockResolvedValue({ templates: [], reachable: false });
    const { wrapper, templates } = await mountSidebar();
    expect(localStorage.getItem(LABEL_KEY)).toBeNull();
    // ComfyUI comes up: this is the first real sight, so nothing is "new".
    listTemplates.mockResolvedValue(listing(["a", "b", "c"]));
    await templates.fetchTemplates({ refresh: true });
    await flushPromises();
    expect(templatesRow(wrapper).find(".wfs-new").exists()).toBe(false);
  });

  // Nor does the Templates place being open over that empty read: whether it
  // was opened onto it, or was open while ComfyUI was asked again.
  it.each([
    ["opened onto it", async () => {}],
    [
      "open while it is read again",
      async (templates) => {
        await templates.fetchTemplates({ refresh: true });
        await flushPromises();
      },
    ],
  ])("does not take an offline read with Templates %s as having seen the catalogue", async (_, whileOpen) => {
    listTemplates.mockResolvedValue({ templates: [], reachable: false });
    const { wrapper, templates } = await mountSidebar();
    await templatesRow(wrapper).trigger("click");
    expect(templates.shown).toBe(true);
    await whileOpen(templates);
    await row(wrapper, "All workflows").trigger("click");
    expect(localStorage.getItem(LABEL_KEY)).toBeNull();

    listTemplates.mockResolvedValue(listing(["a", "b", "c"]));
    await templates.fetchTemplates({ refresh: true });
    await flushPromises();
    expect(templatesRow(wrapper).find(".wfs-new").exists()).toBe(false);
  });

  it("counts a template that appears on a later read as new", async () => {
    const { wrapper, templates } = await mountSidebar();
    expect(templatesRow(wrapper).find(".wfs-new").exists()).toBe(false);
    listTemplates.mockResolvedValue(listing(["a", "b", "d"]));
    await templates.fetchTemplates({ refresh: true });
    await flushPromises();
    expect(templatesRow(wrapper).find(".wfs-new").text()).toBe("1 new");
  });
});

describe("Add workflow", () => {
  it("bumps addRequest each press, for WorkflowsView to open its picker", async () => {
    const { wrapper, store } = await mountSidebar();
    expect(store.addRequest).toBe(0);
    await wrapper.find(".wfs-add").trigger("click");
    expect(store.addRequest).toBe(1);
    await wrapper.find(".wfs-add").trigger("click");
    expect(store.addRequest).toBe(2);
  });
});

describe("viewName", () => {
  it("is the place's name when nothing else is lit", async () => {
    const { store } = await mountSidebar();
    expect(store.viewName).toBe("All workflows");
    store.setPlace("favourites");
    expect(store.viewName).toBe("Favourites");
    store.setPlace("recent");
    expect(store.viewName).toBe("Recently used");
  });

  it("is the Type label when a Type facet is set, over the place", async () => {
    const { store } = await mountSidebar();
    store.setFilters({ type: "upscale" });
    expect(store.viewName).toBe("Upscale");
  });

  it("is the base model's display label when a Base model facet is set", async () => {
    const { store } = await mountSidebar();
    store.setFilters({ checkpoint: "flux1-dev.safetensors" });
    expect(store.viewName).toBe("flux1-dev.safetensors");
  });

  it("is ComfyUI templates while the Templates place is on, over any facet", async () => {
    const { store, templates } = await mountSidebar();
    store.setFilters({ type: "upscale" });
    templates.shown = true;
    expect(store.viewName).toBe("ComfyUI templates");
  });
});

describe("the favourites chip", () => {
  it("is gone: Favourites is a place, not a filter chip", async () => {
    const { store } = await mountSidebar();
    store.setFilters({ favouritesOnly: true });
    expect(store.filterChips.some((chip) => chip.key === "favourites")).toBe(false);
  });
});

describe("the ComfyUI header slot", () => {
  it("is drawn for the owner", async () => {
    const { wrapper } = await mountSidebar();
    expect(wrapper.findComponent({ name: "WorkflowsComfyuiSlot" }).exists()).toBe(true);
  });

  it("is not drawn in a shared view, which cannot read the owner's ComfyUI", async () => {
    sessionContext.value = { scope: "READ" };
    try {
      const { wrapper } = await mountSidebar();
      expect(wrapper.findComponent({ name: "WorkflowsComfyuiSlot" }).exists()).toBe(false);
    } finally {
      sessionContext.value = null;
    }
  });
});
