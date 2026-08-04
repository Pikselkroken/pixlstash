// The sidebar tree's row counts (issue #651).
//
// Every failure this file guards against is SILENT: the tree still renders a
// number, it is just the wrong one. Reading the vault-wide count while the
// sidebar is scoped to a project (or the project-scoped one while it is not)
// looks exactly like a correct render, so each mapping is pinned by name here
// rather than trusted to the ternary that produces it.

import { describe, it, expect } from "vitest";

import {
  PROJECT_VIEW_MODE,
  characterCountUpdates,
  projectCountUpdates,
} from "./sidebarCounts";

/** A character row as `GET /characters?include_counts=true` returns it. */
function characterRow(id, { imageCount, projectImageCount } = {}) {
  return {
    id,
    name: `Character ${id}`,
    image_count: imageCount,
    project_image_count: projectImageCount,
  };
}

describe("characterCountUpdates", () => {
  // The two halves of the same mistake. Both wrong answers are present on the
  // row, so a swapped field is invisible without asserting the NUMBER.
  const rows = [characterRow(1, { imageCount: 40, projectImageCount: 7 })];

  it("shows the project-scoped count in project view mode", () => {
    expect(characterCountUpdates(rows, PROJECT_VIEW_MODE)).toEqual([
      { id: 1, count: 7 },
    ]);
  });

  it("shows the vault-wide count in library view mode", () => {
    expect(characterCountUpdates(rows, "library")).toEqual([
      { id: 1, count: 40 },
    ]);
  });

  it("treats any non-project mode as the vault-wide count", () => {
    for (const mode of ["library", "global", "", null, undefined]) {
      expect(characterCountUpdates(rows, mode)).toEqual([{ id: 1, count: 40 }]);
    }
  });

  it("does not confuse the mode name with a project id", () => {
    // `projectViewMode` is the literal "project", never a project's id.
    expect(PROJECT_VIEW_MODE).toBe("project");
    expect(characterCountUpdates(rows, 3)).toEqual([{ id: 1, count: 40 }]);
  });

  it("skips a row whose count is missing rather than blanking it", () => {
    // An older backend sends no count fields at all; a list read made without
    // `include_counts` sends explicit nulls. Neither is "this person has zero
    // pictures", so neither may produce a write.
    const older = [{ id: 1, name: "Ada" }];
    const withoutCounts = [
      characterRow(2, { imageCount: null, projectImageCount: null }),
    ];
    expect(characterCountUpdates(older, "library")).toEqual([]);
    expect(characterCountUpdates(older, PROJECT_VIEW_MODE)).toEqual([]);
    expect(characterCountUpdates(withoutCounts, "library")).toEqual([]);
    expect(characterCountUpdates(withoutCounts, PROJECT_VIEW_MODE)).toEqual([]);
  });

  it("skips only the scope that is missing, not the whole row", () => {
    // A character in no project still has a vault-wide count.
    const rows = [characterRow(1, { imageCount: 12, projectImageCount: null })];
    expect(characterCountUpdates(rows, "library")).toEqual([
      { id: 1, count: 12 },
    ]);
    expect(characterCountUpdates(rows, PROJECT_VIEW_MODE)).toEqual([]);
  });

  it("writes a real zero", () => {
    // A person with no pictures in this project must read 0, not keep the
    // previous project's number. This is why the guard is `== null`, not falsy.
    const rows = [characterRow(1, { imageCount: 0, projectImageCount: 0 })];
    expect(characterCountUpdates(rows, PROJECT_VIEW_MODE)).toEqual([
      { id: 1, count: 0 },
    ]);
    expect(characterCountUpdates(rows, "library")).toEqual([
      { id: 1, count: 0 },
    ]);
  });

  it("keeps list order and covers every counted row", () => {
    const rows = [
      characterRow(3, { imageCount: 1, projectImageCount: 1 }),
      characterRow(1, { imageCount: 2, projectImageCount: 2 }),
      characterRow(2, { imageCount: 3, projectImageCount: 3 }),
    ];
    expect(characterCountUpdates(rows, "library").map((u) => u.id)).toEqual([
      3, 1, 2,
    ]);
  });

  it("survives a list that never arrived", () => {
    // `refresh()` answers `[]` for an unknown kind and for a declined scoped
    // read; a failed read can leave the caller holding nothing at all.
    expect(characterCountUpdates([], "library")).toEqual([]);
    expect(characterCountUpdates(undefined, "library")).toEqual([]);
    expect(characterCountUpdates(null, PROJECT_VIEW_MODE)).toEqual([]);
  });
});

describe("projectCountUpdates", () => {
  it("reads each project's own image count", () => {
    const rows = [
      { id: 3, name: "Book", image_count: 118 },
      { id: 4, name: "Cards", image_count: 0 },
    ];
    expect(projectCountUpdates(rows)).toEqual([
      { id: 3, count: 118 },
      { id: 4, count: 0 },
    ]);
  });

  it("skips a project with no count rather than blanking it", () => {
    const rows = [{ id: 3, name: "Book", image_count: null }, { id: 4 }];
    expect(projectCountUpdates(rows)).toEqual([]);
  });

  it("ignores the character-only scope field", () => {
    // Only characters carry `project_image_count`; a project row must never be
    // read through it.
    const rows = [{ id: 3, image_count: 5, project_image_count: 99 }];
    expect(projectCountUpdates(rows)).toEqual([{ id: 3, count: 5 }]);
  });

  it("survives a list that never arrived", () => {
    expect(projectCountUpdates([])).toEqual([]);
    expect(projectCountUpdates(undefined)).toEqual([]);
  });
});
