// The glyph a Settings readout shows when it has no value to show.
//
// It is an em dash, and it is a value, not prose: it sits in a table cell or a
// key/value pair where a date or a port number would be, and it has to read as
// "nothing here" rather than as a stray hyphen with spaces around it. The
// repo-wide em-dash-to-hyphen sweep that ran over the prose (commit c760dd05)
// took these three with it, turning `"—"` into `" - "` in a token table's date
// columns and the ComfyUI port readout.
//
// Pinned here rather than left to review, because the next sweep will look
// exactly as harmless as the last one did.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { readFileSync } from "node:fs";
import { join } from "node:path";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return { isReadOnly: ref(false) };
});

vi.mock("../../api/config", () => ({
  getUserConfig: vi.fn(async () => ({ data: {} })),
  patchUserConfig: vi.fn(async () => ({ data: {} })),
}));

vi.mock("../../api/comfyui", () => ({
  listWorkflows: vi.fn(async () => []),
  deleteWorkflow: vi.fn(),
  importWorkflow: vi.fn(),
}));

vi.mock("../../api/users", () => ({
  getAuthState: vi.fn(async () => ({ auth_required: true, username: "owner" })),
  changePassword: vi.fn(),
  listTokens: vi.fn(async () => [
    {
      id: 1,
      label: "example-token",
      scope: "READ",
      resource_type: null,
      created_at: null,
      last_used_at: null,
      expires_at: null,
    },
  ]),
  createToken: vi.fn(),
  patchToken: vi.fn(),
  deleteToken: vi.fn(),
  uploadWatermark: vi.fn(),
  deleteWatermark: vi.fn(),
}));

vi.mock("../../api/pictureSets", () => ({ listPictureSets: vi.fn(async () => []) }));
vi.mock("../../api/projects", () => ({ listProjects: vi.fn(async () => []) }));
vi.mock("../../api/characters", () => ({ listCharacters: vi.fn(async () => []) }));
vi.mock("../../utils/clipboard", () => ({ copyText: vi.fn() }));

vi.mock("vuetify/components", () => ({
  VSwitch: { name: "v-switch", template: "<div><slot /></div>" },
}));

import AccountSection from "./AccountSection.vue";
import WorkflowsSection from "./WorkflowsSection.vue";

const EM_DASH = "—";

const stubs = {
  VIcon: true,
  "v-icon": true,
  AppDialog: true,
  AppSelect: true,
  AppInput: true,
  AppButton: {
    props: ["disabled", "loading"],
    template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
  },
};

async function settle(wrapper) {
  for (let i = 0; i < 4; i += 1) await nextTick();
  return wrapper;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("a Settings readout with no value", () => {
  it("shows an em dash for a token that has never been used", async () => {
    const wrapper = await settle(
      mount(AccountSection, { props: { open: true }, global: { stubs } }),
    );

    const cells = wrapper.findAll(".account-token-sub").map((c) => c.text());
    expect(cells.length).toBeGreaterThanOrEqual(2);
    // Created and last-used, both absent on this row.
    expect(cells[0]).toBe(EM_DASH);
    expect(cells[1]).toBe(EM_DASH);
    for (const cell of cells) expect(cell).not.toBe("-");

    wrapper.unmount();
  });

  it("shows an em dash for the ComfyUI port when none is configured", async () => {
    const wrapper = await settle(
      mount(WorkflowsSection, { props: { open: true }, global: { stubs } }),
    );

    const values = wrapper.findAll(".wf-host-value").map((v) => v.text());
    expect(values).toContain("Not configured");
    expect(values).toContain(EM_DASH);
    expect(values).not.toContain("-");

    wrapper.unmount();
  });
});

// The sweep flattened TEN of these, across seven files. Each count in this
// file has been wrong once already: it was three, then eight, and the eight
// missed `FolderMappingPreviewStep.vue` because that one is markup (`> - <`)
// rather than a string literal, and the first version of this regex only
// looked between double quotes. Rather than mount seven components for one
// glyph each, this reads the sources. Verified against c760dd05, which is
// where every one of them lost its em dash.
describe("no value placeholder was left flattened by the em-dash sweep", () => {
  const SWEPT = [
    "components/settings/AccountSection.vue",
    "components/settings/WorkflowsSection.vue",
    "components/settings/SnapshotsSection.vue",
    "components/widgets/RestoreConfirmDialog.vue",
    "components/panels/StatsSidebar.vue",
    "components/reviews/ReviewArchivedReceipt.vue",
    "components/folders/FolderMappingPreviewStep.vue",
  ];

  it.each(SWEPT)("%s spells its empty value as an em dash", (relative) => {
    const source = readFileSync(join(process.cwd(), "src", relative), "utf8");
    // A lone hyphen standing on its own between quotes or tags is only ever a
    // placeholder; a prose hyphen has words either side of it and is not
    // matched. Both delimiters, or the markup form walks straight past.
    expect(source).not.toMatch(/["'>]\s-\s["'<]/);
    expect(source).toContain(EM_DASH);
  });
});
