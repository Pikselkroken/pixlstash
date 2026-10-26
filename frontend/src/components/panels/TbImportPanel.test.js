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

function fileAt(path, content = "x") {
  const file = new File([content], path.split("/").pop());
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
}

async function pickEagleFolder(wrapper, files) {
  const tab = wrapper.findAll('[role="tab"]').find((t) => t.text() === "Eagle");
  await tab.trigger("click");
  const input = wrapper.get("input[webkitdirectory]");
  Object.defineProperty(input.element, "files", { value: files });
  await input.trigger("change");
  await flushPromises();
}

describe("TbImportPanel Eagle tab", () => {
  it("imports a picked library's originals", async () => {
    const wrapper = mountPanel();
    await flushPromises();
    await pickEagleFolder(wrapper, [
      fileAt("My.library/metadata.json", JSON.stringify({ folders: [] })),
      fileAt(
        "My.library/images/A.info/metadata.json",
        JSON.stringify({ name: "cat", ext: "png", tags: ["cat"] }),
      ),
      fileAt("My.library/images/A.info/cat.png"),
      fileAt("My.library/images/A.info/cat_thumbnail.png"),
    ]);

    const [[payload]] = wrapper.emitted("local-import");
    expect(payload.files.map((f) => f.name)).toEqual(["cat.png"]);
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("says so when the folder is not a library, and imports nothing", async () => {
    const wrapper = mountPanel();
    await flushPromises();
    await pickEagleFolder(wrapper, [fileAt("Pictures/cat.png")]);

    expect(wrapper.emitted("local-import")).toBeUndefined();
    expect(wrapper.get('[role="alert"]').text()).toMatch(/not an Eagle library/);
    wrapper.unmount();
  });
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
    expect(tabs).toHaveLength(6);
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
