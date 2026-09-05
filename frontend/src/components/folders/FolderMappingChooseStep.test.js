// "Add a library" - the wizard's first pane.
//
// The picker's whole job is to ask the folder and then say what adding it would
// mean, so what is worth pinning is that it renders the SERVER's words for all
// five verdicts, that the two refusals offer no button, and that an answer for
// a folder the owner has since navigated away from cannot be acted on.
//
// "vault" and "empty" are added here and switched to, through the same
// switch-and-reload LibrariesSection uses. "pictures" creates nothing: it
// swaps the verdict card for the scan card in place and tells the wizard,
// which builds the library only once the mapping is accepted.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";

// The real VDialog needs Vuetify's defaults instance; render the slot inline,
// as LibrariesSection.test.js does.
vi.mock("vuetify/components", () => ({
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VCard: { name: "v-card", template: "<div><slot /></div>" },
  VCardTitle: { name: "v-card-title", template: "<div><slot /></div>" },
  VCardText: { name: "v-card-text", template: "<div><slot /></div>" },
  VCardActions: { name: "v-card-actions", template: "<div><slot /></div>" },
  VBtn: { name: "v-btn", template: "<button><slot /></button>" },
  VSpacer: { name: "v-spacer", template: "<div />" },
  VTextField: { name: "v-text-field", template: "<input />" },
  VCheckbox: { name: "v-checkbox", template: "<input type='checkbox' />" },
  VProgressCircular: { name: "v-progress-circular", template: "<i />" },
}));

import FolderMappingChooseStep from "./FolderMappingChooseStep.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";
import {
  addLibrary,
  inspectLibraryPath,
  setActiveLibrary,
} from "../../api/libraries";
import { startFolderStructureRead } from "../../api/folderStructure";
import {
  useLibrariesStore,
  useLibrarySwitchStore,
} from "../../stores/useLibrariesStore";
import { reloadPage } from "../../utils/reloadPage";

vi.mock("../../api/libraries", () => ({
  inspectLibraryPath: vi.fn(),
  addLibrary: vi.fn(),
  setActiveLibrary: vi.fn(),
  listLibraries: vi.fn(),
}));

vi.mock("../../api/folderStructure", () => ({
  startFolderStructureRead: vi.fn(),
  getFolderStructureReadStatus: vi.fn(),
  cancelFolderStructureRead: vi.fn(),
}));

vi.mock("../../api/folders", () => ({
  browseFilesystem: vi.fn().mockResolvedValue({ path: "/", entries: [] }),
  createFilesystemFolder: vi.fn(),
}));

// Matches LibrariesSection.test.js: the switch flow ends in a real page
// reload in production, which jsdom cannot do and this suite has no reason to
// exercise.
vi.mock("../../utils/reloadPage", () => ({ reloadPage: vi.fn() }));

// Addable, but NOT the "pictures" verdict - the default for every test that
// is about the ask/inspect/add mechanics rather than about what "pictures"
// specifically does once added.
const VAULT = {
  verdict: "vault",
  path: "/home/me/Pictures/Generations",
  can_add: true,
  headline: "A library you already made",
  detail: "PixlStash attaches it. Nothing inside it changes.",
  suggested_name: "Generations",
  picture_count: 0,
  picture_count_capped: false,
  library: null,
};

const PICTURES = {
  verdict: "pictures",
  path: "/home/me/Pictures/Generations",
  can_add: true,
  headline: "28,412 pictures, no library here yet",
  detail: "Bring them in and name what your folders mean. Nothing is moved.",
  suggested_name: "Generations",
  picture_count: 28412,
  picture_count_capped: false,
  library: null,
};

let pinia;

function mountDialog(props = {}) {
  return mount(FolderMappingChooseStep, {
    props,
    global: {
      plugins: [pinia],
      stubs: {
        // Its own suite covers it; here it is a path source, and mounting it
        // for real drags in Vuetify components this file has no reason to mock.
        FolderBrowser: true,
        AppButton: {
          props: ["disabled", "loading"],
          template:
            '<button :disabled="disabled" @click="$emit(\'click\', $event)"><slot /></button>',
        },
      },
    },
  });
}

async function settle(wrapper) {
  await nextTick();
  await nextTick();
  await nextTick();
  return wrapper;
}

async function typePath(wrapper, path) {
  await wrapper.find(".choose-step__field .app-input__field").setValue(path);
  await wrapper.find(".choose-step__field .app-input__field").trigger("blur");
  return settle(wrapper);
}

function actionButton(wrapper) {
  return wrapper
    .findAll(".choose-step__verdict button")
    .find((button) => button.text().trim() !== "Cancel");
}

function seedActiveLibrary() {
  const librariesStore = useLibrariesStore();
  librariesStore.libraries = [
    {
      uuid: "uuid-current",
      name: "Family Photos",
      is_active: true,
      is_reachable: true,
      path: "/home/me/Pictures/Family",
      active_share_links: 0,
    },
    {
      uuid: "uuid-work",
      name: "Client work",
      is_active: false,
      is_reachable: true,
      path: "/mnt/work/client",
      active_share_links: 0,
    },
  ];
}

beforeEach(() => {
  window.localStorage.clear();
  pinia = createPinia();
  setActivePinia(pinia);
  vi.clearAllMocks();
  inspectLibraryPath.mockResolvedValue(structuredClone(VAULT));
  addLibrary.mockResolvedValue({ uuid: "uuid-new", name: "Generations" });
  setActiveLibrary.mockResolvedValue({ status: "ok" });
  startFolderStructureRead.mockReturnValue(new Promise(() => {}));
});

describe("asking the folder", () => {
  it("asks nothing until a path is given", async () => {
    await settle(mountDialog());
    expect(inspectLibraryPath).not.toHaveBeenCalled();
  });

  it.each([
    ["vault", "A library you already made", "Add it"],
    ["pictures", "28,412 pictures, no library here yet", "Bring them in"],
    ["empty", "Empty", "Start here"],
  ])(
    "renders the server's words for %s and offers its own verb",
    async (verdict, headline, label) => {
      inspectLibraryPath.mockResolvedValue({
        ...structuredClone(VAULT),
        verdict,
        headline,
        detail: "whatever the server said",
      });
      const wrapper = await typePath(mountDialog(), VAULT.path);

      expect(wrapper.text()).toContain(headline);
      expect(wrapper.text()).toContain("whatever the server said");
      expect(actionButton(wrapper).text().trim()).toBe(label);
    },
  );

  it.each([
    [
      "overlaps",
      "Inside a library you already have",
      '"Generations" covers this folder.',
    ],
    [
      "attached",
      "Already on the list",
      'This folder is the library "Client work".',
    ],
  ])(
    "offers no button for %s and shows the reason",
    async (verdict, headline, detail) => {
      inspectLibraryPath.mockResolvedValue({
        ...structuredClone(VAULT),
        verdict,
        can_add: false,
        headline,
        detail,
      });
      const wrapper = await typePath(
        mountDialog(),
        "/home/me/Pictures/Generations/2024",
      );

      expect(wrapper.text()).toContain(detail);
      expect(actionButton(wrapper)).toBeFalsy();
      expect(wrapper.find(".mapping-card--warn").exists()).toBe(true);
    },
  );

  it("shows the server's message when the folder cannot be read", async () => {
    inspectLibraryPath.mockRejectedValue({
      response: {
        data: { detail: "Path is in a restricted system directory: /etc" },
      },
    });
    const wrapper = await typePath(mountDialog(), "/etc");

    expect(wrapper.find(".choose-step__error").text()).toContain(
      "restricted system directory",
    );
    expect(wrapper.find(".choose-step__verdict").exists()).toBe(false);
  });
});

describe("adding it", () => {
  it("adds the path the server resolved, not the one that was typed, and switches to it", async () => {
    // The typed path may be relative to a home directory or carry a trailing
    // separator; the verdict describes the resolved one, and that is what the
    // button was offered for.
    seedActiveLibrary();
    const wrapper = await typePath(mountDialog(), "~/Pictures/Generations/");

    await actionButton(wrapper).trigger("click");
    await settle(wrapper);

    expect(addLibrary).toHaveBeenCalledWith(
      "/home/me/Pictures/Generations",
      "Generations",
    );
    expect(wrapper.emitted("close")).toBeTruthy();
    expect(setActiveLibrary).toHaveBeenCalledWith("uuid-new");
    expect(reloadPage).toHaveBeenCalled();
    const switchStore = useLibrarySwitchStore();
    expect(switchStore.targetLibrary).toEqual({
      uuid: "uuid-new",
      name: "Generations",
    });
    expect(switchStore.currentLibrary?.name).toBe("Family Photos");
    expect(startFolderStructureRead).not.toHaveBeenCalled();
  });

  it("does not switch when the create itself fails", async () => {
    seedActiveLibrary();
    addLibrary.mockRejectedValue({
      response: { data: { detail: '"Generations" covers this folder.' } },
    });
    const wrapper = await typePath(mountDialog(), VAULT.path);

    await actionButton(wrapper).trigger("click");
    await settle(wrapper);

    expect(setActiveLibrary).not.toHaveBeenCalled();
    expect(wrapper.emitted("close")).toBeFalsy();
  });

  it("shows the server's refusal and re-asks, so the card agrees with it", async () => {
    // The server re-inspects, so a folder that became covered between the
    // verdict and the click is refused there rather than here.
    addLibrary.mockRejectedValue({
      response: { data: { detail: '"Generations" covers this folder.' } },
    });
    const wrapper = await typePath(mountDialog(), VAULT.path);
    inspectLibraryPath.mockClear();

    await actionButton(wrapper).trigger("click");
    await settle(wrapper);

    expect(wrapper.find(".choose-step__error").text()).toContain(
      "covers this folder",
    );
    expect(inspectLibraryPath).toHaveBeenCalled();
    expect(setActiveLibrary).not.toHaveBeenCalled();
  });

  it("does not silently fail on the first press when blur lands before the click", async () => {
    // A browser orders mousedown -> blur -> click. `@blur` re-inspects, and
    // before the no-op guard that cleared the verdict synchronously, so the
    // click that followed found nothing to add and did nothing - every time,
    // on the first press, with no message.
    //
    // The slow answer is load-bearing. With a mock that settles inside the
    // blur's own `nextTick` this passes either way, which is exactly how the
    // bug survived being "covered": the click has to land while the re-ask is
    // still in flight, as it does in a browser.
    const wrapper = await typePath(mountDialog(), VAULT.path);
    inspectLibraryPath.mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(() => resolve(structuredClone(VAULT)), 20),
        ),
    );

    await wrapper.find(".choose-step__field .app-input__field").trigger("blur");
    await actionButton(wrapper)?.trigger("click");
    await settle(wrapper);

    expect(addLibrary).toHaveBeenCalled();
  });

  it("sends a name the owner typed over the one the folder suggested", async () => {
    const wrapper = await typePath(mountDialog(), VAULT.path);

    await wrapper
      .find(".choose-step__name .app-input__field")
      .setValue("2024 client");
    await actionButton(wrapper).trigger("click");
    await settle(wrapper);

    expect(addLibrary).toHaveBeenCalledWith(VAULT.path, "2024 client");
  });

  it("stops overwriting the name once the owner has set one", async () => {
    // Two folders both called `2024` are only addable because of this: the
    // second verdict must not put the basename back over what was typed.
    const wrapper = await typePath(mountDialog(), VAULT.path);
    await wrapper.find(".choose-step__name .app-input__field").setValue("Mine");

    inspectLibraryPath.mockResolvedValue({
      ...structuredClone(VAULT),
      path: "/home/me/Pictures/Other",
      suggested_name: "Other",
    });
    await typePath(wrapper, "/home/me/Pictures/Other");

    expect(
      wrapper.find(".choose-step__name .app-input__field").element.value,
    ).toBe("Mine");
  });

  it("cannot act on a verdict for a folder the path has moved on from", async () => {
    // The guard the file header claims and did not test. A slow answer for the
    // old folder must not arm the button for the new one.
    let releaseFirst;
    inspectLibraryPath.mockImplementationOnce(
      () => new Promise((resolve) => (releaseFirst = resolve)),
    );
    const wrapper = mountDialog();
    await settle(wrapper);
    await typePath(wrapper, "/home/me/Pictures/Slow");

    inspectLibraryPath.mockResolvedValue({
      ...structuredClone(VAULT),
      path: "/home/me/Pictures/Fast",
    });
    await typePath(wrapper, "/home/me/Pictures/Fast");

    // The first answer lands last, naming a folder nobody is looking at.
    releaseFirst({
      ...structuredClone(VAULT),
      path: "/home/me/Pictures/Slow",
    });
    await settle(wrapper);

    await actionButton(wrapper).trigger("click");
    await settle(wrapper);

    expect(addLibrary).toHaveBeenCalledWith(
      "/home/me/Pictures/Fast",
      "Generations",
    );
  });
});

describe("a 'pictures' verdict", () => {
  beforeEach(() => {
    inspectLibraryPath.mockResolvedValue(structuredClone(PICTURES));
  });

  it("swaps the verdict card for the scan card in place and creates nothing", async () => {
    seedActiveLibrary();
    const wrapper = await typePath(mountDialog(), PICTURES.path);
    await wrapper
      .find(".choose-step__name .app-input__field")
      .setValue("2024 client");

    await actionButton(wrapper).trigger("click");
    await settle(wrapper);

    expect(wrapper.find(".choose-step__verdict").exists()).toBe(false);
    expect(wrapper.find(".scan-step .mapping-card").text()).toContain(
      "Working out what your folders mean",
    );
    // The path is fixed for the read's lifetime.
    expect(
      wrapper.find(".choose-step__field .app-input__field").element.disabled,
    ).toBe(true);
    expect(wrapper.emitted("scan")[0][0]).toEqual({
      path: PICTURES.path,
      label: "2024 client",
    });
    expect(startFolderStructureRead).toHaveBeenCalledWith(PICTURES.path, {
      matchExisting: false,
    });
    expect(addLibrary).not.toHaveBeenCalled();
    expect(setActiveLibrary).not.toHaveBeenCalled();
    expect(wrapper.emitted("close")).toBeFalsy();
  });

  it("hands the scan card's Cancel up unchanged", async () => {
    const wrapper = await typePath(mountDialog(), PICTURES.path);
    await actionButton(wrapper).trigger("click");
    await settle(wrapper);

    const cancel = wrapper
      .findAll(".scan-step button")
      .find((button) => button.text().trim() === "Cancel");
    await cancel.trigger("click");

    expect(wrapper.emitted("cancel")).toBeTruthy();
  });
});

describe("resuming a saved read", () => {
  it("reattaches to the read at that path without asking the folder", async () => {
    const wrapper = await settle(
      mountDialog({
        resume: {
          taskId: "task-7",
          path: PICTURES.path,
          label: "Generations",
          mode: "local_import",
        },
      }),
    );

    expect(inspectLibraryPath).not.toHaveBeenCalled();
    expect(startFolderStructureRead).not.toHaveBeenCalled();
    expect(wrapper.find(".choose-step__field .app-input__field").element.value).toBe(
      PICTURES.path,
    );
    expect(wrapper.find(".scan-step").exists()).toBe(true);
    expect(wrapper.emitted("task")[0][0]).toBe("task-7");
  });
});

describe("the browser", () => {
  it("is handed the paths already registered", async () => {
    seedActiveLibrary();
    const wrapper = await settle(mountDialog());

    expect(wrapper.findComponent(FolderBrowser).props("registeredPaths")).toEqual([
      "/home/me/Pictures/Family",
      "/mnt/work/client",
    ]);
  });
});

describe("in a container", () => {
  it("offers no Browse and says the path is a container path", async () => {
    useLibrariesStore().inDocker = true;
    const wrapper = await settle(mountDialog());

    expect(wrapper.find(".choose-step__browse").exists()).toBe(false);
    expect(wrapper.text()).toContain("running in a container");
  });
});
