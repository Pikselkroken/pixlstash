import { describe, it, expect } from "vitest";

import { describeSegment, formatLayout, parseLayout } from "./libraryLayout";

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
