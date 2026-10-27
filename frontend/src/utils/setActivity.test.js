import { describe, expect, it } from "vitest";

import {
  activityOf,
  combinationsOfModel,
  combinationsOfSet,
  LATEST_PICTURES,
} from "./setActivity";

const flux = {
  key: "a",
  picture_count: 5,
  models: [{ id: 1 }, { id: 2 }],
  covered_by: [7],
  workflows: [
    { id: "w1", name: "Portrait", picture_count: 3 },
    { id: "w2", name: "Street", picture_count: 2 },
  ],
  last_used_at: "2026-10-01T10:00:00Z",
  latest: [
    { picture_id: 11, at: "2026-10-01T10:00:00Z" },
    { picture_id: 12, at: "2026-09-01T10:00:00Z" },
  ],
};
const fluxLora = {
  key: "b",
  picture_count: 4,
  models: [{ id: 1 }, { id: 3 }],
  workflows: [{ id: "w2", name: "Street", picture_count: 4 }],
  last_used_at: "2026-10-20T10:00:00Z",
  latest: [{ picture_id: 13, at: "2026-10-20T10:00:00Z" }],
};

describe("activityOf", () => {
  it("sums each workflow across combinations, most pictures first", () => {
    const got = activityOf([flux, fluxLora]);
    expect(got.workflows).toEqual([
      { id: "w2", name: "Street", picture_count: 6 },
      { id: "w1", name: "Portrait", picture_count: 3 },
    ]);
    expect(got.pictures).toBe(9);
  });

  it("takes the newest last use and the newest pictures first", () => {
    const got = activityOf([flux, fluxLora]);
    expect(got.lastUsedAt).toBe("2026-10-20T10:00:00Z");
    expect(got.latest.map((p) => p.picture_id)).toEqual([13, 11, 12]);
  });

  it("never takes an unreadable last use over a readable one", () => {
    const got = activityOf([
      { last_used_at: "not a date" },
      { last_used_at: "2026-10-01T00:00:00Z" },
    ]);
    expect(got.lastUsedAt).toBe("2026-10-01T00:00:00Z");
  });

  it("puts a picture with an unreadable date last", () => {
    const got = activityOf([
      {
        latest: [
          { picture_id: 1, at: "not a date" },
          { picture_id: 2, at: "2026-10-01T00:00:00Z" },
          { picture_id: 3, at: "2026-10-05T00:00:00Z" },
        ],
      },
    ]);
    expect(got.latest.map((p) => p.picture_id)).toEqual([3, 2, 1]);
  });

  it("caps the latest pictures", () => {
    const latest = Array.from({ length: 14 }, (_, i) => ({
      picture_id: i,
      at: `2026-10-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
    }));
    const got = activityOf([{ latest }]);
    expect(got.latest).toHaveLength(LATEST_PICTURES);
    expect(got.latest[0].picture_id).toBe(13);
  });

  it("answers nothing used for no combinations", () => {
    expect(activityOf([])).toEqual({
      workflows: [],
      lastUsedAt: null,
      latest: [],
      pictures: 0,
    });
  });
});

describe("combinationsOfSet / combinationsOfModel", () => {
  it("reads an owner's set by what it covers, an evidence card by its own", () => {
    const all = [flux, fluxLora];
    expect(combinationsOfSet({ set: { id: 7 } }, all)).toEqual([flux]);
    expect(combinationsOfSet({ combinations: [fluxLora] }, all)).toEqual([
      fluxLora,
    ]);
  });

  it("finds every combination a model ran in", () => {
    expect(combinationsOfModel(1, [flux, fluxLora])).toEqual([flux, fluxLora]);
    expect(combinationsOfModel(3, [flux, fluxLora])).toEqual([fluxLora]);
  });
});
