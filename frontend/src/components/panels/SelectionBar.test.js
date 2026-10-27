// The grid's selection pill after #2160: the verbs that left the toolbar live
// here. What is pinned is the order the design draws, that Export and Train
// LoRA act from the pill, and that S opens Add to set.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    onSessionReset: () => () => {},
    sessionContext: { value: null },
    apiClient: {
      get: vi.fn().mockResolvedValue({ data: {} }),
      post: vi.fn().mockResolvedValue({ data: {} }),
    },
    isReadOnly: ref(false),
    setRequestClientId: vi.fn(),
    API_BASE_URL: "http://backend.test/api/v1",
    newOperationBatchId: () => "cli-test",
  };
});

import SelectionBar from "./SelectionBar.vue";
import { isReadOnly as readOnlyRef } from "../../utils/apiClient";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useTrainingStore } from "../../stores/useTrainingStore";

// Vuetify is not installed here. An unresolved v-menu drops its named slots,
// so every activator would vanish and the assertions pass vacuously; the stub
// renders the activator, and the content once open.
const VMenuStub = {
  name: "VMenu",
  props: ["modelValue"],
  emits: ["update:modelValue"],
  template:
    '<div class="v-menu-stub"><slot name="activator" :props="{ onClick: () => $emit(\'update:modelValue\', !modelValue) }" />' +
    '<slot v-if="modelValue" /></div>',
};

const TooltipStub = {
  name: "Tooltip",
  props: ["text", "activator"],
  template:
    '<span class="tip" :data-text="text"><slot name="activator" :props="{}" /></span>',
};

// The set picker reads the entity lists on mount; what matters here is that
// its trigger is the one S presses, so the stub counts presses on it.
const atePresses = vi.fn();
const AteStub = {
  name: "AddToEntityControl",
  props: ["type", "label", "keyHint", "disabled", "subjectIds"],
  setup() {
    return { press: () => atePresses() };
  },
  template:
    '<div class="ate"><button class="ate-btn" :disabled="disabled" @click="press">{{ label }}</button></div>',
};

const TbExportPanelStub = {
  name: "TbExportPanel",
  emits: ["confirm-export", "confirm-export-folder"],
  template:
    '<div class="tb-export-panel"><button class="confirm" @click="$emit(\'confirm-export\')" /></div>',
};

function mountBar(props = {}) {
  return mount(SelectionBar, {
    attachTo: document.body,
    global: {
      stubs: {
        "v-icon": true,
        "v-menu": VMenuStub,
        Tooltip: TooltipStub,
        AddToEntityControl: AteStub,
        TbExportPanel: TbExportPanelStub,
        SelectionMenu: true,
        TbTagPanel: true,
        PluginParametersUI: true,
      },
    },
    props: {
      selectedCount: 3,
      selectedImageIds: [1, 2, 3],
      scrapheapPicturesId: "SCRAPHEAP",
      selectedCharacter: "ALL",
      ...props,
    },
  });
}

const verb = (wrapper, name) => wrapper.find(`[data-verb="${name}"]`);

function pressS() {
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "s" }));
}

let wrapper;
beforeEach(() => {
  setActivePinia(createPinia());
  readOnlyRef.value = false;
  atePresses.mockClear();
});
afterEach(() => wrapper?.unmount());

describe("SelectionBar - the verbs on the selection (#2160)", () => {
  it("draws count, verbs, ⋯, then Scrapheap and Clear, in that order", () => {
    wrapper = mountBar();
    const run = wrapper.find(".selection-ctx-bar").element;
    const order = [
      run.querySelector(".selection-count"),
      run.querySelector('[data-verb="favourite"]'),
      run.querySelector('[data-verb="tag"]'),
      run.querySelector(".ate-btn"),
      run.querySelector('[data-verb="export"]'),
      run.querySelector('[data-verb="train"]'),
      run.querySelector('[data-verb="more"]'),
      run.querySelector('[data-verb="scrapheap"]'),
      run.querySelector('[data-verb="clear"]'),
    ];
    expect(order.every(Boolean)).toBe(true);
    for (let i = 1; i < order.length; i++) {
      expect(
        order[i - 1].compareDocumentPosition(order[i]) &
          Node.DOCUMENT_POSITION_FOLLOWING,
        `position ${i}`,
      ).toBeTruthy();
    }
    expect(wrapper.find(".selection-count").text()).toBe("3 selected");
    expect(wrapper.find(".ate-btn").text()).toBe("Add to set");
  });

  it("names the export by the selection and forwards its confirm", async () => {
    wrapper = mountBar();
    expect(verb(wrapper, "export").attributes("aria-label")).toBe(
      "Export 3 pictures",
    );
    await verb(wrapper, "export").trigger("click");
    await wrapper.find(".tb-export-panel .confirm").trigger("click");
    expect(wrapper.emitted("confirm-export-zip")).toHaveLength(1);

    await wrapper.setProps({ selectedCount: 1, selectedImageIds: [1] });
    expect(verb(wrapper, "export").attributes("aria-label")).toBe(
      "Export 1 picture",
    );
  });

  it("trains on the set in view, and is dimmed with a reason elsewhere", async () => {
    wrapper = mountBar();
    expect(verb(wrapper, "train").attributes("disabled")).toBeDefined();

    const selection = useSelectionStore();
    selection.selectedSet = 7;
    selection.lastSelectedSetLabel = "Pub nights";
    const training = useTrainingStore();
    const open = vi.spyOn(training, "openForScope").mockResolvedValue();
    await wrapper.vm.$nextTick();

    expect(verb(wrapper, "train").attributes("disabled")).toBeUndefined();
    await verb(wrapper, "train").trigger("click");
    expect(open).toHaveBeenCalledWith({
      type: "set",
      id: 7,
      name: "Pub nights",
    });
  });

  it("opens Add to set on S", () => {
    wrapper = mountBar();
    pressS();
    expect(atePresses).toHaveBeenCalledTimes(1);
  });

  it("leaves S alone while typing, read-only, or with grouping locked", async () => {
    wrapper = mountBar();
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "s", bubbles: true }));
    input.remove();
    expect(atePresses).not.toHaveBeenCalled();

    await wrapper.setProps({ groupingLockReason: "Part of a stack" });
    pressS();
    expect(atePresses).not.toHaveBeenCalled();

    wrapper.unmount();
    readOnlyRef.value = true;
    wrapper = mountBar();
    pressS();
    expect(atePresses).not.toHaveBeenCalled();
    expect(wrapper.find(".ate-btn").exists()).toBe(false);
    expect(verb(wrapper, "train").exists()).toBe(false);
  });

  it("tells the grid while Export or Train LoRA is open, so Esc closes only it", async () => {
    wrapper = mountBar();
    await verb(wrapper, "export").trigger("click");
    expect(wrapper.emitted("selection-menu-open")?.at(-1)).toEqual([true]);
    await verb(wrapper, "export").trigger("click");
    expect(wrapper.emitted("selection-menu-open")?.at(-1)).toEqual([false]);

    useTrainingStore().trainDialogOpen = true;
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted("selection-menu-open")?.at(-1)).toEqual([true]);
  });
});
