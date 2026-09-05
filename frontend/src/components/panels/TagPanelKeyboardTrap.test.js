// Tab must never trap focus in the tag panel's suggestion field (issue #755,
// WCAG 2.1.2 Level A).
//
// The trap was: Tab committed the highlighted suggestion to every selected
// picture, the `startsWith` filter still matched the now-complete tag so the
// list stayed open, and the handler called `preventDefault()` unconditionally
// while it was open. On a failing write the field never cleared either, so every
// Tab re-fired the failing request and focus could never leave by keyboard.
//
// The contract these tests pin: Tab fills, Enter commits, Escape dismisses the
// list before it closes the panel, and a dismissed list lets Tab through.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";

const tagsApi = vi.hoisted(() => ({
  listTags: vi.fn(async () => []),
  addPictureTag: vi.fn(async () => ({})),
  removePictureTag: vi.fn(async () => ({})),
  bulkFetchTags: vi.fn(async () => []),
  removeTagEverywhere: vi.fn(async () => ({})),
  listTagPredictions: vi.fn(async () => []),
  confirmTagPrediction: vi.fn(async () => ({})),
  rejectTagPrediction: vi.fn(async () => ({})),
}));

vi.mock("../../api/tags", () => tagsApi);
vi.mock("../../api/pictures", () => ({
  resetPicturesTags: vi.fn(async () => ({})),
}));
vi.mock("../../api/taggers", () => ({
  listTaggers: vi.fn(async () => ({ plugins: [] })),
}));
vi.mock("../../api/config", () => ({ getUserConfig: vi.fn(async () => ({})) }));
vi.mock("../../api/users", () => ({ getPenalisedTags: vi.fn(async () => []) }));

import TbTagPanel from "./TbTagPanel.vue";

const STUBS = { "v-icon": true, "v-btn": true, "v-progress-circular": true };

beforeEach(() => {
  Object.values(tagsApi).forEach((mock) => mock.mockReset());
  tagsApi.listTags.mockResolvedValue([
    { tag: "sunset", count: 9 },
    { tag: "sunset-beach", count: 3 },
  ]);
  tagsApi.bulkFetchTags.mockResolvedValue([]);
  tagsApi.listTagPredictions.mockResolvedValue({
    tag_predictions: [],
    meta: { acceptance_threshold: 0.95, label_thresholds: {} },
  });
  tagsApi.addPictureTag.mockResolvedValue({});
});

async function mountPanel() {
  const wrapper = mount(TbTagPanel, {
    props: {
      backendUrl: "http://backend",
      open: true,
      selectedCount: 2,
      selectedImageIds: [1, 2],
      allGridImages: [{ id: 1 }, { id: 2 }],
    },
    attachTo: document.body,
    global: { stubs: STUBS },
  });
  await flushPromises();
  await flushPromises();
  return wrapper;
}

function input(wrapper) {
  return wrapper.find("input.tag-menu-input");
}

/** Dispatch a real key event so `defaultPrevented` reflects the handlers. */
async function press(wrapper, key) {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
  });
  input(wrapper).element.dispatchEvent(event);
  await flushPromises();
  return event;
}

async function type(wrapper, value) {
  await input(wrapper).setValue(value);
  await flushPromises();
}

const suggestions = () =>
  document.querySelectorAll("#tb-tag-suggestions [role='option']");

describe("the tag suggestion combobox", () => {
  it("completes the field on Tab without writing to any picture", async () => {
    const wrapper = await mountPanel();
    await type(wrapper, "suns");
    expect(suggestions()).toHaveLength(2);

    const first = await press(wrapper, "Tab");

    expect(first.defaultPrevented).toBe(true);
    expect(input(wrapper).element.value).toBe("sunset");
    expect(tagsApi.addPictureTag).not.toHaveBeenCalled();
    // The completed tag still matches the startsWith filter, but the list is
    // dismissed, so it must not be rendered.
    expect(suggestions()).toHaveLength(0);
    wrapper.unmount();
  });

  it("lets the second Tab move focus, even after the write failed", async () => {
    tagsApi.addPictureTag.mockRejectedValue(new Error("write failed"));
    const wrapper = await mountPanel();
    await type(wrapper, "suns");

    await press(wrapper, "Tab"); // completes to "sunset"
    await press(wrapper, "Enter"); // commits, and the request fails
    await flushPromises();

    expect(tagsApi.addPictureTag).toHaveBeenCalled();
    expect(wrapper.find(".plugin-menu-error").exists()).toBe(true);
    // The failure leaves the typed tag in place on purpose, so the list could
    // reopen; this is exactly the state that used to trap focus.
    const escape = await press(wrapper, "Tab");
    expect(escape.defaultPrevented).toBe(false);
    wrapper.unmount();
  });

  it("commits on Enter", async () => {
    const wrapper = await mountPanel();
    await type(wrapper, "suns");
    await press(wrapper, "ArrowDown");
    await press(wrapper, "Enter");
    await flushPromises();

    expect(tagsApi.addPictureTag.mock.calls.map((args) => args[1])).toEqual([
      "sunset",
      "sunset",
    ]);
    wrapper.unmount();
  });

  it("dismisses the list on the first Escape and closes the panel on the second", async () => {
    const wrapper = await mountPanel();
    await type(wrapper, "suns");
    expect(suggestions()).toHaveLength(2);

    await press(wrapper, "Escape");
    expect(suggestions()).toHaveLength(0);
    expect(wrapper.emitted("close")).toBeUndefined();

    await press(wrapper, "Escape");
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });

  it("reopens a dismissed list on ArrowDown", async () => {
    const wrapper = await mountPanel();
    await type(wrapper, "suns");
    await press(wrapper, "Escape");
    expect(suggestions()).toHaveLength(0);

    await press(wrapper, "ArrowDown");
    expect(suggestions()).toHaveLength(2);
    wrapper.unmount();
  });

  it("exposes the combobox state assistive tech needs", async () => {
    const wrapper = await mountPanel();
    const el = input(wrapper);
    expect(el.attributes("role")).toBe("combobox");
    expect(el.attributes("aria-autocomplete")).toBe("list");
    expect(el.attributes("aria-expanded")).toBe("false");
    expect(el.attributes("aria-activedescendant")).toBeUndefined();

    await type(wrapper, "suns");
    expect(el.attributes("aria-expanded")).toBe("true");
    expect(el.attributes("aria-controls")).toBe("tb-tag-suggestions");

    await press(wrapper, "ArrowDown");
    expect(el.attributes("aria-activedescendant")).toBe("tb-tag-suggestion-0");
    expect(suggestions()[0].getAttribute("aria-selected")).toBe("true");
    wrapper.unmount();
  });

  // #782 item 3: the popup only renders once `tagInputRect` lands, a tick after
  // `suggestionsOpen` flips, so ARIA driven by `suggestionsOpen` alone claimed
  // an expanded listbox, and an activedescendant id, that was not in the DOM.
  it("never claims to be expanded before the listbox is in the DOM", async () => {
    const wrapper = await mountPanel();
    input(wrapper).element.value = "suns";
    input(wrapper).element.dispatchEvent(new Event("input", { bubbles: true }));

    for (let tick = 0; tick < 4; tick += 1) {
      const el = input(wrapper);
      if (el.attributes("aria-expanded") === "true") {
        expect(suggestions().length).toBeGreaterThan(0);
      }
      const active = el.attributes("aria-activedescendant");
      if (active) expect(document.getElementById(active)).not.toBeNull();
      await nextTick();
    }

    await flushPromises();
    expect(input(wrapper).attributes("aria-expanded")).toBe("true");
    expect(suggestions()).toHaveLength(2);
    wrapper.unmount();
  });

  // #782 item 2: mousedown does not fire on touch, and committing on press-down
  // means a press-and-drag-away still writes the tag.
  it("commits a clicked suggestion, not a pressed one, without blurring the field", async () => {
    const wrapper = await mountPanel();
    input(wrapper).element.focus();
    await type(wrapper, "suns");
    const option = suggestions()[0];

    const down = new MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
    });
    option.dispatchEvent(down);
    await flushPromises();
    // Press-down only keeps the input from blurring; nothing is written.
    expect(down.defaultPrevented).toBe(true);
    expect(tagsApi.addPictureTag).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(input(wrapper).element);

    option.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();
    expect(tagsApi.addPictureTag.mock.calls.map((args) => args[1])).toEqual([
      "sunset",
      "sunset",
    ]);
    expect(document.activeElement).toBe(input(wrapper).element);
    wrapper.unmount();
  });
});
