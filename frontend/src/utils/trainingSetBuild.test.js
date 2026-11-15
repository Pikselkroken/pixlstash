import { describe, expect, it } from "vitest";

import {
  buildLine,
  freeTrainingSetName,
  liveBuildRuns,
  subjectFromDescription,
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

  it("reads who a sheet draws from the description", () => {
    expect(subjectFromDescription("Adult man, short beard, his eyes grey")).toBe("man");
    expect(subjectFromDescription("Adult woman, copper bob")).toBe("woman");
    // "woman" and "human" hold "man" but are not it.
    expect(subjectFromDescription("A human woman")).toBe("woman");
    expect(subjectFromDescription("Freckles, green eyes")).toBeNull();
    expect(subjectFromDescription("")).toBeNull();
  });
});
