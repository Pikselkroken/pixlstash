// The breadcrumb trail, and the project crumb it withholds rather than guesses.
//
// `projName` used to fall back to `Project ${id}` when the id→name map had no
// entry. That renders a raw id as if it were a label, and for a session that
// was never granted project scope it is worse than useless: `GET /projects`
// 403s such a token, so `projectNames` stays empty for it forever and the
// breadcrumb would permanently assert that project N exists. The whole
// surrounding wave (#708 / #718 / #719 / #721) exists to stop the frontend
// showing project information it was not given.
//
// The name is genuinely absent at first paint even for the owner: the trail
// computes from the route immediately, while names are published by the
// sidebar's `fetchProjects()`, which App.vue only reaches after
// `await fetchConfig()`. Withholding the crumb across that window is the
// intended behaviour: render nothing and fill in, never placeholder and
// correct.
//
// Both directions throughout. With a resolvable name the trail is byte-for-byte
// what it has always been, including the crumb's `to` target, because a
// breadcrumb that quietly stopped linking would be its own regression.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

let currentRoute = { name: "all-pictures", params: {}, query: {} };
const push = vi.fn();

vi.mock("vue-router", () => ({
  useRoute: () => currentRoute,
  useRouter: () => ({ push }),
}));

vi.mock("../utils/apiClient", () => ({
  onSessionReset: () => () => {},
}));

import { useBreadcrumb } from "./useBreadcrumb";
import { useEntityNamesStore } from "../stores/useEntityNamesStore";

/** Set the route and read the resulting trail. */
function trailFor(route) {
  currentRoute = { params: {}, query: {}, ...route };
  const { breadcrumb } = useBreadcrumb();
  return breadcrumb.value;
}

/** Just the visible labels, in order. */
function labels(trail) {
  return trail.map((crumb) => crumb.label);
}

beforeEach(() => {
  setActivePinia(createPinia());
  push.mockReset();
  currentRoute = { name: "all-pictures", params: {}, query: {} };
});

describe("the project crumb when the name is known", () => {
  beforeEach(() => {
    useEntityNamesStore().mergeProjectNames([{ id: 5, name: "Book" }]);
  });

  it("names the project on its own route", () => {
    expect(labels(trailFor({ name: "project", params: { id: "5" } }))).toEqual([
      "Projects",
      "Book",
    ]);
  });

  it("names it as the clickable ancestor of a person", () => {
    useEntityNamesStore().mergeCharacterNames([{ id: 7, name: "Ada" }]);
    const trail = trailFor({
      name: "project-character",
      params: { id: "7", projectId: "5" },
    });
    expect(labels(trail)).toEqual(["Projects", "Book", "Ada"]);
    // The ancestor still navigates by id, which is the whole reason the crumb
    // carries `to` rather than a name.
    expect(trail[1].to).toEqual({ name: "project", params: { id: "5" } });
  });

  it("names it as the clickable ancestor of a set", () => {
    useEntityNamesStore().mergeSetNames([{ id: 9, name: "Portraits" }]);
    const trail = trailFor({
      name: "project-set",
      params: { id: "9", projectId: "5" },
    });
    expect(labels(trail)).toEqual(["Projects", "Book", "Portraits"]);
    expect(trail[1].to).toEqual({ name: "project", params: { id: "5" } });
  });
});

describe("the project crumb when the name is not known", () => {
  it("renders no leaf on the project route, and never the raw id", () => {
    const trail = trailFor({ name: "project", params: { id: "5" } });
    expect(labels(trail)).toEqual(["Projects"]);
    expect(JSON.stringify(trail)).not.toContain("Project 5");
    expect(JSON.stringify(trail)).not.toContain("5");
  });

  it("drops the ancestor but keeps the person", () => {
    // The reduced trail is still correct and still useful: the person's own
    // crumb does not depend on the project's name.
    useEntityNamesStore().mergeCharacterNames([{ id: 7, name: "Ada" }]);
    const trail = trailFor({
      name: "project-character",
      params: { id: "7", projectId: "5" },
    });
    expect(labels(trail)).toEqual(["Projects", "Ada"]);
    expect(JSON.stringify(trail)).not.toContain("Project 5");
  });

  it("drops the ancestor but keeps the set", () => {
    useEntityNamesStore().mergeSetNames([{ id: 9, name: "Portraits" }]);
    const trail = trailFor({
      name: "project-set",
      params: { id: "9", projectId: "5" },
    });
    expect(labels(trail)).toEqual(["Projects", "Portraits"]);
    expect(JSON.stringify(trail)).not.toContain("Project 5");
  });

  it("emits no crumb with a null or undefined label", () => {
    // Filtering must remove the crumb, not leave a blank one that still renders
    // a separator.
    for (const route of [
      { name: "project", params: { id: "5" } },
      { name: "project-character", params: { id: "7", projectId: "5" } },
      { name: "project-set", params: { id: "9", projectId: "5" } },
    ]) {
      for (const crumb of trailFor(route)) {
        expect(crumb).toBeTruthy();
        expect(crumb.label).toBeTypeOf("string");
        expect(crumb.label.length).toBeGreaterThan(0);
      }
    }
  });

  it("resolves the crumb once the name arrives, with no reload", () => {
    // The window closes by itself: the trail is a computed over the names
    // store, so publishing the name fills the crumb in.
    const names = useEntityNamesStore();
    currentRoute = { name: "project", params: { id: "5" }, query: {} };
    const { breadcrumb } = useBreadcrumb();
    expect(labels(breadcrumb.value)).toEqual(["Projects"]);

    names.mergeProjectNames([{ id: 5, name: "Book" }]);
    expect(labels(breadcrumb.value)).toEqual(["Projects", "Book"]);
  });
});

describe("the routes that carry no project crumb are untouched", () => {
  it("keeps the global trails exactly as they were", () => {
    expect(labels(trailFor({ name: "all-pictures" }))).toEqual([
      "Global",
      "All Pictures",
    ]);
    expect(labels(trailFor({ name: "scrapheap" }))).toEqual([
      "Global",
      "Scrapheap",
    ]);
  });

  it("keeps the multi-selection trails, which never named a project", () => {
    const trail = trailFor({
      name: "project-character",
      params: { id: "7", projectId: "5" },
      query: { ids: "7,8" },
    });
    expect(labels(trail)).toEqual(["Projects", "Multiple People"]);
  });

  it("leaves the character and set fallbacks alone", () => {
    // Deliberately NOT changed alongside the project crumb: these are the
    // leaf of the most common routes, and the same treatment would empty the
    // trail on every cold load into a person view. Pinned so that widening the
    // rule is a decision someone makes on purpose.
    expect(labels(trailFor({ name: "character", params: { id: "7" } }))).toEqual(
      ["Global", "Character 7"],
    );
    expect(labels(trailFor({ name: "set", params: { id: "9" } }))).toEqual([
      "Global",
      "Set 9",
    ]);
  });
});
