// The accessible name of the library-switch overlay (#1016).
//
// LibrarySwitchOverlay.test.js mocks `vuetify/components` away, which is fine
// for the behaviour it covers but useless here: the whole question is which
// element Vuetify's real VDialog puts `role`/`aria-modal` on and whether our
// naming attributes land on that same element. `vi.mock` is file-scoped, so
// this check needs its own file with the real components mounted.
//
// Escape is deliberately suppressed on this dialog, so an unnamed one strands
// a screen-reader user in a modal that also refuses the standard way out.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createVuetify } from "vuetify";
import * as vuetifyComponents from "vuetify/components";
import * as vuetifyDirectives from "vuetify/directives";
import { nextTick } from "vue";

vi.mock("../../api/libraries", () => ({ setActiveLibrary: vi.fn() }));
vi.mock("../../utils/reloadPage", () => ({ reloadPage: vi.fn() }));

import LibrarySwitchOverlay from "./LibrarySwitchOverlay.vue";
import { setActiveLibrary } from "../../api/libraries";
import { useLibrarySwitchStore } from "../../stores/useLibrariesStore";

const vuetify = createVuetify({
  components: vuetifyComponents,
  directives: vuetifyDirectives,
});

let pinia;

beforeEach(() => {
  vi.clearAllMocks();
  pinia = createPinia();
  setActivePinia(pinia);
});

/** Resolve an element's name/description the way an AT would: join the text of
 * every id in the IDREF list, in order. */
function fromIdRefs(element, attribute) {
  const refs = element.getAttribute(attribute);
  expect(refs, `${attribute} is missing`).toBeTruthy();
  return refs
    .split(/\s+/)
    .map((id) => {
      const target = document.getElementById(id);
      expect(target, `${attribute} points at missing id "${id}"`).toBeTruthy();
      return target.textContent.trim().replace(/\s+/g, " ");
    })
    .join(" ");
}

function findDialog() {
  const dialogs = document.querySelectorAll('[role="alertdialog"]');
  expect(dialogs).toHaveLength(1);
  // The authoritative role is Vuetify's, overridden in place. A second nested
  // dialog element would announce as a dialog inside a dialog.
  expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(0);
  return dialogs[0];
}

describe("LibrarySwitchOverlay accessible name", () => {
  it("names and describes the switching phase", async () => {
    setActiveLibrary.mockReturnValue(new Promise(() => {}));
    const wrapper = mount(LibrarySwitchOverlay, {
      attachTo: document.body,
      global: { plugins: [pinia, vuetify] },
    });
    const store = useLibrarySwitchStore();
    store.begin({ uuid: "b", name: "Client work" }, { uuid: "a", name: "Family Photos" }, null);
    await nextTick();
    await nextTick();

    const dialog = findDialog();
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(fromIdRefs(dialog, "aria-labelledby")).toBe(
      "Switching to Client work…",
    );
    expect(fromIdRefs(dialog, "aria-describedby")).toContain(
      "PixlStash is finishing or cancelling work",
    );

    wrapper.unmount();
  });

  it("names the failed phase and describes it with the error", async () => {
    setActiveLibrary.mockRejectedValue({
      response: { data: { detail: "Drive went away" } },
    });
    const wrapper = mount(LibrarySwitchOverlay, {
      attachTo: document.body,
      global: { plugins: [pinia, vuetify] },
    });
    const store = useLibrarySwitchStore();

    await store.begin(
      { uuid: "b", name: "Client work" },
      { uuid: "a", name: "Family Photos" },
      null,
    );
    await nextTick();

    const dialog = findDialog();
    expect(fromIdRefs(dialog, "aria-labelledby")).toBe(
      "Could not switch to Client work",
    );
    const description = fromIdRefs(dialog, "aria-describedby");
    expect(description).toContain("PixlStash is still using Family Photos");
    expect(description).toContain("Drive went away");

    wrapper.unmount();
  });
});
