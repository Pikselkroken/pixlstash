// The 2.0 training tour (#2145). What matters most: the tour never starts a
// run. Next on the Train dialog step closes it, and Enter pressed inside the
// dialog (whose Enter-accepts would press Start) is taken as Next instead.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick, ref, watch } from "vue";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VTooltip: { name: "v-tooltip", template: "<span><slot /></span>" },
}));
vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  isReadOnly: ref(false),
}));
vi.mock("../../api/config", () => ({ patchUserConfig: vi.fn(async () => ({})) }));

import WhatsChangedTour from "./WhatsChangedTour.vue";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useWhatsChangedStore } from "../../stores/useWhatsChangedStore";

const SUBJECT = { type: "character", id: 2, name: "Example", count: 812 };

/** A fake app: the tour's moves only flip these flags. */
function fakeApp() {
  const state = ref({ selected: false, menu: false, train: false, models: false });
  const actions = {
    select: vi.fn(() => (state.value.selected = true)),
    openMenu: vi.fn(() => (state.value.menu = true)),
    closeMenu: vi.fn(() => (state.value.menu = false)),
    openTrain: vi.fn(() => {
      state.value.menu = false;
      state.value.train = true;
    }),
    closeTrain: vi.fn(() => (state.value.train = false)),
    goModels: vi.fn(() => (state.value.models = true)),
    isSelected: () => state.value.selected,
    trainOpen: () => state.value.train,
    onModels: () => state.value.models,
  };
  return { state, actions };
}

let wrapper;
let dialog;

beforeEach(() => {
  setActivePinia(createPinia());
  useEntityListsStore().lists = {
    characters: [{ id: 2, name: "Example", image_count: 812 }],
    sets: [],
    projects: [],
  };
  // Stands in for the Train dialog: a focusable control inside it, and the
  // dialog's own Enter-accepts (AppDialog presses Start on a plain Enter).
  dialog = document.createElement("div");
  dialog.innerHTML = '<input class="field" />';
  document.body.appendChild(dialog);
});

afterEach(() => {
  wrapper?.unmount();
  dialog.remove();
});

async function start(step = 1) {
  const app = fakeApp();
  const store = useWhatsChangedStore();
  store.card = true;
  store.tourStep = step;
  wrapper = mount(WhatsChangedTour, {
    props: { actions: app.actions },
    attachTo: document.body,
  });
  await flushPromises();
  return { app, store };
}

const press = () => wrapper.find('[data-testid="tour-next"]').trigger("click");

describe("the training tour", () => {
  it("names the person with the most pictures", async () => {
    await start(1);
    expect(wrapper.text()).toContain("We picked Example");
    await press();
    await flushPromises();
    expect(wrapper.text()).toContain("Example's 812 pictures");
  });

  it("Next walks the app through the four steps and never starts a run", async () => {
    const { app, store } = await start(1);
    await press();
    await flushPromises();
    expect(app.actions.select).toHaveBeenCalledWith(SUBJECT);
    expect(app.actions.openMenu).toHaveBeenCalledWith(SUBJECT);
    expect(store.tourStep).toBe(2);
    await press();
    await flushPromises();
    expect(app.state.value.train).toBe(true);
    expect(store.tourStep).toBe(3);
    await press();
    await flushPromises();
    expect(app.actions.closeTrain).toHaveBeenCalled();
    expect(store.tourStep).toBe(4);
    expect(wrapper.find('[data-testid="tour-next"]').text()).toContain("Done");
    await press();
    await flushPromises();
    expect(app.actions.goModels).toHaveBeenCalled();
    expect(store.tourStep).toBe(0);
    expect(store.card).toBe(false);
  });

  it("Enter inside the Train dialog is Next, and never reaches the dialog", async () => {
    const { app, store } = await start(3);
    app.state.value.train = true;
    await nextTick();
    const accept = vi.fn();
    dialog.addEventListener("keydown", (e) => e.key === "Enter" && accept());
    const field = dialog.querySelector(".field");
    field.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }),
    );
    await flushPromises();
    expect(accept).not.toHaveBeenCalled();
    expect(app.actions.closeTrain).toHaveBeenCalled();
    expect(store.tourStep).toBe(4);
  });

  it("closing the dialog yourself moves on; the tour's own close does not skip a step", async () => {
    const { app, store } = await start(2);
    app.state.value.train = true;
    await flushPromises();
    expect(store.tourStep).toBe(3);
    // Previous from 3 closes the dialog itself: back to 2, never via 4.
    const seen = [];
    watch(() => store.tourStep, (n) => seen.push(n), { flush: "sync" });
    await wrapper.findAll("button").find((b) => b.text().includes("Previous")).trigger("click");
    await flushPromises();
    expect(store.tourStep).toBe(2);
    expect(seen).toEqual([2]);
    expect(app.state.value.menu).toBe(true);
  });

  it("Esc ends the tour where you are and keeps the card", async () => {
    const { store } = await start(2);
    const inner = vi.fn();
    dialog.addEventListener("keydown", inner);
    dialog.querySelector(".field").dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }),
    );
    await flushPromises();
    expect(store.tourStep).toBe(0);
    expect(store.card).toBe(true);
    expect(inner).not.toHaveBeenCalled();
  });
});
