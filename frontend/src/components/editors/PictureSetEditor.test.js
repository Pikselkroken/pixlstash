// PictureSetEditor locking behaviour (picture-set locking, plan §3.2):
//   - a locked set renders its name/description/project/appearance fields
//     disabled (only the Locked checkbox stays active);
//   - saving a locked set sends ONLY { id, locked } (never the other fields,
//     which would 423 server-side), so unticking Locked + Save is the unlock
//     path;
//   - saving an UNLOCKED set sends the full body including locked.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { h } from "vue";

vi.mock("../../utils/apiClient", () => ({
  apiClient: {
    post: vi.fn().mockResolvedValue({ data: {} }),
    patch: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

// The editor imports VIcon directly from vuetify, which pulls in CSS vitest
// can't load; replace it with a trivial stub.
vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
}));

import { apiClient } from "../../utils/apiClient";
import PictureSetEditor from "./PictureSetEditor.vue";

// Lightweight stubs for the App* widgets so we can read their `disabled` prop
// and drive the Save button without pulling in the whole design-system tree.
const AppDialog = {
  name: "AppDialog",
  props: ["open", "title", "width"],
  template: `<div><slot /><slot name="footer" /></div>`,
};
const fieldStub = (name) => ({
  name,
  props: ["modelValue", "disabled", "label", "options", "rows"],
  template: `<div class="${name}-stub" :data-disabled="disabled ? 'true' : 'false'"></div>`,
});
const AppButton = {
  name: "AppButton",
  props: ["disabled", "variant"],
  emits: ["click"],
  template: `<button class="app-button-stub" :disabled="disabled" @click="$emit('click')"><slot /></button>`,
};
const VIcon = {
  name: "v-icon",
  setup: (_p, { slots }) => () => h("i", slots.default?.()),
};

const globalOpts = {
  stubs: {
    AppDialog,
    AppInput: fieldStub("AppInput"),
    AppTextarea: fieldStub("AppTextarea"),
    AppSelect: fieldStub("AppSelect"),
    AppButton,
    FieldLabel: { name: "FieldLabel", template: "<div><slot /></div>" },
    "v-icon": VIcon,
  },
};

function mountEditor(set) {
  return mount(PictureSetEditor, {
    props: { open: true, set, backendUrl: "http://x", projects: [] },
    global: globalOpts,
  });
}

const lockedSet = {
  id: 7,
  name: "Eval slice",
  description: "frozen",
  project_id: null,
  set_icon: "mdi-layers-triple",
  set_color: "#b0732b",
  locked: true,
};

beforeEach(() => {
  apiClient.post.mockClear();
  apiClient.patch.mockClear();
});

describe("PictureSetEditor — locked set", () => {
  it("disables name/description/project fields", () => {
    const wrapper = mountEditor(lockedSet);
    expect(wrapper.find(".AppInput-stub").attributes("data-disabled")).toBe(
      "true",
    );
    expect(wrapper.find(".AppTextarea-stub").attributes("data-disabled")).toBe(
      "true",
    );
    expect(wrapper.find(".AppSelect-stub").attributes("data-disabled")).toBe(
      "true",
    );
  });

  it("disables the appearance (icon/color) buttons", () => {
    const wrapper = mountEditor(lockedSet);
    const disabledButtons = wrapper
      .findAll("button.icon-btn, button.color-swatch")
      .filter((b) => b.attributes("disabled") !== undefined);
    // Every icon/colour button is disabled while locked.
    expect(disabledButtons.length).toBeGreaterThan(0);
    expect(
      wrapper
        .findAll("button.icon-btn, button.color-swatch")
        .every((b) => b.attributes("disabled") !== undefined),
    ).toBe(true);
  });

  it("keeps the Locked checkbox active and saves only { id, locked:false } on unlock", async () => {
    const wrapper = mountEditor(lockedSet);
    const checkbox = wrapper.find("input[type=checkbox]");
    expect(checkbox.exists()).toBe(true);
    expect(checkbox.element.disabled).toBe(false);

    // Untick Locked → save should PATCH only the unlock.
    await checkbox.setValue(false);
    const saveBtn = wrapper
      .findAll("button.app-button-stub")
      .find((b) => b.text().includes("Save"));
    await saveBtn.trigger("click");

    expect(apiClient.patch).toHaveBeenCalledTimes(1);
    const [url, body] = apiClient.patch.mock.calls[0];
    expect(url).toContain("/picture_sets/7");
    expect(body).toEqual({ id: 7, locked: false });
    // Crucially, the disabled fields are NOT sent (they would 423).
    expect(body).not.toHaveProperty("name");
    expect(body).not.toHaveProperty("set_color");
  });
});

describe("PictureSetEditor — unlocked set", () => {
  it("sends the full body including locked on save", async () => {
    const wrapper = mountEditor({ ...lockedSet, locked: false });
    // Fields are enabled.
    expect(wrapper.find(".AppInput-stub").attributes("data-disabled")).toBe(
      "false",
    );
    const saveBtn = wrapper
      .findAll("button.app-button-stub")
      .find((b) => b.text().includes("Save"));
    await saveBtn.trigger("click");

    expect(apiClient.patch).toHaveBeenCalledTimes(1);
    const [, body] = apiClient.patch.mock.calls[0];
    expect(body).toMatchObject({ id: 7, name: "Eval slice", locked: false });
  });
});
