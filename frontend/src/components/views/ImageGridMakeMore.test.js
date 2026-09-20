// Which popup "Make more like these…" opens, and what it hands over (#1407).
//
// This is the most conditional new code in the feature and the branch is not a
// client-side guess: a client cannot group pictures by card, because a card IS
// the grouping. So the selection is pre-flighted first and the SERVER's answer
// decides — one group with a card is the single Run popup over that group's
// pictures, anything else is the Make more popup.
//
// Three things here can invert without anything else going red: the branch
// itself, the hand-over of the answer already paid for, and the guard that
// keeps one gesture from starting two ComfyUI `/object_info` reads.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";
import { useSelectionStore } from "../../stores/useSelectionStore.js";
import { useProjectStore } from "../../stores/useProjectStore.js";
import { useSortStore } from "../../stores/useSortStore.js";
import { useRunDialogStore } from "../../stores/useRunDialogStore.js";

const preflightWorkflowRun = vi.fn();
vi.mock("../../api/workflows", () => ({
  preflightWorkflowRun: (...args) => preflightWorkflowRun(...args),
}));

vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef, computed: makeComputed } = await import("vue");
  return {
    onSessionReset: () => () => {},
    apiClient: {
      get: vi.fn().mockResolvedValue({ data: [] }),
      post: vi.fn().mockResolvedValue({ data: {} }),
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
    replace: vi.fn().mockResolvedValue(undefined),
    resolve: () => ({ name: "all-pictures", matched: [{}] }),
    currentRoute: ref({ query: {} }),
  }),
}));

import ImageGrid from "./ImageGrid.vue";

const CARD = "a".repeat(64);
const OTHER = "b".repeat(64);

function mountGrid() {
  const selectionStore = useSelectionStore();
  const projectStore = useProjectStore();
  const sortStore = useSortStore();
  selectionStore.selectedCharacter = "ALL";
  selectionStore.selectedSet = null;
  selectionStore.selectedSetIds = [];
  projectStore.projectViewMode = "global";
  projectStore.selectedProjectId = null;
  sortStore.selectedSort = "DATE";
  sortStore.selectedDescending = true;

  return mount(ImageGrid, {
    shallow: true,
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
        // See ImageGridUseAsInput.test.js: a compiler option cannot take effect
        // on an already-compiled template, so every Vuetify tag warns once per
        // mount and the rpc that carries it can red a fast file's whole run.
        warnHandler(message, _instance, trace) {
          if (message.startsWith("Failed to resolve component")) return;
          console.warn(`[Vue warn]: ${message}${trace}`);
        },
      },
    },
    props: { backendUrl: "/api/v1" },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  vi.spyOn(console, "debug").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe('"Make more like these…" picks its popup from the server\'s answer', () => {
  it("opens the single Run popup when one card claims the whole selection", async () => {
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 1,
      groups: [{ workflow_key: CARD, picture_ids: [11, 12, 13], reasons: [] }],
    });
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();
    wrapper.vm.selectedImageIds = [11, 12, 13];

    await wrapper.vm.makeMoreLikeSelection();

    expect(runDialog.makeMore).toBe(null);
    expect(runDialog.source).toMatchObject({
      kind: "selection",
      workflowKey: CARD,
      pictureIds: [11, 12, 13],
    });
  });

  it("opens the Make more popup when the selection spans several cards", async () => {
    const answer = {
      ok: true,
      runs: 2,
      groups: [
        { workflow_key: CARD, picture_ids: [11], reasons: [] },
        { workflow_key: OTHER, picture_ids: [12], reasons: [] },
      ],
    };
    preflightWorkflowRun.mockResolvedValue(answer);
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();
    wrapper.vm.selectedImageIds = [11, 12];

    await wrapper.vm.makeMoreLikeSelection();

    expect(runDialog.source).toBe(null);
    // The answer goes with it: the popup would otherwise ask the identical
    // question again, and each ask costs the server an /object_info read.
    expect(runDialog.makeMore).toMatchObject({ pictureIds: [11, 12] });
    // `toEqual`, not `toBe`: pinia stores it behind a reactive proxy, so the
    // identity differs while the answer is the same one.
    expect(runDialog.makeMore.preflight).toEqual(answer);
  });

  it("opens the Make more popup, with no answer, when the look-ahead fails", async () => {
    // The popup asks for itself when it is handed nothing, so a failed
    // look-ahead must not swallow the gesture.
    preflightWorkflowRun.mockRejectedValue(new Error("comfyui down"));
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();
    wrapper.vm.selectedImageIds = [11, 12];

    await wrapper.vm.makeMoreLikeSelection();

    expect(runDialog.makeMore).toMatchObject({ pictureIds: [11, 12] });
    expect(runDialog.makeMore.preflight).toBeUndefined();
  });

  it("treats a group with no card as several, not as the single popup", async () => {
    // One group whose `workflow_key` is empty is a picture on no card at all
    // (A1111, or nothing runnable). The single Run popup has no card to open
    // on, so this belongs in the popup that can show a refusal per group.
    preflightWorkflowRun.mockResolvedValue({
      ok: false,
      runs: 0,
      groups: [
        { workflow_key: "", picture_ids: [11], reasons: [{ code: "a1111" }] },
      ],
    });
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();
    wrapper.vm.selectedImageIds = [11];

    await wrapper.vm.makeMoreLikeSelection();

    expect(runDialog.source).toBe(null);
    expect(runDialog.makeMore).toMatchObject({ pictureIds: [11] });
  });

  it("asks once per gesture, however many times the entry is fired", async () => {
    // The menu closes on the click, so a double press - or the entry fired
    // from both menus - would otherwise start two /object_info reads and let
    // whichever answered last decide which popup opened.
    let release;
    preflightWorkflowRun.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve({ ok: true, runs: 1, groups: [] });
      }),
    );
    const wrapper = mountGrid();
    wrapper.vm.selectedImageIds = [11];

    const first = wrapper.vm.makeMoreLikeSelection();
    const second = wrapper.vm.makeMoreLikeSelection();
    release();
    await Promise.all([first, second]);

    expect(preflightWorkflowRun).toHaveBeenCalledTimes(1);
  });

  it("does nothing at all on an empty selection", async () => {
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();
    wrapper.vm.selectedImageIds = [];

    await wrapper.vm.makeMoreLikeSelection();

    expect(preflightWorkflowRun).not.toHaveBeenCalled();
    expect(runDialog.source).toBe(null);
    expect(runDialog.makeMore).toBe(null);
  });
});

describe('the Recipe tab\'s "Run…"', () => {
  it("opens the popup on that one picture's own recipe", async () => {
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();

    wrapper.vm.openRunForPicture(42);

    expect(runDialog.source).toMatchObject({
      kind: "picture",
      pictureIds: [42],
    });
    // Not the workflow picker: this one already knows which recipe it is.
    expect(runDialog.source.pickWorkflow).toBeUndefined();
  });

  it("falls back to the right-clicked picture when given no id", async () => {
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();
    wrapper.vm.contextMenuImage = { id: 77 };

    wrapper.vm.openRunForPicture();

    expect(runDialog.source.pictureIds).toEqual([77]);
  });

  it("does nothing with neither an id nor a right-clicked picture", async () => {
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();
    runDialog.openRun({ kind: "picture", pictureIds: [99] });

    wrapper.vm.openRunForPicture(null);

    // Untouched - asserting against the store's own `null` default would pass
    // with the whole function body deleted.
    expect(runDialog.source.pictureIds).toEqual([99]);
  });
});
