// PictureSetEditor locking behaviour (picture-set locking, plan §3.2):
//   - a locked set renders its name/description/project/appearance fields
//     disabled (only the Locked checkbox stays active);
//   - saving a locked set sends ONLY { id, locked } (never the other fields,
//     which would 423 server-side), so unticking Locked + Save is the unlock
//     path;
//   - saving an UNLOCKED set sends the full body including locked.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { h } from "vue";

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
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
  props: ["modelValue", "disabled", "label", "options", "rows", "multiple"],
  emits: ["update:modelValue"],
  template: `<div class="${name}-stub" :data-disabled="disabled ? 'true' : 'false'" :data-multiple="multiple ? 'true' : 'false'"></div>`,
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

// The editor reports save failures through `useNoticeStore`, so a mount needs an
// active Pinia even though these tests never assert on a notice.
const globalOpts = {
  plugins: [],
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
  const pinia = createPinia();
  setActivePinia(pinia);
  globalOpts.plugins = [pinia];
  apiClient.post.mockClear();
  apiClient.patch.mockClear();
});

describe("PictureSetEditor - locked set", () => {
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

describe("PictureSetEditor - unlocked set", () => {
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

  it("loads multiple projects and PATCHes the complete edited selection", async () => {
    const wrapper = mount(PictureSetEditor, {
      props: {
        open: true,
        set: { ...lockedSet, locked: false, project_ids: [1, 2] },
        backendUrl: "http://x",
        projects: [
          { id: 1, name: "One" },
          { id: 2, name: "Two" },
          { id: 3, name: "Three" },
        ],
      },
      global: globalOpts,
    });
    const projectSelect = wrapper.findComponent({ name: "AppSelect" });
    expect(projectSelect.attributes("data-multiple")).toBe("true");
    expect(projectSelect.props("modelValue")).toEqual(["1", "2"]);

    projectSelect.vm.$emit("update:modelValue", ["2", "3"]);
    await wrapper.vm.$nextTick();
    const saveBtn = wrapper
      .findAll("button.app-button-stub")
      .find((button) => button.text().includes("Save"));
    await saveBtn.trigger("click");

    expect(apiClient.patch).toHaveBeenCalledTimes(1);
    const [, body] = apiClient.patch.mock.calls[0];
    expect(body).toMatchObject({ project_ids: [2, 3] });
    expect(body).not.toHaveProperty("project_id");
  });
});

// The adapter tray is wired here, not tested here - `AdapterTray.test.js` owns
// its behaviour. What only this file can prove is the WIRING: that the editor
// mounts it at all, and that it hands it this set rather than some other
// entity. Both are invisible to the tray's own suite, and a tray pointed at the
// wrong entity type renders another entity's adapters under this set's name.
const AdapterTrayStub = {
  name: "AdapterTray",
  props: ["entityType", "entityId"],
  template: `<div class="adapter-tray-stub" :data-type="entityType" :data-id="entityId"></div>`,
};

describe("PictureSetEditor - adapter tray", () => {
  function mountWithTray(props) {
    return mount(PictureSetEditor, {
      props: { backendUrl: "http://x", projects: [], ...props },
      global: {
        ...globalOpts,
        stubs: { ...globalOpts.stubs, AdapterTray: AdapterTrayStub },
      },
    });
  }

  it("points the tray at this set", () => {
    const tray = mountWithTray({ open: true, set: lockedSet }).find(
      ".adapter-tray-stub",
    );
    expect(tray.exists()).toBe(true);
    expect(tray.attributes("data-type")).toBe("set");
    expect(tray.attributes("data-id")).toBe("7");
  });

  it("shows it even for a locked set, because it is read-only", () => {
    // Everything else on this dialog is disabled while the set is locked. The
    // tray writes nothing, so hiding it would withhold a fact for no reason.
    expect(
      mountWithTray({ open: true, set: lockedSet })
        .find(".adapter-tray-stub")
        .exists(),
    ).toBe(true);
  });

  it("does not mount it before the dialog has ever been opened", () => {
    // The mount is what triggers the read, and the hosts keep this component
    // alive for the life of the view. Once opened it stays mounted - including
    // through the close, so the widest row does not vanish from under the leave
    // transition - and re-reads via its key on the next open. What must never
    // happen is a read for a dialog the user has not opened at all.
    expect(
      mountWithTray({ open: false, set: lockedSet })
        .find(".adapter-tray-stub")
        .exists(),
    ).toBe(false);
  });
});
