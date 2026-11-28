// Sheet draws closed mid-way (#2819): a placeholder each while their runs go
// on, how far by each run's percent in Tasks, ready once none is left.

import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useSheetDrawsStore } from "./useSheetDrawsStore";
import { useTasksStore } from "./useTasksStore";

beforeEach(() => setActivePinia(createPinia()));

const snapshot = (phase, keys) => ({ phase, runs: keys.map((key) => ({ key, status: "running" })) });

describe("the sheet draws", () => {
  it("shows a draw while its runs go on, at their mean progress", () => {
    const draws = useSheetDrawsStore();
    useTasksStore().comfyuiRuns = [
      { run_id: "a", percent: 50 },
      { run_id: "b", percent: 0 },
    ];
    draws.keep("outfit:5", {
      kind: "outfit",
      character: { id: 5, name: "Mira" },
      label: "Raincoat",
      snapshot: snapshot("drawing", ["a", "b", "c"]),
    });
    const [p] = draws.of("outfit", 5);
    // c has ended (no longer listed), a is halfway, b has not started.
    expect(p.fraction).toBeCloseTo((1 + 0.5 + 0) / 3);
    expect(p).toMatchObject({ name: "Raincoat", count: 3, ready: false });
    expect(p.status).toBe("Drawing 3 sheets…");
    expect(draws.of("outfit", 6)).toEqual([]);
    expect(draws.of("location")).toEqual([]);
  });

  it("is ready once no run of it is left, and gone when picked or forgotten", () => {
    const draws = useSheetDrawsStore();
    draws.keep("location:", { kind: "location", character: null, label: "Pub", snapshot: snapshot("pick", ["x"]) });
    expect(draws.of("location")[0]).toMatchObject({ ready: true, fraction: 1, status: "Sheets ready to pick" });
    // A draw back at its form has nothing coming.
    draws.keep("location:", { kind: "location", character: null, label: "Pub", snapshot: snapshot("describe", ["x"]) });
    expect(draws.of("location")).toEqual([]);
    draws.keep("prop:", { kind: "prop", character: null, label: "Lamp", snapshot: snapshot("drawing", ["y"]) });
    draws.forget("prop:");
    expect(draws.placeholders).toEqual([]);
  });
});
