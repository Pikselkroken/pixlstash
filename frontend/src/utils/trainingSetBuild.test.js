import { describe, expect, it } from "vitest";

import {
  buildingSetOf,
  buildLine,
  freeTrainingSetName,
  liveBuildRuns,
  proportionsFromDescription,
} from "./trainingSetBuild";

const runs = (...statuses) =>
  statuses.map((status, i) => ({ key: `r${i + 1}`, status }));

describe("trainingSetBuild", () => {
  it("names a new set the first free '<name> training set'", () => {
    expect(freeTrainingSetName("Mira", [])).toBe("Mira training set");
    expect(
      freeTrainingSetName("Mira", [
        { name: "MIRA training set" },
        { name: "Mira training set 2" },
      ]),
    ).toBe("Mira training set 3");
  });

  it("says where a build stands, and nothing for a finished set", () => {
    expect(buildLine(null, [])).toBeNull();
    expect(buildLine({ stage: "pick", line: { picked: 40, passing: 52 } }, []).text).toBe(
      "picking · 40 of 52",
    );
    expect(buildLine({ stage: "generate", sheets: { runs: [] } }, []).text).toBe(
      "not drawn yet",
    );
    const drawing = {
      stage: "generate",
      sheets: { runs: runs("completed", "queued", "queued", "queued") },
    };
    // Three still live, the first of them drawing at 45%.
    const live = [
      { run_id: "r2", status: "running", percent: 45.2 },
      { run_id: "r3", status: "queued" },
      { run_id: "r4", status: "queued" },
      { run_id: "elsewhere", status: "queued" },
    ];
    expect(buildLine(drawing, live)).toEqual({
      text: "drawing 2 of 4",
      percent: 45,
      error: false,
    });
    expect(liveBuildRuns(drawing, live).map((r) => r.run_id)).toEqual(["r2", "r3", "r4"]);
    expect(buildLine(drawing, []).text).toBe("4 drawn · keep one");
    const failed = { stage: "generate", sheets: { runs: runs("failed", "completed") } };
    expect(buildLine(failed, [])).toMatchObject({ text: "1 of 2 failed", error: true });
  });

  it("reads a little person or a cartoon figure from the description", () => {
    expect(proportionsFromDescription("Adult woman with dwarfism")).toBe("little_person");
    expect(proportionsFromDescription("A Little Person, red hair")).toBe("little_person");
    expect(proportionsFromDescription("Chibi girl, pink hair")).toBe("cartoon");
    // A cartoon style is not a cartoon figure.
    expect(proportionsFromDescription("Cartoon style, tall and slim")).toBeNull();
    expect(proportionsFromDescription("Adult man, short beard")).toBeNull();
    expect(proportionsFromDescription("")).toBeNull();
  });

  it("opens the build of the selected set, before the people have loaded", () => {
    const sets = [
      { id: 4, name: "Done", building: null, parent_character_id: 7 },
      { id: 5, name: "Mira training set", building: { v: 1 }, parent_character_id: 7 },
      { id: 6, name: "Lifted", building: { v: 1 }, parent_character_id: null },
    ];
    const people = [{ id: 7, name: "Mira" }];
    expect(buildingSetOf(sets, people, "5")).toEqual({
      set: sets[1],
      character: { id: 7, name: "Mira" },
    });
    // People not read yet: the build still opens, the name comes later.
    expect(buildingSetOf(sets, [], 5).character).toEqual({ id: 7, name: "" });
    expect(buildingSetOf(sets, people, 4)).toBeNull();
    expect(buildingSetOf(sets, people, 6)).toBeNull();
    expect(buildingSetOf(sets, people, null)).toBeNull();
  });

  it("counts only the draw on show, not runs an earlier draw left going", () => {
    const building = {
      stage: "generate",
      run_keys: ["old-1", "old-2", "old-3", "r2"],
      sheets: { runs: runs("completed", "queued") },
    };
    const live = [
      { run_id: "old-1", status: "running", percent: 10 },
      { run_id: "old-2", status: "queued" },
      { run_id: "old-3", status: "queued" },
      { run_id: "r2", status: "queued" },
    ];
    expect(buildLine(building, live).text).toBe("drawing 2 of 2");
    // A delete still stops all four.
    expect(liveBuildRuns(building, live)).toHaveLength(4);
  });
});
