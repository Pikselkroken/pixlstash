import { describe, it, expect } from "vitest";

import {
  describeSegment,
  formatLayout,
  layoutExamples,
  parseLayout,
} from "./libraryLayout";

describe("parseLayout / formatLayout", () => {
  it("round-trips the grammar the API uses", () => {
    expect(parseLayout("project/person,set")).toEqual([
      ["project"],
      ["person", "set"],
    ]);
    expect(formatLayout([["project"], ["person", "set"]])).toBe(
      "project/person,set",
    );
  });

  it("spells no layout as null on the way out, whatever came in", () => {
    // `null` is what the PATCH means by "turn it off"; `""` is a value the
    // server would have to guess about, so it must never be produced here.
    for (const empty of [null, undefined, ""]) {
      expect(parseLayout(empty)).toEqual([]);
    }
    expect(formatLayout([])).toBeNull();
    expect(formatLayout([[]])).toBeNull();
  });

  it("drops a facet the builder cannot offer rather than carrying it", () => {
    // A level the owner cannot see is a level they would delete by accident on
    // the next save, so it is not shown as one.
    expect(parseLayout("project/camera")).toEqual([["project"]]);
    expect(formatLayout([["project", "camera"]])).toBe("project");
  });
});

describe("describeSegment", () => {
  it("reads a segment the way the artboard writes it", () => {
    expect(describeSegment(["person"])).toBe("Person");
    expect(describeSegment(["person", "set"])).toBe("Person or Set");
    expect(describeSegment(["project", "person", "set"])).toBe(
      "Project, Person or Set",
    );
    expect(describeSegment([])).toBe("");
  });
});

describe("layoutExamples", () => {
  // The renderer behind these is private on purpose (see the module docstring),
  // so its two rules - first match wins, an unfilled segment is skipped - are
  // asserted through the four fixtures that are its only callers.
  it("shows the default layout doing what the artboard says it does", () => {
    const examples = layoutExamples([["project"], ["person", "set"]], "_Inbox");
    expect(examples.map((e) => e.folder)).toEqual([
      "2024 Shoots / Mira /",
      "2024 Shoots / mira-lora-v3 /",
      "mira-lora-v3 /",
      "_Inbox /",
    ]);
  });

  it("follows the builder, so a narrowed layout visibly narrows the tree", () => {
    // The reason these are computed rather than written down: they are the
    // feedback for an edit, and a static strip would lie the moment one is made.
    const examples = layoutExamples([["project"]], "_Inbox");
    expect(examples.map((e) => e.folder)).toEqual([
      "2024 Shoots /",
      "2024 Shoots /",
      "_Inbox /",
      "_Inbox /",
    ]);
  });

  it("takes the first facet a picture has, and skips a segment nothing fills", () => {
    // Row 2 has no person, so `person,set` falls through to the set; row 3 has
    // no project, so the project level is skipped rather than left empty - the
    // property the whole grammar exists for, two deep instead of five.
    const [withPerson, withSet, setOnly] = layoutExamples(
      [["project"], ["person", "set"]],
      "_Inbox",
    );
    expect(withPerson.folder).toBe("2024 Shoots / Mira /");
    expect(withSet.folder).toBe("2024 Shoots / mira-lora-v3 /");
    expect(setOnly.folder).toBe("mira-lora-v3 /");
  });

  it("names the unfiled folder only when nothing fills anything", () => {
    const rows = layoutExamples([["project"], ["person", "set"]], "_Unsorted");
    expect(rows[3].folder).toBe("_Unsorted /");
    expect(rows[0].folder).not.toContain("_Unsorted");
  });
});
