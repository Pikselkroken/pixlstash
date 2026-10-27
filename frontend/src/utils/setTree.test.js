import { describe, expect, it } from "vitest";
import { ancestorsOf, buildSetTree, flattenBranch } from "./setTree.js";

const set = (id, parent = {}) => ({ id, name: `s${id}`, ...parent });

describe("buildSetTree", () => {
  it("files a set under its listed parent set or person", () => {
    const sets = [
      set(1),
      set(2, { parent_set_id: 1 }),
      set(3, { parent_character_id: 7 }),
    ];
    const tree = buildSetTree(sets, [7]);
    expect(tree.roots.map((s) => s.id)).toEqual([1]);
    expect(tree.bySet.get(1).map((s) => s.id)).toEqual([2]);
    expect(tree.byCharacter.get(7).map((s) => s.id)).toEqual([3]);
  });

  it("keeps a set whose parent is not listed as a root, so none vanish", () => {
    const sets = [set(2, { parent_set_id: 99 }), set(3, { parent_character_id: 8 })];
    const tree = buildSetTree(sets, [7]);
    expect(tree.roots.map((s) => s.id)).toEqual([2, 3]);
  });

  it("roots a stored cycle rather than hiding it", () => {
    const sets = [set(1, { parent_set_id: 2 }), set(2, { parent_set_id: 1 })];
    expect(buildSetTree(sets, []).roots.map((s) => s.id)).toEqual([1, 2]);
  });
});

describe("flattenBranch", () => {
  const sets = [
    set(10, { parent_character_id: 7 }),
    set(11, { parent_set_id: 10 }),
    set(12, { parent_set_id: 10 }),
    set(20, { parent_character_id: 7 }),
  ];
  const tree = buildSetTree(sets, [7]);

  it("shows only open branches, depth first, with levels and elbows", () => {
    const closed = flattenBranch(tree.byCharacter.get(7), tree.bySet, () => false);
    expect(closed.map((r) => [r.set.id, r.level, r.last])).toEqual([
      [10, 1, false],
      [20, 1, true],
    ]);
    expect(closed[0]).toMatchObject({ childCount: 2, expanded: false });

    const open = flattenBranch(tree.byCharacter.get(7), tree.bySet, (id) => id === 10);
    expect(open.map((r) => [r.set.id, r.level, r.last, r.rails])).toEqual([
      [10, 1, false, []],
      [11, 2, false, [1]],
      [12, 2, true, [1]],
      [20, 1, true, []],
    ]);
  });

  it("drops the rail of an ancestor that was the last child", () => {
    const rows = flattenBranch([set(20, {})], buildSetTree([set(20), set(21, { parent_set_id: 20 })], []).bySet, () => true);
    expect(rows.map((r) => [r.set.id, r.rails])).toEqual([
      [20, []],
      [21, []],
    ]);
  });
});

describe("ancestorsOf", () => {
  it("names every listed set above and the person at the top", () => {
    const sets = [
      set(10, { parent_character_id: 7 }),
      set(11, { parent_set_id: 10 }),
      set(12, { parent_set_id: 11 }),
    ];
    expect(ancestorsOf(12, sets)).toEqual({
      setIds: [11, 10],
      characterId: 7,
      resolved: true,
    });
    expect(ancestorsOf(10, sets)).toEqual({
      setIds: [],
      characterId: 7,
      resolved: true,
    });
    // A parent outside the list leaves the depth unknown.
    expect(ancestorsOf(13, [set(13, { parent_set_id: 99 })]).resolved).toBe(false);
  });
});
