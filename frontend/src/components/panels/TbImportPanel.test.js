import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

vi.mock("../../api/projects", () => ({
  listProjects: vi.fn(),
}));

import { listProjects } from "../../api/projects";
import TbImportPanel from "./TbImportPanel.vue";
import { IMPORT_FILE_ACCEPT } from "../../utils/media.js";

function mountPanel() {
  return mount(TbImportPanel, {
    attachTo: document.body,
    props: { open: true },
    global: { stubs: { "v-icon": true } },
  });
}

beforeEach(() => {
  listProjects.mockResolvedValue([{ id: 7, name: "مشروع الصور 🖼️" }]);
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("TbImportPanel hardening", () => {
  it("offers the picker the shared import accept, not an undefined binding", async () => {
    // A `:accept` bound to a name the <script setup> never imported renders no
    // `accept` attribute at all - the picker then offers every file on the
    // disk, and nothing about the markup looks wrong. This asserts the rendered
    // attribute rather than the binding, because that is the difference.
    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.get(".tb-import-file-input").attributes("accept")).toBe(
      IMPORT_FILE_ACCEPT,
    );

    wrapper.unmount();
  });

  it("labels the project selector and exposes complete tab relationships", async () => {
    const wrapper = mountPanel();
    await flushPromises();

    const select = wrapper.get(".tb-import-project select");
    expect(wrapper.get(".tbm-title").attributes("for")).toBe(
      select.attributes("id"),
    );

    const tabs = wrapper.findAll('[role="tab"]');
    expect(tabs).toHaveLength(5);
    expect(tabs[0].attributes()).toMatchObject({
      "aria-selected": "true",
      tabindex: "0",
    });
    expect(tabs[1].attributes("tabindex")).toBe("-1");

    const panel = wrapper.get('[role="tabpanel"]');
    expect(tabs[0].attributes("aria-controls")).toBe(panel.attributes("id"));
    expect(panel.attributes("aria-labelledby")).toBe(tabs[0].attributes("id"));

    wrapper.unmount();
  });

  it("moves selection and focus with the tab arrow-key contract", async () => {
    const wrapper = mountPanel();
    await flushPromises();
    const firstTab = wrapper.findAll('[role="tab"]')[0];

    await firstTab.trigger("keydown", { key: "ArrowRight" });
    await flushPromises();

    const activeTab = wrapper.findAll('[role="tab"]')[1];
    expect(activeTab.attributes("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(activeTab.element);
    expect(wrapper.get('[role="tabpanel"]').attributes("aria-labelledby")).toBe(
      activeTab.attributes("id"),
    );

    wrapper.unmount();
  });

  it("explains project-load failure, preserves import, and offers retry", async () => {
    listProjects.mockRejectedValueOnce(new Error("offline"));
    const wrapper = mountPanel();
    await flushPromises();

    const alert = wrapper.get('[role="alert"]');
    expect(alert.text()).toContain("Importing without one still works");
    expect(wrapper.get(".tb-import-project select").attributes("disabled")).toBeUndefined();

    listProjects.mockResolvedValueOnce([{ id: 8, name: "Recovered" }]);
    await alert.get("button").trigger("click");
    await flushPromises();

    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    expect(wrapper.get(".tb-import-project select").text()).toContain(
      "Recovered",
    );

    wrapper.unmount();
  });
});
