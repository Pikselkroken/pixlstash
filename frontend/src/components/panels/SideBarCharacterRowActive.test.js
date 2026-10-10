// The character row's active highlight, on the one path that reaches its
// single-selection fallback.
//
// The row's active class is
//
//   selectedCharacterIdSet.size > 0
//     ? selectedCharacterIdSet.has(char.id)
//     : selectionStore.selectedCharacter === char.id
//
// and the `else` half used to read a BARE `selectedCharacter`, an identifier
// that is not declared in the component's `<script setup>` at all. It resolved
// to `undefined`, so the comparison was always false and the branch could never
// light a row.
//
// That branch is not dead code. Every ordinary selection path writes the scalar
// and the id list together, so the set is non-empty and the ternary takes its
// first arm:
//
//   * `SideBar.selectCharacter` emits `ids: [numId]` for a real person, and
//     `useAppNavigation` writes both (`useAppNavigation.js:112-113`);
//   * the route parser derives `ids` from `params.id` when `?ids=` is absent
//     (`useViewStore.parseIds`), so `/character/7` yields `[7]` as well.
//
// The exception is `App.vue:391`: a share token scoped to a CHARACTER sets
// `selectionStore.selectedCharacter = ctx.resource_id` and never touches
// `selectedCharacterIds`, which stays at its `[]` default. That session renders
// the People section (it is gated `scopedResourceType !== 'picture_set'`), so
// the one person the token exists for was the one row that could never show as
// selected. That is the state this file pins.

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

import { isReadOnly, sessionContext } from "../../utils/apiClient";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import SideBar from "./SideBar.vue";

/**
 * The element's own tooltip text: its direct `Tooltip` child, stubbed by the
 * shallow mount. `undefined` when it has none or the text is empty, because an
 * empty text is a disabled tip.
 */
function tipText(el) {
  const tip = el
    .findAll("tooltip-stub")
    .find((t) => t.element.parentElement === el.element);
  return tip?.attributes("text") || undefined;
}

const ADA = { id: 7, name: "Ada", image_count: 3, project_image_count: 3 };
const GRACE = { id: 9, name: "Grace", image_count: 5, project_image_count: 5 };
const CHARACTERS = [ADA, GRACE];

function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/characters")) return { data: CHARACTERS };
  if (u.includes("/projects")) return { data: [] };
  if (u.includes("/picture_sets")) return { data: [] };
  if (u.includes("/summary")) return { data: { image_count: 3 } };
  return { data: [] };
}

async function mountSidebar({ docked = false, teleport = docked } = {}) {
  if (docked) useSidebarStore().sidebarDocked = true;
  const options = {
    shallow: true,
    props: { backendUrl: "/api/v1" },
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
      ...(teleport ? { stubs: { teleport: false } } : {}),
    },
  };
  if (docked) options.attachTo = document.body;
  const wrapper = mount(SideBar, options);
  wrapper.vm.refreshSidebar();
  for (let i = 0; i < 5; i += 1) await flushPromises();
  return wrapper;
}

/**
 * Is the People-section row for `name` rendered with the active class?
 *
 * Located by the row's own tooltip, which the template builds from the person's
 * name, so the assertion is tied to the row a user would actually click rather
 * than to a DOM position. Returns `null` when no such row was rendered, which
 * would otherwise read as a passing "not active".
 */
function characterRowIsActive(wrapper, name) {
  const row = wrapper
    .findAll(".sidebar-list-item")
    .find((el) => String(tipText(el) ?? "").startsWith(`${name} (`));
  if (!row) return null;
  return row.classes().includes("active");
}

function characterRow(wrapper, name) {
  return wrapper
    .findAll(".sidebar-list-item")
    .find((el) => String(tipText(el) ?? "").startsWith(`${name} (`));
}

let consoleWarn;

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  sessionContext.value = null;
  apiGet.mockReset().mockImplementation((url) => Promise.resolve(respond(url)));
  consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("the character row's single-selection fallback", () => {
  it("exposes selection state and keyboard activation semantics", async () => {
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = ADA.id;
    selection.selectedCharacterIds = [];
    await wrapper.vm.$nextTick();

    const adaRow = characterRow(wrapper, ADA.name);
    const graceRow = characterRow(wrapper, GRACE.name);
    expect(adaRow.attributes()).toMatchObject({
      role: "button",
      tabindex: "0",
      "aria-pressed": "true",
    });
    expect(graceRow.attributes("aria-pressed")).toBe("false");

    await graceRow.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("select-character")?.at(-1)?.[0]).toMatchObject({
      id: GRACE.id,
      ids: [GRACE.id],
    });

    wrapper.unmount();
  });

  it("lights the row when only the scalar selection is set", async () => {
    // Exactly the `App.vue:391` share-token state: the scalar names a real
    // person, the id list was never written. Before the fix the bare
    // `selectedCharacter` was `undefined` here and no row could go active.
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = ADA.id;
    selection.selectedCharacterIds = [];
    await wrapper.vm.$nextTick();

    expect(characterRowIsActive(wrapper, ADA.name)).toBe(true);
    // And only that row.
    expect(characterRowIsActive(wrapper, GRACE.name)).toBe(false);

    wrapper.unmount();
  });

  it("does not light a row when the scalar names nobody", async () => {
    // The other direction: "All Pictures" is not a person, so no person row may
    // claim the highlight. A fallback that matched loosely would light one.
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = "ALL";
    selection.selectedCharacterIds = [];
    await wrapper.vm.$nextTick();

    expect(characterRowIsActive(wrapper, ADA.name)).toBe(false);
    expect(characterRowIsActive(wrapper, GRACE.name)).toBe(false);

    wrapper.unmount();
  });

  it("still prefers the id set when the ordinary paths populate it", async () => {
    // The first arm of the ternary is what every normal selection uses; the fix
    // must not disturb it. Scalar and set deliberately disagree here so the
    // assertion can only pass if the SET won.
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = ADA.id;
    selection.selectedCharacterIds = [GRACE.id];
    await wrapper.vm.$nextTick();

    expect(characterRowIsActive(wrapper, GRACE.name)).toBe(true);
    expect(characterRowIsActive(wrapper, ADA.name)).toBe(false);

    wrapper.unmount();
  });

  it("lights every row of a genuine multi-selection", async () => {
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = ADA.id;
    selection.selectedCharacterIds = [ADA.id, GRACE.id];
    await wrapper.vm.$nextTick();

    expect(characterRowIsActive(wrapper, ADA.name)).toBe(true);
    expect(characterRowIsActive(wrapper, GRACE.name)).toBe(true);

    wrapper.unmount();
  });

  it("resolves the identifier, so no render warning is logged", async () => {
    // The cheap signal the bug produced: Vue warns once per render on a
    // template reference it cannot resolve.
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = ADA.id;
    selection.selectedCharacterIds = [];
    await wrapper.vm.$nextTick();

    const resolutionWarnings = consoleWarn.mock.calls.filter(([msg]) =>
      String(msg ?? "").includes("selectedCharacter"),
    );
    expect(resolutionWarnings).toEqual([]);

    wrapper.unmount();
  });
});

describe("the shelf's two routes are one destination", () => {
  // `SideBar` keeps its own `isModelsView`, separate from the one
  // `useAppNavigation` exports, and adding the runs tab broke this half
  // silently: on `/models/runs` the Models entry went dark AND the underlying
  // picture selection lit a row of its own, which is the two-active-
  // destinations defect the guard exists to prevent. Both now read
  // `MODEL_SHELF_ROUTES`, so the pair cannot drift apart again.
  it("treats /models/runs as the Models destination", async () => {
    nav.route.name = "models-runs";
    const wrapper = await mountSidebar();
    const models = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Models"));
    expect(models).toBeTruthy();
    expect(models.attributes("aria-current")).toBe("page");
    wrapper.unmount();
    nav.route.name = "all-pictures";
  });

  it("does not let a picture selection light a second destination there", async () => {
    nav.route.name = "models-runs";
    useSelectionStore().selectedCharacter = ADA.id;
    const wrapper = await mountSidebar();
    const active = wrapper.findAll(".sidebar-list-item.active");
    const labels = active.map((el) => el.text());
    expect(labels.filter((l) => l.includes("Ada"))).toHaveLength(0);
    wrapper.unmount();
    nav.route.name = "all-pictures";
  });
});

describe("the docked library menu", () => {
  it("uses menu semantics and supports arrow, submenu, and Escape focus", async () => {
    const wrapper = await mountSidebar({ docked: true });
    const trigger = wrapper.find(
      ".sidebar-collapsed-row--project .sidebar-collapsed-item",
    );

    expect(trigger.element.tagName).toBe("BUTTON");
    expect(trigger.attributes()).toMatchObject({
      "aria-haspopup": "menu",
      "aria-expanded": "false",
    });

    await trigger.trigger("keydown", { key: "ArrowDown" });
    await flushPromises();

    const menu = document.querySelector("#sidebar-project-menu");
    expect(menu.getAttribute("role")).toBe("menu");
    expect(menu.getAttribute("aria-label")).toBe("Library navigation");
    const rootItems = Array.from(
      menu.querySelectorAll(':scope > [role="menuitem"]'),
    );
    expect(rootItems.map((item) => item.tagName)).toEqual([
      "BUTTON",
      "BUTTON",
      "BUTTON",
    ]);
    expect(document.activeElement).toBe(rootItems[0]);

    rootItems[0].dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "ArrowDown",
        bubbles: true,
        cancelable: true,
      }),
    );
    expect(document.activeElement).toBe(rootItems[1]);

    rootItems[1].dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "ArrowRight",
        bubbles: true,
        cancelable: true,
      }),
    );
    await flushPromises();

    const submenu = document.querySelector("#sidebar-project-submenu");
    expect(submenu.getAttribute("role")).toBe("menu");
    expect(submenu.getAttribute("aria-label")).toBe("Projects");
    const submenuItem = submenu.querySelector('[role="menuitem"]');
    expect(submenuItem.tagName).toBe("BUTTON");
    expect(document.activeElement).toBe(submenuItem);

    submenuItem.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        bubbles: true,
        cancelable: true,
      }),
    );
    await flushPromises();
    expect(document.querySelector("#sidebar-project-submenu")).toBeNull();
    expect(document.activeElement).toBe(rootItems[1]);

    rootItems[1].dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        bubbles: true,
        cancelable: true,
      }),
    );
    await flushPromises();
    expect(document.querySelector("#sidebar-project-menu")).toBeNull();
    expect(document.activeElement).toBe(trigger.element);

    wrapper.unmount();
  });
});

describe("Create with LoRA… in a person's or a set's context menu", () => {
  // The row opened the Run popup on anybody, and the popup then said there
  // was no LoRA to run. It is now live only once a LoRA is known to be
  // attached to the person or set the menu was opened on.
  const PORTRAITS = { id: 4, name: "Portraits", picture_count: 2 };
  const LORA = { sha256: "a".repeat(64), name: "ada-v1" };
  const NO_LORA =
    "No LoRA is attached to Ada. Assign one from the Models shelf.";

  /** Right-click `row` and return the menu's Create with LoRA… button. */
  async function loraItem(row) {
    await row.trigger("contextmenu");
    await flushPromises();
    return loraButton();
  }

  /**
   * The row as it is now: re-queried, since a re-render may replace it. The
   * menu is teleported to <body>, so it is not under the wrapper.
   */
  function loraButton() {
    return Array.from(
      document.querySelectorAll(".sidebar-ctx-menu .ctx-item"),
    ).find((el) => /Create with LoRA|No LoRA attached/.test(el.textContent));
  }

  function itemTip(el) {
    return el.querySelector("tooltip-stub")?.getAttribute("text");
  }

  /** Serve `/adapters` from `answer(params)`; everything else as before. */
  function serveAdapters(answer) {
    apiGet.mockImplementation((url, config) => {
      const u = String(url ?? "");
      if (u.includes("/adapters")) {
        return Promise.resolve(answer(config?.params ?? {})).then((rows) => ({
          data: { adapters: rows },
        }));
      }
      if (u.includes("/picture_sets")) {
        return Promise.resolve({ data: [PORTRAITS] });
      }
      return Promise.resolve(respond(url));
    });
  }

  beforeEach(() => {
    useFilterStore().comfyuiConfigured = true;
  });

  it("is disabled, with the reason, for a person with no LoRA", async () => {
    serveAdapters(() => []);
    const wrapper = await mountSidebar({ teleport: true });
    const item = await loraItem(characterRow(wrapper, ADA.name));

    expect(item.disabled).toBe(true);
    // Said in the row itself, not only in a tooltip a keyboard cannot reach.
    expect(item.textContent).toContain("No LoRA attached");
    expect(itemTip(item)).toBe(NO_LORA);
    // Both attachable file kinds were asked about, for this person.
    const asked = apiGet.mock.calls
      .filter(([url]) => String(url).includes("/adapters"))
      .map(([, config]) => config.params);
    expect(asked).toEqual([
      { file_kind: "adapter", character_id: ADA.id },
      { file_kind: "unknown", character_id: ADA.id },
    ]);
    wrapper.unmount();
  });

  it("is live for a person with a LoRA attached", async () => {
    serveAdapters((params) => (params.file_kind === "adapter" ? [LORA] : []));
    const wrapper = await mountSidebar({ teleport: true });
    const item = await loraItem(characterRow(wrapper, ADA.name));

    expect(item.disabled).toBe(false);
    expect(item.textContent).toContain("Create with LoRA…");
    expect(itemTip(item)).toBe("Run a workflow with Ada's LoRA");
    wrapper.unmount();
  });

  it("counts a LoRA the shelf has not classified", async () => {
    serveAdapters((params) => (params.file_kind === "unknown" ? [LORA] : []));
    const wrapper = await mountSidebar({ teleport: true });
    const item = await loraItem(characterRow(wrapper, ADA.name));

    expect(item.disabled).toBe(false);
    wrapper.unmount();
  });

  it("stays disabled until the answer lands, and when it cannot be read", async () => {
    let fail;
    serveAdapters(() => new Promise((_, reject) => (fail = reject)));
    const wrapper = await mountSidebar({ teleport: true });
    const item = await loraItem(characterRow(wrapper, ADA.name));

    expect(item.disabled).toBe(true);
    expect(itemTip(item)).toBe("Checking for an attached LoRA…");

    fail(new Error("shelf unavailable"));
    await flushPromises();
    expect(loraButton().disabled).toBe(true);
    expect(itemTip(loraButton())).toBe("Could not read the Models shelf.");
    wrapper.unmount();
  });

  it("does not let a late answer for one person enable another's row", async () => {
    // Ada's read is still out when the menu is reopened on Grace, who has no
    // LoRA. Ada's answer must not make Grace's row live.
    const pending = [];
    serveAdapters((params) =>
      params.character_id === ADA.id
        ? new Promise((resolve) => pending.push(resolve))
        : [],
    );
    const wrapper = await mountSidebar({ teleport: true });
    await loraItem(characterRow(wrapper, ADA.name));
    await loraItem(characterRow(wrapper, GRACE.name));
    pending.forEach((resolve) => resolve([LORA]));
    await flushPromises();

    expect(loraButton().disabled).toBe(true);
    expect(itemTip(loraButton())).toBe(
      "No LoRA is attached to Grace. Assign one from the Models shelf.",
    );
    wrapper.unmount();
  });

  it("makes no read while the row is not offered, then asks once it is", async () => {
    // ComfyUI's configuration lands after start-up. A menu opened before it
    // has made no read, so the row that then appears must not be live on the
    // last menu's answer, and must not sit at "Checking…" for good either.
    let answer = () => [LORA];
    serveAdapters((params) => answer(params));
    const wrapper = await mountSidebar({ teleport: true });
    expect((await loraItem(characterRow(wrapper, GRACE.name))).disabled).toBe(
      false,
    );
    apiGet.mockClear();

    useFilterStore().comfyuiConfigured = false;
    expect(await loraItem(characterRow(wrapper, ADA.name))).toBeUndefined();
    expect(
      apiGet.mock.calls.filter(([url]) => String(url).includes("/adapters")),
    ).toEqual([]);

    const pending = [];
    answer = () => new Promise((resolve) => pending.push(resolve));
    useFilterStore().comfyuiConfigured = true;
    await flushPromises();
    expect(loraButton().disabled).toBe(true);
    expect(itemTip(loraButton())).toBe("Checking for an attached LoRA…");

    // The read it was owed, for the person the menu is open on.
    expect(
      apiGet.mock.calls
        .filter(([url]) => String(url).includes("/adapters"))
        .map(([, config]) => config.params.character_id),
    ).toEqual([ADA.id, ADA.id]);
    pending.forEach((resolve) => resolve([LORA]));
    await flushPromises();
    expect(loraButton().disabled).toBe(false);
    wrapper.unmount();
  });

  it("follows the same rule on a picture set", async () => {
    let attached = [];
    serveAdapters((params) =>
      params.set_id === PORTRAITS.id && params.file_kind === "adapter"
        ? attached
        : [],
    );
    const wrapper = await mountSidebar({ teleport: true });
    // `characterRow` finds a row by its tooltip, which a set's row shares.
    const row = characterRow(wrapper, PORTRAITS.name);

    const item = await loraItem(row);
    expect(item.disabled).toBe(true);
    expect(itemTip(item)).toBe(
      "No LoRA is attached to Portraits. Assign one from the Models shelf.",
    );

    attached = [LORA];
    expect((await loraItem(row)).disabled).toBe(false);
    wrapper.unmount();
  });
});
