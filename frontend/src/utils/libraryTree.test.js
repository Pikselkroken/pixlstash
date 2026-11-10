import { describe, expect, it } from "vitest";
import { findMatch, folderTreeRows, inProjectBranch } from "./libraryTree.js";

const projects = [
  { id: 1, name: "Portraits" },
  { id: 2, name: "Lookbook" },
];
const characters = [
  { id: 10, name: "Courier", project_ids: [1, 2] },
  { id: 11, name: "Mira", project_ids: [] },
];
const sets = [
  { id: 20, name: "Golden hour", project_ids: [1] },
  { id: 21, name: "Courier LoRA", project_ids: [1], parent_character_id: 10 },
  { id: 22, name: "Loose", project_ids: [] },
];
const allOpen = () => true;
const keys = (rows) => rows.map((r) => r.key);

describe("inProjectBranch", () => {
  it("reads null as in no project at all", () => {
    expect(inProjectBranch(characters[1], null)).toBe(true);
    expect(inProjectBranch(characters[0], null)).toBe(false);
    expect(inProjectBranch(characters[0], 2)).toBe(true);
  });
});

describe("findMatch", () => {
  it("splits round the first case-insensitive hit", () => {
    expect(findMatch("The Courier", "cour")).toEqual({
      before: "The ",
      match: "Cour",
      after: "ier",
    });
    expect(findMatch("Mira", "x")).toBeNull();
    expect(findMatch("Mira", "  ")).toBeNull();
  });
});

// What GET /library/folders answers for: Portraits/Courier/Golden hour (a set
// filed inside a person's folder), Portraits/2024 (the owner's own),
// Global/Mira, Unassigned, and a set folder whose set is not listed here.
const folders = [
  { path: "Global", name: "Global", kind: "global", entity_id: null, project_id: null, count: 2 },
  { path: "Global/Mira", name: "Mira", kind: "character", entity_id: 11, project_id: null, count: 2 },
  { path: "Portraits", name: "Portraits", kind: "project", entity_id: 1, project_id: 1, count: 9 },
  { path: "Portraits/2024", name: "2024", kind: "folder", entity_id: null, project_id: 1, count: 1 },
  { path: "Portraits/Courier", name: "Courier", kind: "character", entity_id: 10, project_id: 1, count: 7 },
  { path: "Portraits/Courier/Golden hour", name: "Golden hour", kind: "set", entity_id: 20, project_id: 1, count: 3 },
  { path: "Portraits/Refs", name: "Refs", kind: "set", entity_id: 99, project_id: 1, count: 1 },
  { path: "Unassigned", name: "Unassigned", kind: "unassigned", entity_id: null, project_id: null, count: 4 },
];
const tree = (extra) =>
  folderTreeRows({ folders, projects, characters, sets, isOpen: allOpen, ...extra });

describe("folderTreeRows", () => {
  it("nests folders as filed: projects, then Global and Unassigned last", () => {
    expect(keys(tree())).toEqual([
      "f:Portraits",
      "f:Portraits/Courier",
      "f:Portraits/Courier/Golden hour",
      "f:Portraits/2024",
      "f:Portraits/Refs",
      "f:Global",
      "f:Global/Mira",
      "f:Unassigned",
    ]);
    const rows = tree();
    const golden = rows.find((r) => r.key === "f:Portraits/Courier/Golden hour");
    expect(golden).toMatchObject({ kind: "set", depth: 2, projectId: 1, count: 3 });
    // Courier has siblings below, so its guide runs on past Golden hour.
    expect(golden.rails).toEqual([1]);
    expect(golden.entity.id).toBe(20);
    // A set this list does not hold is a plain folder, not a broken set row.
    expect(rows.find((r) => r.key === "f:Portraits/Refs")).toMatchObject({
      kind: "folder",
      entity: null,
    });
  });

  it("draws a closed folder without its children", () => {
    expect(keys(tree({ isOpen: (key) => key === "f:Portraits" }))).toEqual([
      "f:Portraits",
      "f:Portraits/Courier",
      "f:Portraits/2024",
      "f:Portraits/Refs",
      "f:Global",
      "f:Unassigned",
    ]);
  });

  it("finds through closed folders, dims the context and drops what does not match", () => {
    const rows = tree({ isOpen: () => false, query: "gold" });
    expect(keys(rows)).toEqual([
      "f:Portraits",
      "f:Portraits/Courier",
      "f:Portraits/Courier/Golden hour",
    ]);
    expect(rows[0]).toMatchObject({ open: true, dim: true, match: null });
    expect(rows[2].match.match).toBe("Gold");
  });

  it("matches a folder by name and never Global or Unassigned", () => {
    const rows = tree({ isOpen: () => false, query: "portr" });
    expect(keys(rows)).toEqual(["f:Portraits"]);
    expect(rows[0]).toMatchObject({ open: false, dim: false });
    expect(tree({ query: "global" })).toEqual([]);
    expect(tree({ query: "unassigned" })).toEqual([]);
  });
});
