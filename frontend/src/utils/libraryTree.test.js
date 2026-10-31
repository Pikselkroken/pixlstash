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

describe("folderTreeRows", () => {
  it("files people and every set directly under each project, then Global and Unassigned", () => {
    const rows = folderTreeRows({ projects, characters, sets, isOpen: allOpen });
    expect(keys(rows)).toEqual([
      "f:p:1",
      "f:p:1/c:10",
      "f:p:1/s:20",
      "f:p:1/s:21",
      "f:p:2",
      "f:p:2/c:10",
      "f:global",
      "f:global/c:11",
      "f:global/s:22",
      "f:unassigned",
    ]);
    expect(rows.find((r) => r.key === "f:p:1/c:10").projectId).toBe(1);
    expect(rows.find((r) => r.key === "f:unassigned").childCount).toBe(0);
  });

  it("draws a closed folder without its children", () => {
    const rows = folderTreeRows({
      projects,
      characters,
      sets,
      isOpen: (key) => key === "f:p:2",
    });
    expect(keys(rows)).toEqual([
      "f:p:1",
      "f:p:2",
      "f:p:2/c:10",
      "f:global",
      "f:unassigned",
    ]);
  });

  it("keeps a project share token's tree to its project", () => {
    const rows = folderTreeRows({
      projects: [projects[0]],
      characters,
      sets,
      isOpen: () => false,
      withGlobal: false,
    });
    expect(keys(rows)).toEqual(["f:p:1"]);
  });

  it("finds through closed folders, dims the context and drops what does not match", () => {
    const rows = folderTreeRows({
      projects,
      characters,
      sets,
      isOpen: () => false,
      query: "cour",
    });
    expect(keys(rows)).toEqual([
      "f:p:1",
      "f:p:1/c:10",
      "f:p:1/s:21",
      "f:p:2",
      "f:p:2/c:10",
    ]);
    expect(rows[0]).toMatchObject({ open: true, dim: true, match: null });
    expect(rows[1].match.match).toBe("Cour");
  });

  it("matches a project by name and never Global or Unassigned", () => {
    const rows = folderTreeRows({
      projects,
      characters,
      sets,
      isOpen: () => false,
      query: "look",
    });
    expect(keys(rows)).toEqual(["f:p:2"]);
    expect(rows[0]).toMatchObject({ open: false, dim: false });
    expect(
      folderTreeRows({ projects, characters, sets, isOpen: allOpen, query: "global" }),
    ).toEqual([]);
  });
});
