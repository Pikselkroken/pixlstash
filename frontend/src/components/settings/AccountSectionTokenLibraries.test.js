// The "Every library" switch in the API token table (#1787).
//
// A token works only in the library it was made in unless the owner says
// otherwise, and the server lets only the owner's own session on the local
// network say so. The table has to agree with that before the request is
// made: no column with one library, no usable switch on a share link or for a
// caller the server would refuse, and a refused change must not be left
// showing as made.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick, reactive } from "vue";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return { isReadOnly: ref(false), API_BASE_URL: "http://127.0.0.1:9537/api/v1" };
});
vi.mock("../../api/config", () => ({
  getUserConfig: vi.fn(async () => ({ data: {} })),
  patchUserConfig: vi.fn(async () => ({ data: {} })),
}));

const setTokenLibraries = vi.fn();
vi.mock("../../api/users", () => ({
  getAuthState: vi.fn(async () => ({ username: "owner", has_password: true })),
  changePassword: vi.fn(),
  listTokens: vi.fn(async () => [
    { id: 1, description: "agent", scope: "ALL", resource_type: null, all_libraries: false },
    { id: 2, description: "share", scope: "READ", resource_type: "picture_set", resource_id: 3, all_libraries: false },
  ]),
  createToken: vi.fn(),
  patchToken: vi.fn(),
  deleteToken: vi.fn(),
  setTokenLibraries: (...args) => setTokenLibraries(...args),
  uploadWatermark: vi.fn(),
  deleteWatermark: vi.fn(),
}));
vi.mock("../../api/pictureSets", () => ({ listPictureSets: vi.fn(async () => []) }));
vi.mock("../../api/projects", () => ({ listProjects: vi.fn(async () => []) }));
vi.mock("../../api/characters", () => ({ listCharacters: vi.fn(async () => []) }));
vi.mock("../../utils/clipboard", () => ({ copyText: vi.fn() }));

const store = reactive({ libraries: [], canManage: true });
vi.mock("../../stores/useLibrariesStore", () => ({
  useLibrariesStore: () => store,
}));

vi.mock("vuetify/components", () => ({
  VSwitch: {
    name: "v-switch",
    props: ["modelValue", "disabled"],
    emits: ["update:modelValue"],
    template:
      '<button role="switch" :aria-checked="String(!!modelValue)" :disabled="disabled" @click="$emit(\'update:modelValue\', !modelValue)" />',
  },
  VTooltip: {
    name: "VTooltip",
    props: ["text"],
    setup: (_p, { slots }) => () => slots.default?.(),
  },
}));

import AccountSection from "./AccountSection.vue";
import Tooltip from "../widgets/Tooltip.vue";

const stubs = {
  VIcon: true,
  "v-icon": true,
  AppDialog: true,
  AppSelect: true,
  AppInput: true,
  AppButton: true,
  ConnectAgentDialog: true,
};

async function table() {
  const wrapper = mount(AccountSection, { props: { open: true }, global: { stubs } });
  for (let i = 0; i < 4; i += 1) await nextTick();
  return wrapper;
}

// The first switch in a row is "Every library"; the second is the watermark.
const librarySwitch = (wrapper, row) =>
  wrapper.findAll("tbody tr")[row].findAll('[role="switch"]')[0];
// What hovering that row's switch says. Read off the tip rather than the
// page: a tip draws nothing until it is hovered.
const lockedReason = (wrapper, row) =>
  wrapper.findAll("tbody tr")[row].findComponent(Tooltip).props("text");

beforeEach(() => {
  setTokenLibraries.mockReset();
  setTokenLibraries.mockResolvedValue({ all_libraries: true });
  store.libraries = [{ uuid: "a" }, { uuid: "b" }];
  store.canManage = true;
});

describe("the Every library switch", () => {
  it("has no column while there is only one library", async () => {
    store.libraries = [{ uuid: "a" }];
    const wrapper = await table();

    expect(wrapper.find("thead").text()).not.toMatch(/every library/i);
    // Only the watermark switch is left on each row.
    expect(wrapper.findAll("tbody tr")[0].findAll('[role="switch"]')).toHaveLength(1);
  });

  it("widens the token it is on, and shows it", async () => {
    const wrapper = await table();
    expect(wrapper.find("thead").text()).toMatch(/every library/i);

    await librarySwitch(wrapper, 0).trigger("click");
    await nextTick();

    expect(setTokenLibraries).toHaveBeenCalledWith(1, true);
    expect(librarySwitch(wrapper, 0).attributes("aria-checked")).toBe("true");

    // And off again pins it, rather than asking for the same thing twice.
    await librarySwitch(wrapper, 0).trigger("click");
    await nextTick();
    expect(setTokenLibraries).toHaveBeenLastCalledWith(1, false);
  });

  it("is locked on a share link, and says why", async () => {
    const wrapper = await table();

    expect(librarySwitch(wrapper, 0).attributes("disabled")).toBeUndefined();
    expect(librarySwitch(wrapper, 1).attributes("disabled")).toBeDefined();
    expect(lockedReason(wrapper, 1)).toMatch(/share link stays/i);
    expect(lockedReason(wrapper, 0)).toBe("");
  });

  it("is locked for a caller the server would refuse, and says why", async () => {
    store.canManage = false;
    const wrapper = await table();

    expect(librarySwitch(wrapper, 0).attributes("disabled")).toBeDefined();
    expect(lockedReason(wrapper, 0)).toMatch(/local network/i);
  });

  it("goes back to off when the change is refused, with the reason", async () => {
    setTokenLibraries.mockRejectedValue({
      response: { data: { detail: "a token cannot" } },
    });
    const wrapper = await table();

    await librarySwitch(wrapper, 0).trigger("click");
    for (let i = 0; i < 3; i += 1) await nextTick();

    expect(librarySwitch(wrapper, 0).attributes("aria-checked")).toBe("false");
    expect(wrapper.find(".account-tokens-error").text()).toMatch(/a token cannot/);
  });
});
