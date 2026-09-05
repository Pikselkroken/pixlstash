// The create-and-assign seam (#645), end to end across the shape boundary.
//
// This exists because the feature shipped broken and a green suite hid it. The
// backend answers BOTH `POST /characters` and `PATCH /characters/{id}` with
// `CharacterMutationResponse` = `{status, character}` (pixlstash/routes/
// characters.py: POST ~1240, PATCH ~589), so the record is NESTED. Every host
// read `savedCharacter.id` off the envelope, got undefined, hit its own guard,
// and showed "couldn't be assigned" while the person had in fact been created.
// The old tests mocked `createCharacter` as resolving to a FLAT `{id, name}`,
// so they encoded the assumption instead of the contract and proved nothing.
//
// So: the endpoint mock here returns the REAL envelope, the REAL CharacterEditor
// performs the save, and the payload it emits is fed to each host's handler.
// The payload under test is therefore produced by the component, never authored
// by the test. Both flows assert the assignment call AND that the success notice
// fires and the failure notice does not, which is the assertion whose absence
// let this ship.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import * as vuetifyComponents from "vuetify/components";
import * as vuetifyDirectives from "vuetify/directives";

// The exact shape the routes return.
const CREATED_ENVELOPE = {
  status: "success",
  character: { id: 99, name: "Alice", project_id: null },
};

// The factory is hoisted above the const above, so it declares its own copy
// rather than closing over it; beforeEach re-establishes both from CREATED_ENVELOPE.
vi.mock("../../api/characters", () => ({
  createCharacter: vi.fn(),
  patchCharacter: vi.fn(),
  getReferencePictures: vi
    .fn()
    .mockResolvedValue({ reference_picture_ids: [] }),
  addCharacterFaces: vi.fn().mockResolvedValue({ status: "success" }),
  addCharacterFacesByFaceId: vi.fn().mockResolvedValue({ status: "success" }),
}));
vi.mock("../../api/pictures", () => ({
  listPicturesByIds: vi.fn().mockResolvedValue([]),
}));
vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  appendShareToken: (u) => u,
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import {
  createCharacter,
  patchCharacter,
  addCharacterFaces,
  addCharacterFacesByFaceId,
} from "../../api/characters";
import { chooseCharacterAssignment } from "../../utils/characterCreateFlow.js";
import CharacterEditor from "./CharacterEditor.vue";

const vuetify = createVuetify({
  components: vuetifyComponents,
  directives: vuetifyDirectives,
});

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  createCharacter.mockResolvedValue(CREATED_ENVELOPE);
  patchCharacter.mockResolvedValue({
    status: "success",
    character: { id: 99, name: "Alice renamed" },
  });
  addCharacterFaces.mockResolvedValue({ status: "success" });
  addCharacterFacesByFaceId.mockResolvedValue({ status: "success" });
});

/**
 * Drive the real editor through a real save and return what it emitted.
 * @param {Object} [character] - the pre-filled record the dialog opens on.
 * @returns {Promise<{wrapper: Object, payload: any, emitted: boolean}>}
 */
async function saveThroughEditor(character = { id: null, name: "Alice" }) {
  const wrapper = mount(CharacterEditor, {
    props: { open: true, character, backendUrl: "http://x", projects: [] },
    global: { plugins: [vuetify] },
  });
  await flushPromises();
  await wrapper.vm.save();
  await flushPromises();
  const events = wrapper.emitted("saved");
  return {
    wrapper,
    emitted: Boolean(events),
    payload: events ? events[0][0] : undefined,
  };
}

describe("CharacterEditor unwraps the mutation envelope", () => {
  it("emits the RECORD, not the {status, character} envelope, on create", async () => {
    const { payload } = await saveThroughEditor();
    expect(createCharacter).toHaveBeenCalled();
    // The bug in one assertion: this was the envelope, so `.id` was undefined.
    expect(payload).toEqual(CREATED_ENVELOPE.character);
    expect(payload.id).toBe(99);
    expect(payload.status).toBeUndefined();
    expect(payload.character).toBeUndefined();
  });

  it("unwraps the same envelope on patch", async () => {
    const { payload } = await saveThroughEditor({ id: 99, name: "Alice" });
    expect(patchCharacter).toHaveBeenCalled();
    expect(payload).toEqual({ id: 99, name: "Alice renamed" });
    expect(payload.id).toBe(99);
  });

  it("withholds `saved` and reports when the response carries no record", async () => {
    // Not a masking fallback: an envelope without a record is a real error, so
    // nothing is emitted and the console/notice name the shape problem.
    createCharacter.mockResolvedValue({ status: "success", character: null });
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { emitted } = await saveThroughEditor();
    expect(emitted).toBe(false);
    expect(spy).toHaveBeenCalled();
    expect(String(spy.mock.calls[0][0])).toContain("CharacterMutationResponse");
    spy.mockRestore();
  });
});

describe("CharacterEditor - multi-project membership", () => {
  it("loads every project and PATCHes the complete edited selection", async () => {
    const wrapper = mount(CharacterEditor, {
      props: {
        open: true,
        character: {
          id: 99,
          name: "Alice",
          project_id: 1,
          project_ids: [1, 2],
        },
        backendUrl: "http://x",
        projects: [
          { id: 1, name: "One" },
          { id: 2, name: "Two" },
          { id: 3, name: "Three" },
        ],
      },
      global: {
        plugins: [vuetify],
        stubs: {
          AppDialog: {
            props: ["open", "title", "width"],
            template: "<div><slot /><slot name='footer' /></div>",
          },
        },
      },
    });
    await flushPromises();

    const projectCheckboxes = wrapper.findAll(
      ".app-select__multiple-option input",
    );
    expect(projectCheckboxes.map((input) => input.element.checked)).toEqual([
      true,
      true,
      false,
    ]);

    await projectCheckboxes[0].setValue(false);
    await projectCheckboxes[2].setValue(true);
    await wrapper.vm.save();
    await flushPromises();

    expect(patchCharacter).toHaveBeenCalledTimes(1);
    expect(patchCharacter.mock.calls[0][1]).toMatchObject({
      id: 99,
      project_ids: [2, 3],
    });
    expect(patchCharacter.mock.calls[0][1]).not.toHaveProperty("project_id");
  });
});

// ── The two host handlers, driven by the payload the editor really emits ─────

/** Reproduces ImageGrid.handleCreatePersonSaved's assignment branch. */
async function gridHandleSaved(savedCharacter, pending, notices) {
  const characterId = savedCharacter?.id;
  const name = savedCharacter?.name || "person";
  if (characterId == null) {
    notices.error(`Created ${name}, but the selection couldn't be assigned.`);
    return;
  }
  if (pending.mode === "faces") {
    await addCharacterFacesByFaceId(characterId, pending.ids, {});
  } else {
    await addCharacterFaces(characterId, pending.ids, {});
  }
  const n = pending.ids.length;
  const unit = pending.mode === "faces" ? "face" : "picture";
  notices.success(
    `Created ${name}, assigned ${n} ${unit}${n === 1 ? "" : "s"}.`,
  );
}

/** Reproduces ImageOverlay.handleCreatePersonSaved's assignment branch. */
async function overlayHandleSaved(savedCharacter, faceId, notices) {
  const characterId = savedCharacter?.id;
  const name = savedCharacter?.name || "person";
  if (characterId == null || faceId == null) {
    notices.error(`Created ${name}, but the face couldn't be assigned.`);
    return;
  }
  await addCharacterFacesByFaceId(characterId, [faceId], {});
  notices.success(`Created ${name}, assigned to this face.`);
}

function noticeSpies() {
  return { success: vi.fn(), error: vi.fn() };
}

describe("grid flow: the selection is actually assigned", () => {
  it("assigns the picture selection to the new person and reports success", async () => {
    const { payload } = await saveThroughEditor();
    const pending = chooseCharacterAssignment({
      pictureIds: ["10", "11"],
      faceEntries: [],
    });
    const notices = noticeSpies();

    await gridHandleSaved(payload, pending, notices);

    expect(addCharacterFaces).toHaveBeenCalledWith(99, ["10", "11"], {});
    expect(addCharacterFacesByFaceId).not.toHaveBeenCalled();
    expect(notices.success).toHaveBeenCalledWith(
      "Created Alice, assigned 2 pictures.",
    );
    // The assertion whose absence let this ship.
    expect(notices.error).not.toHaveBeenCalled();
  });

  it("assigns by face id when faces were selected", async () => {
    const { payload } = await saveThroughEditor();
    const pending = chooseCharacterAssignment({
      pictureIds: ["10"],
      faceEntries: [{ imageId: "10", faceIdx: 0, faceId: 7 }],
    });
    const notices = noticeSpies();

    await gridHandleSaved(payload, pending, notices);

    expect(addCharacterFacesByFaceId).toHaveBeenCalledWith(99, [7], {});
    expect(addCharacterFaces).not.toHaveBeenCalled();
    expect(notices.success).toHaveBeenCalledWith(
      "Created Alice, assigned 1 face.",
    );
    expect(notices.error).not.toHaveBeenCalled();
  });
});

describe("overlay flow: the face is actually assigned", () => {
  it("assigns the captured face to the new person and reports success", async () => {
    const { payload } = await saveThroughEditor();
    const notices = noticeSpies();

    await overlayHandleSaved(payload, 4, notices);

    expect(addCharacterFacesByFaceId).toHaveBeenCalledWith(99, [4], {});
    expect(notices.success).toHaveBeenCalledWith(
      "Created Alice, assigned to this face.",
    );
    expect(notices.error).not.toHaveBeenCalled();
  });

  it("would have failed against the raw envelope, which is the shipped bug", async () => {
    // Characterises what the hosts used to receive. Kept so the regression is
    // described, not merely prevented.
    const notices = noticeSpies();
    await overlayHandleSaved(CREATED_ENVELOPE, 4, notices);
    expect(addCharacterFacesByFaceId).not.toHaveBeenCalled();
    expect(notices.error).toHaveBeenCalled();
  });
});

// The adapter tray is wired here, not tested here - `AdapterTray.test.js` owns
// its behaviour. What only this file can prove is the WIRING: that the editor
// mounts it at all, that it hands it THIS person, and that an unsaved person
// gets no tray. All three are invisible to the tray's own suite, and a tray
// given the wrong entity type would render a picture set's adapters under a
// person's name.
const AdapterTrayStub = {
  name: "AdapterTray",
  props: ["entityType", "entityId"],
  template: `<div class="adapter-tray-stub" :data-type="entityType" :data-id="entityId"></div>`,
};

describe("CharacterEditor - adapter tray", () => {
  function mountWithTray(props) {
    return mount(CharacterEditor, {
      props: { backendUrl: "http://x", projects: [], ...props },
      global: {
        plugins: [vuetify],
        stubs: {
          // The real AppDialog teleports its body out of the wrapper, so the
          // tray would be unfindable for reasons that have nothing to do with
          // the wiring under test.
          AppDialog: {
            props: ["open", "title", "width"],
            template: "<div><slot /><slot name='footer' /></div>",
          },
          AdapterTray: AdapterTrayStub,
        },
      },
    });
  }

  it("points the tray at this person", async () => {
    const wrapper = mountWithTray({
      open: true,
      character: { id: 42, name: "Alice" },
    });
    await flushPromises();
    const tray = wrapper.find(".adapter-tray-stub");
    expect(tray.exists()).toBe(true);
    expect(tray.attributes("data-type")).toBe("character");
    expect(tray.attributes("data-id")).toBe("42");
  });

  it("gives an unsaved person no id to look up", async () => {
    const wrapper = mountWithTray({
      open: true,
      character: { id: null, name: "" },
    });
    await flushPromises();
    // Mounted, but with nothing to read - the tray renders nothing at all for a
    // null id, which is what keeps a create dialog from carrying a section that
    // can only ever say "none".
    expect(wrapper.find(".adapter-tray-stub").attributes("data-id")).toBe(
      undefined,
    );
  });

  it("does not mount it while the dialog is closed", async () => {
    const wrapper = mountWithTray({
      open: false,
      character: { id: 42, name: "Alice" },
    });
    await flushPromises();
    expect(wrapper.find(".adapter-tray-stub").exists()).toBe(false);
  });
});
