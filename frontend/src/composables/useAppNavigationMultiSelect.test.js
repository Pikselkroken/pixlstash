// The other half of the sidebar's "select all people": the payload has to reach
// the URL, because the route is the single source of truth for what the grid
// shows (§2 of the frontend architecture).
//
// `pushRouteForCurrentSelection` only attaches `?ids=…&mode=…` when
// `selectedCharacter` names a real person, so a multi-selection whose primary id
// is ALL_PICTURES_ID pushes `all-pictures` and silently loses every id. That is
// exactly what an intermediate `select-set: null` emit caused, and it is the
// failure this file exists to catch: a sidebar-only test still passes while the
// feature does nothing.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";

const push = vi.fn();

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: "all-pictures" }),
  useRouter: () => ({
    push: (...args) => {
      push(...args);
      return Promise.resolve();
    },
    replace: vi.fn(),
    currentRoute: ref({ query: {} }),
  }),
}));

import { useAppNavigation } from "./useAppNavigation";
import { useSelectionStore } from "../stores/useSelectionStore";
import { ALL_PICTURES_ID } from "../stores/useViewStore";

const SELECT_ALL_PAYLOAD = {
  id: 7,
  label: null,
  ids: [7, 9],
  projectIds: { 7: null, 9: null },
  multiMode: "union",
};

beforeEach(() => {
  setActivePinia(createPinia());
  push.mockReset();
});

describe("handleSelectCharacter — a multi-selection from the sidebar", () => {
  it("puts every id and the mode on the route", async () => {
    const selection = useSelectionStore();
    // The landing view: this is where the primary-id mistake was invisible,
    // because `all-pictures` → `all-pictures` is a swallowed duplicate push.
    selection.selectedCharacter = ALL_PICTURES_ID;
    selection.selectedCharacterIds = [];
    const nav = useAppNavigation();

    await nav.handleSelectCharacter(SELECT_ALL_PAYLOAD);

    expect(push).toHaveBeenCalledWith({
      name: "character",
      params: { id: "7" },
      query: { ids: "7,9", mode: "union" },
    });
    expect(selection.selectedCharacterIds).toEqual([7, 9]);
  });

  it("lets the payload's mode override a remembered intersection", async () => {
    // `characterMultiMode` is sessionStorage-backed, so a user who last used
    // Overlap would otherwise get "all N people, intersected" — empty by
    // construction — from a key called "select all".
    const selection = useSelectionStore();
    selection.setCharacterMultiMode("intersection");
    selection.selectedCharacter = ALL_PICTURES_ID;
    const nav = useAppNavigation();

    await nav.handleSelectCharacter(SELECT_ALL_PAYLOAD);

    expect(selection.characterMultiMode).toBe("union");
    expect(push.mock.calls[0][0].query.mode).toBe("union");
  });

  it("keeps the remembered mode when the gesture states none (ctrl-click)", async () => {
    const selection = useSelectionStore();
    selection.setCharacterMultiMode("intersection");
    selection.selectedCharacter = 7;
    const nav = useAppNavigation();

    await nav.handleSelectCharacter({ ...SELECT_ALL_PAYLOAD, multiMode: null });

    expect(selection.characterMultiMode).toBe("intersection");
    expect(push.mock.calls[0][0].query.mode).toBe("intersection");
  });
});
