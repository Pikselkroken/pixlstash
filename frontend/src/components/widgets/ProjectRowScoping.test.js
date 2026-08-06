// The Project row in the two picture menus, under a session that was granted
// no project scope.
//
// A share token scoped to a character, a picture or a set is 403'd outright by
// `GET /projects` (`routes/projects.py` rejects on `resource_type` before it
// reads anything), and by `POST /projects/membership` with it. The Project row
// was rendered unconditionally, so such a session got a control that opened a
// flyout reading "No projects found", logged a warning from the membership
// read, and left every row permanently inert. Absent project information must
// be OMITTED, not shown as an empty menu.
//
// Both menus are covered because they are separate components with separate
// templates: a rule applied to one and forgotten in the other is exactly the
// shape of bug this file exists to catch (the same reason
// `KeepCoverOnlyMenus.test.js` is written this way).
//
// Both directions are asserted. Over-blocking is its own regression, so the
// owner, an unscoped read-only token and a project-scoped token must all keep
// the row, and the sibling Set / Person controls must survive in every case.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false),
    sessionContext: ref(null),
    onSessionReset: () => () => {},
  };
});

import { isReadOnly, sessionContext } from "../../utils/apiClient";
import ImageGridContextMenu from "./ImageGridContextMenu.vue";
import SelectionMenu from "../panels/SelectionMenu.vue";

// Stubbed, but with its props preserved so the test can ask WHICH entity
// controls were rendered rather than counting anonymous nodes.
const AddToEntityControlStub = {
  name: "AddToEntityControl",
  props: ["type"],
  template: '<div class="ate-stub" :data-type="type" />',
};

const globalStubs = {
  global: {
    stubs: {
      "v-icon": true,
      teleport: true,
      AddToEntityControl: AddToEntityControlStub,
    },
  },
};

function mountContextMenu(props = {}) {
  return mount(ImageGridContextMenu, {
    props: {
      allPicturesId: "ALL",
      unassignedPicturesId: "UNASSIGNED",
      scrapheapPicturesId: "SCRAPHEAP",
      backendUrl: "http://x",
      visible: true,
      selectedCharacter: "ALL",
      selectedImageIds: ["10", "11", "12"],
      ...props,
    },
    ...globalStubs,
  });
}

function mountSelectionMenu(props = {}) {
  return mount(SelectionMenu, {
    props: {
      open: true,
      backendUrl: "http://x",
      isReadOnly: isReadOnly.value,
      isScrapheapView: false,
      selectedImageIds: ["10", "11", "12"],
      selectedCount: 3,
      ...props,
    },
    ...globalStubs,
  });
}

/** Which entity controls the menu actually rendered, e.g. ["project", "set"]. */
function entityControlTypes(wrapper) {
  return wrapper.findAll(".ate-stub").map((el) => el.attributes("data-type"));
}

const MENUS = [
  ["ImageGridContextMenu", mountContextMenu],
  ["SelectionMenu", mountSelectionMenu],
];

/** Put the session into a share token scoped to one non-project resource. */
function scopeSessionTo(resourceType) {
  isReadOnly.value = true;
  sessionContext.value = { scope: "READ", resource_type: resourceType };
}

let consoleError;
let consoleWarn;

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  sessionContext.value = null;
  // Not silenced-and-forgotten: a template dereferencing something the server
  // stopped sending shows up here first, so the absence of output is itself an
  // assertion below.
  consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe.each(MENUS)("%s: the Project row without project scope", (_n, mountMenu) => {
  it("omits the Project row for a character-scoped token", () => {
    scopeSessionTo("character");
    expect(entityControlTypes(mountMenu())).not.toContain("project");
  });

  it("omits it for a set-scoped and a picture-scoped token too", () => {
    for (const resourceType of ["picture_set", "picture"]) {
      scopeSessionTo(resourceType);
      expect(
        entityControlTypes(mountMenu()),
        `a ${resourceType}-scoped token was still offered the Project row`,
      ).not.toContain("project");
    }
  });

  it("keeps the sibling Set and Person rows", () => {
    // The fix must remove exactly one row. A scoped session still assigns sets
    // and people; taking those away would be over-blocking.
    scopeSessionTo("character");
    const types = entityControlTypes(mountMenu());
    expect(types).toContain("set");
    expect(types).toContain("character");
  });

  it("renders without a console error or warning", () => {
    scopeSessionTo("character");
    mountMenu();
    expect(consoleError).not.toHaveBeenCalled();
    expect(consoleWarn).not.toHaveBeenCalled();
  });
});

describe.each(MENUS)("%s: the Project row with project scope", (_n, mountMenu) => {
  it("renders it for the owner, unchanged", () => {
    const types = entityControlTypes(mountMenu());
    expect(types).toContain("project");
    // The owner's menu is otherwise untouched: still all three controls, still
    // in the shipped order.
    expect(types).toEqual(["project", "character", "set"]);
  });

  it("renders it for an owner session that reported its context", () => {
    sessionContext.value = { scope: "ALL", resource_type: null };
    expect(entityControlTypes(mountMenu())).toContain("project");
  });

  it("renders it for an unscoped read-only token", () => {
    // A whole-vault READ token can list every project, so it keeps the row even
    // though every entry inside is inert.
    isReadOnly.value = true;
    sessionContext.value = { scope: "READ", resource_type: null };
    expect(entityControlTypes(mountMenu())).toContain("project");
  });

  it("renders it for a project-scoped token", () => {
    isReadOnly.value = true;
    sessionContext.value = { scope: "READ", resource_type: "project" };
    expect(entityControlTypes(mountMenu())).toContain("project");
  });

  it("renders without a console error or warning", () => {
    mountMenu();
    expect(consoleError).not.toHaveBeenCalled();
    expect(consoleWarn).not.toHaveBeenCalled();
  });
});
