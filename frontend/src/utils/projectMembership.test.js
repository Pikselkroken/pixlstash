import { describe, expect, it } from "vitest";
import {
  entityBelongsToProject,
  getEntityProjectIds,
  toggleEntityProjectPatch,
  withEntityProjectIds,
} from "./projectMembership";

describe("multi-project entity membership", () => {
  it("adds a project from the + menu without moving existing membership", () => {
    const character = { id: 7, project_id: 2, project_ids: [2, 5] };

    expect(toggleEntityProjectPatch(character, 3)).toEqual({
      project_ids: [2, 3, 5],
    });
    expect(getEntityProjectIds(character)).toEqual([2, 5]);
  });

  it("removes only the current project when an active + menu item is toggled", () => {
    const pictureSet = { id: 8, project_id: 2, project_ids: [2, 3, 5] };

    expect(toggleEntityProjectPatch(pictureSet, 3)).toEqual({
      project_ids: [2, 5],
    });
  });

  it("keeps legacy scalar records compatible and aligns local primary state", () => {
    expect(entityBelongsToProject({ project_id: 4 }, 4)).toBe(true);
    expect(withEntityProjectIds({ id: 9 }, [7, 2])).toMatchObject({
      project_id: 2,
      project_ids: [2, 7],
    });
  });
});
