// The other half of the folder URL round trip (`useAppNavigationFolders.test.js`
// is the first): what the sidebar does when the route arrives.
//
// `/ref-folder/:id` is restored by the `activeFolderKey` watcher, which
// rebuilt the payload from the folder listing alone and therefore always
// selected the folder ROOT. Since a subfolder click now keeps its `?path=`,
// that watcher would have thrown it away on the very next reload - the URL
// would say the subfolder and the grid would show the whole folder.
//
// The id keeps owning `referenceFolderId`, so the grid query still carries
// `reference_folder_id`; only `pathPrefix` and the highlight key come from the
// query.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";

const apiGet = vi.fn();

vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef } = await import("vue");
  return {
    apiClient: {
      get: (...args) => apiGet(...args),
      post: vi.fn().mockResolvedValue({ data: {} }),
      patch: vi.fn().mockResolvedValue({ data: {} }),
      put: vi.fn().mockResolvedValue({ data: {} }),
      delete: vi.fn().mockResolvedValue({ data: {} }),
    },
    onSessionReset: () => () => {},
    activateShareToken: vi.fn(),
    appendShareToken: (url) => url,
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated: makeRef(true),
    isReadOnly: makeRef(false),
    sessionContext: makeRef(null),
    login: vi.fn(),
    logout: vi.fn(),
    newOperationBatchId: () => "cli-test",
    operationBatchHeaders: () => undefined,
    setRequestClientId: vi.fn(),
    notifySessionReset: vi.fn(),
    toBackendWebSocketUrl: () => "",
    API_BASE_URL: "/api/v1",
  };
});

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const { vi: vitest } = await import("vitest");
  const route = reactive({
    query: {},
    params: {},
    path: "/",
    name: "all-pictures",
  });
  return {
    useRoute: () => route,
    useRouter: () => ({
      push: vitest.fn(),
      replace: vitest.fn(),
      currentRoute: { value: route },
    }),
  };
});

import SideBar from "./SideBar.vue";
import { useViewStore } from "../../stores/useViewStore";
import { useSidebarStore } from "../../stores/useSidebarStore";

const ROOT = "/home/me/library/refs";
const SUB = "/home/me/library/refs/2024/summer";
// A second folder, spelled the way a Windows server's listing spells it.
const WIN_ROOT = "D:\\library\\refs";
// A third: POSIX, but with a backslash inside a folder NAME. Legal on POSIX,
// and the reason `_urlPath` is not folded into `_samePath`.
const ODD_ROOT = "/home/me/refs/a\\b";

// `status: active` with no `last_scanned` is what makes `selectedFolderScanning`
// true while this folder is selected, which is how a test sees the sidebar's
// selection without reaching into the component: it reaches
// `sidebarStore.folderScanning`, and it is driven by `selectedFolderReferenceId`
// - the half of the selection the teardown below has to clear.
const FOLDER = {
  id: 5,
  folder: ROOT,
  label: "Refs",
  active: true,
  status: "active",
  last_scanned: null,
};

const WIN_FOLDER = { id: 6, folder: WIN_ROOT, label: "Win refs", active: true };
const ODD_FOLDER = { id: 7, folder: ODD_ROOT, label: "Odd refs", active: true };

const IMPORT_FOLDER = { id: 9, folder: "/home/me/inbox", label: "Inbox" };

function respond(url) {
  if (String(url).includes("/import-folders")) {
    return { data: { folders: [IMPORT_FOLDER] } };
  }
  if (String(url).includes("/reference-folders")) {
    return { data: { folders: [FOLDER, WIN_FOLDER, ODD_FOLDER], in_docker: true } };
  }
  return { data: [] };
}

/**
 * `teleport: true` renders the teleported layer (the sidebar's context menu
 * lives there) instead of leaving it stubbed, which `shallow` does by default.
 */
async function mountSidebar({ teleport = false } = {}) {
  const wrapper = mount(SideBar, {
    shallow: true,
    props: { backendUrl: "/api/v1" },
    global: {
      stubs: teleport ? { teleport: false } : {},
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
  });
  for (let i = 0; i < 5; i += 1) await flushPromises();
  return wrapper;
}

/** The payload the sidebar last handed the grid via `select-folder`. */
function lastFolderPayload(wrapper) {
  const emitted = wrapper.emitted("select-folder");
  return emitted ? emitted[emitted.length - 1][0] : null;
}

/** Put the app on `/ref-folder/<id>`, with or without a `?path=`. */
function routeToFolder(path, folderKey = "rf-5") {
  useViewStore().view = {
    folderKey,
    folderFilter: path
      ? { pathPrefix: path, label: path.split(/[/\\]/).pop() }
      : null,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  setActivePinia(createPinia());
  apiGet.mockReset().mockImplementation((url) => Promise.resolve(respond(url)));
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("restoring /ref-folder/:id", () => {
  it("selects the subfolder the query names, keeping the folder's id", async () => {
    const wrapper = await mountSidebar();
    routeToFolder(SUB);
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: SUB,
      // What the URL will say next time: relative to the folder its id names,
      // so the owner's folder tree stays out of the address bar (#1206 item 9).
      subPath: "2024/summer",
      label: "summer",
    });

    wrapper.unmount();
  });

  // #1206 item 9. The URL now says the subfolder relative to the folder its id
  // already names, so a reference-folder click stops putting the owner's whole
  // folder tree in the address bar and in browser history. The absolute form
  // every other test in this file uses is still read - that is what links
  // shared, and history entries made, before this carry.
  it("selects the subfolder a relative query names", async () => {
    const wrapper = await mountSidebar();
    routeToFolder("2024/summer");
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: SUB,
      subPath: "2024/summer",
      label: "summer",
    });

    wrapper.unmount();
  });

  it("re-spells a relative query into a Windows folder's separators", async () => {
    // Same rule as the absolute case: `file_path_prefix` is a literal LIKE
    // against the stored `file_path`, so a slash-spelled prefix on a Windows
    // library matches nothing.
    const wrapper = await mountSidebar();
    routeToFolder("2024/summer", "rf-6");
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 6,
      pathPrefix: `${WIN_ROOT}\\2024\\summer`,
      subPath: "2024\\summer",
      label: "summer",
    });

    wrapper.unmount();
  });

  it("selects the folder root when the route names no path", async () => {
    const wrapper = await mountSidebar();
    routeToFolder(null);
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: ROOT,
      label: "Refs",
    });

    wrapper.unmount();
  });

  it("selects the folder root when the query only restates it", async () => {
    // The root row's own click carries `pathPrefix: folder.folder`, so the URL
    // it pushes repeats the root. That must stay the folder itself - label and
    // highlight key included - not a subfolder that happens to be the root.
    const wrapper = await mountSidebar();
    routeToFolder(`${ROOT}/`);
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: ROOT,
      label: "Refs",
    });

    wrapper.unmount();
  });

  // A `?path=` that climbs out of the folder falls back to the folder root -
  // the same refusal an absolute path naming somewhere else has always got.
  // Reading the relative shape (#1206 item 9) made that refusal conditional on
  // the string LOOKING absolute: `?path=../../..` was taken as the tail
  // verbatim and built a pathPrefix outside the folder. Harmless today -
  // `file_path_prefix` is a literal escaped LIKE and `reference_folder_id`
  // still scopes the query, so it returns an empty grid - but it is the
  // containment rule, and a rule that holds by accident downstream is one
  // change away from not holding.
  it.each([
    ["a relative climb", "../../.."],
    ["a climb inside a tail", "2024/../../etc"],
    ["an absolute climb through the root", `${ROOT}/../../etc`],
    ["a bare parent", ".."],
    ["a same-directory segment", "2024/./summer"],
  ])("refuses %s and falls back to the folder root", async (_name, path) => {
    const wrapper = await mountSidebar();
    routeToFolder(path);
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: ROOT,
      label: "Refs",
    });

    wrapper.unmount();
  });

  it("still reads a POSIX folder whose NAME contains a backslash", async () => {
    // The containment check splits on `\` for a Windows root only. On POSIX a
    // `\` is an ordinary character in a name, so `a\..\b` is one segment and
    // must not be mistaken for a climb - the same rule `_subPathUnder` and the
    // re-spelling already follow.
    const wrapper = await mountSidebar();
    routeToFolder(`${ODD_ROOT}/2024`, "rf-7");
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 7,
      pathPrefix: `${ODD_ROOT}/2024`,
      subPath: "2024",
      label: "2024",
    });

    wrapper.unmount();
  });

  // The teardown at the top of the same watcher compares the sidebar's own
  // selection key against the route key it is leaving. Those two stopped being
  // the same string once a subfolder could be selected: the sidebar holds
  // `path-<abs path>` and the route only ever said `rf-5`, so the equality test
  // read "not mine" and left the highlight - and the scanning light behind
  // `selectedFolderReferenceId` - stuck on a folder the app had navigated away
  // from. Found by review on this branch; it applies to a hand-clicked
  // subfolder as much as to a restored one, which is why the guard compares on
  // the folder the selection belongs to rather than on the key.
  it("clears the selection when the route leaves a restored subfolder", async () => {
    const wrapper = await mountSidebar();
    const sidebarStore = useSidebarStore();
    routeToFolder(SUB);
    await flushPromises();
    expect(sidebarStore.folderScanning).toBe(true);

    useViewStore().view = null; // the route left every folder view
    await flushPromises();

    expect(sidebarStore.folderScanning).toBe(false);

    wrapper.unmount();
  });

  it("still clears the selection when the route leaves a folder root", async () => {
    const wrapper = await mountSidebar();
    const sidebarStore = useSidebarStore();
    routeToFolder(null);
    await flushPromises();
    expect(sidebarStore.folderScanning).toBe(true);

    useViewStore().view = null;
    await flushPromises();

    expect(sidebarStore.folderScanning).toBe(false);

    wrapper.unmount();
  });

  // The push half of item 43 gives each subfolder click a distinct URL, which
  // means vue-router now creates a Back target where it used to deduplicate
  // the identical `/ref-folder/5` and create none. Watching only
  // `activeFolderKey` left every one of those inert - `rf-5` either side, so
  // nothing re-read the query and the grid stayed on the newer subfolder while
  // the address bar went back. A Back button that moves the URL and nothing
  // else is worse than the gesture having had nowhere to go.
  it("follows Back from one subfolder to its sibling", async () => {
    const other = `${ROOT}/2023/winter`;
    const wrapper = await mountSidebar();
    routeToFolder(SUB);
    await flushPromises();
    expect(lastFolderPayload(wrapper).pathPrefix).toBe(SUB);

    routeToFolder(other); // Back: same folderKey, different ?path=
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: other,
      subPath: "2023/winter",
      label: "winter",
    });

    wrapper.unmount();
  });

  it("follows Back from a subfolder to the folder root", async () => {
    const wrapper = await mountSidebar();
    routeToFolder(SUB);
    await flushPromises();
    expect(lastFolderPayload(wrapper).pathPrefix).toBe(SUB);

    routeToFolder(ROOT); // Back again, to the root click's own URL
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: ROOT,
      label: "Refs",
    });

    wrapper.unmount();
  });

  // THE GUARDRAIL for this whole region. A route arriving twice must be inert
  // the second time.
  //
  // That one property catches the entire bug class the key-space map describes,
  // because every one of these spellings ends up compared against another one
  // here: if any crossing compares two spaces with the wrong comparator, the
  // sidebar stops recognising its own work, re-emits, and the emit pushes a
  // route that differs from the one that arrived - a new history entry whose
  // Back returns to the first spelling and starts the round again. That is not
  // a redundant fetch, it is a folder view you cannot leave.
  //
  // **Parameterised over the separator-changing inputs on purpose.** The POSIX
  // case alone passed happily while the Windows case looped, because on POSIX
  // `routeSubfolderUnder` re-spells nothing and the two spaces coincide. A
  // guardrail that only tries the input where the spaces agree cannot see a
  // spaces-disagree bug.
  describe("a route arriving twice is inert", () => {
    const cases = [
      // [name, folderKey, the ?path= as it arrives in the URL]
      ["a POSIX path, spelled as the server spells it", "rf-5", SUB],
      ["the folder root", "rf-5", ROOT],
      [
        "a Windows folder reached by a slash-spelled link",
        "rf-6",
        `${WIN_ROOT.replace(/\\/g, "/")}/2024/summer`,
      ],
      [
        "a Windows folder reached by its own spelling",
        "rf-6",
        `${WIN_ROOT}\\2024\\summer`,
      ],
      [
        "a POSIX folder whose own name contains a backslash",
        "rf-7",
        `${ODD_ROOT}/2024`,
      ],
      [
        "a POSIX SUBfolder whose name contains a backslash",
        "rf-7",
        `${ODD_ROOT}/c\\d`,
      ],
      // The shape the sidebar writes now (#1206 item 9). Every absolute case
      // above is a link or a history entry from BEFORE it, which is why both
      // shapes are here rather than one replacing the other.
      ["a POSIX subfolder, said relative to its folder", "rf-5", "2024/summer"],
      ["a Windows subfolder, said relative to its folder", "rf-6", "2024\\summer"],
      ["the same Windows subfolder, slash-spelled", "rf-6", "2024/summer"],
      [
        "a relative tail whose own name contains a backslash",
        "rf-7",
        "c\\d",
      ],
    ];

    it.each(cases)("%s", async (_name, folderKey, path) => {
      const wrapper = await mountSidebar();
      routeToFolder(path, folderKey);
      await flushPromises();
      const emittedOnce = wrapper.emitted("select-folder")?.length ?? 0;
      const fetchedOnce = apiGet.mock.calls.length;
      expect(emittedOnce).toBeGreaterThan(0);

      // Exactly what the sidebar just applied, arriving again.
      routeToFolder(path, folderKey);
      await flushPromises();

      expect(wrapper.emitted("select-folder").length).toBe(emittedOnce);
      expect(apiGet.mock.calls.length).toBe(fetchedOnce);

      wrapper.unmount();
    });
  });

  // `selectedFolderRouteKey` is the one crossing from the sidebar's row-key
  // space into the route's. It reads `selectedFolderReferenceId`, which is
  // `null` when no reference folder is selected - and `Number(null)` is `0`,
  // which `Number.isFinite` accepts. So it used to answer `rf-0` for "nothing
  // selected", which made the teardown believe a phantom folder was showing,
  // and made the `if-` branch unreachable in exactly the case it exists for.
  it("clears an import-folder selection when the route leaves it", async () => {
    // An import folder is selected with `selectedFolderReferenceId` left null,
    // and `Number(null)` is `0`, which `Number.isFinite` accepts - so the
    // crossing into the route's key space answered `rf-0` for a selection that
    // is really `if-9`. The teardown then compared `rf-0` against the `if-9`
    // it was leaving, found no match, and left the import folder highlighted
    // (and its scanning light on) after the app had navigated away.
    const wrapper = await mountSidebar();
    const sidebarStore = useSidebarStore();
    useViewStore().view = { folderKey: "if-9", folderFilter: null };
    await flushPromises();
    expect(lastFolderPayload(wrapper)).toEqual({
      importSourceFolder: IMPORT_FOLDER.folder,
      importFolderId: 9,
      label: "Inbox",
    });
    expect(sidebarStore.folderScanning).toBe(true);

    useViewStore().view = null;
    await flushPromises();

    expect(sidebarStore.folderScanning).toBe(false);

    wrapper.unmount();
  });

  it("does not read a slash-spelled URL as inside a POSIX folder named a\\b", async () => {
    // The other direction of the same rule. Folding `\` to `/` on a POSIX root
    // makes `/home/me/refs/a/b/2024` and `/home/me/refs/a\b/2024` the same
    // string, so a URL naming a DIFFERENT folder was accepted as a subfolder
    // and then rewritten into this one - the grid would show the wrong folder
    // or nothing. Folding is for Windows roots, where both characters really
    // do separate; here it must fall back to the folder root.
    const wrapper = await mountSidebar();
    routeToFolder("/home/me/refs/a/b/2024", "rf-7");
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 7,
      pathPrefix: ODD_ROOT,
      label: "Odd refs",
    });

    wrapper.unmount();
  });

  it("keeps a backslash that is part of a POSIX folder's name", async () => {
    // The mirror of the Windows case. There `/` in the URL means "separator";
    // here it does not, and a `\` is a legal character in a POSIX folder name.
    // Re-spelling the tail on POSIX would rewrite `c\d` to `c/d`, hand the
    // listing a `file_path_prefix` for a folder that does not exist, and come
    // back with an empty grid - the same failure the re-spelling exists to
    // prevent, caused by the fix for it.
    const wrapper = await mountSidebar();
    routeToFolder(`${ODD_ROOT}/c\\d`, "rf-7");
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 7,
      pathPrefix: `${ODD_ROOT}/c\\d`,
      // And the tail written into the URL keeps that backslash too: on POSIX
      // it is a character in a name, not a separator, at both ends of the trip.
      subPath: "c\\d",
      label: "c\\d",
    });

    wrapper.unmount();
  });

  it("matches a subfolder whose separators disagree with the folder's", async () => {
    // The folder listing and the `?path=` come from the same server and
    // normally agree, but the query half is a URL: it gets shared, edited and
    // pasted. A `/` where the listing said `\` used to read as "not inside
    // this folder" and drop the reader back to the folder root.
    const slashed = `${WIN_ROOT.replace(/\\/g, "/")}/2024/summer`;
    const wrapper = await mountSidebar();
    routeToFolder(slashed, "rf-6");
    await flushPromises();

    // And the payload comes back in the FOLDER's spelling, not the URL's.
    // `file_path_prefix` is a literal LIKE against the stored `file_path`
    // (`utils/query/predicate_filter.py`), so a slash-spelled prefix against a
    // Windows library matches nothing and the grid comes back empty - and the
    // tree row's own key, built from the browse listing, would not match the
    // highlight either.
    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 6,
      pathPrefix: `${WIN_ROOT}\\2024\\summer`,
      subPath: "2024\\summer",
      label: "summer",
    });

    wrapper.unmount();
  });

  // #1206 item 12. The cleanup matched `rf-<id>`, which is the ROOT row's key;
  // a selected subfolder's key is `path-<abs path>`, so removing the folder
  // under it left the highlight and the scanning light on a folder that no
  // longer existed. Matching on the folder the selection belongs to is what the
  // import-folder branch already did.
  it("clears a selected subfolder when its reference folder is removed", async () => {
    const wrapper = await mountSidebar({ teleport: true });
    const sidebarStore = useSidebarStore();
    routeToFolder(SUB);
    await flushPromises();
    expect(sidebarStore.folderScanning).toBe(true);

    vi.spyOn(window, "confirm").mockReturnValue(true);
    const rows = wrapper.findAll(".sidebar-folder-root-row");
    await rows[0].trigger("contextmenu");
    await flushPromises();
    // The context menu is teleported to <body>, so it is not under `wrapper`.
    const remove = document.querySelector(".sidebar-ctx-item--danger");
    expect(remove).not.toBe(null);
    remove.click();
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toBe(null);
    expect(sidebarStore.folderScanning).toBe(false);

    wrapper.unmount();
  });

  it("ignores a path that is not inside this folder", async () => {
    // A `?path=` left over from an "About your library" destination names a
    // folder with no id at all. Applying it under this folder's id would build
    // a grid query for a pair that does not exist.
    const wrapper = await mountSidebar();
    routeToFolder("/home/me/somewhere-else");
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: ROOT,
      label: "Refs",
    });

    wrapper.unmount();
  });
});
