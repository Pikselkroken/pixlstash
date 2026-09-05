// The two ways a mapping can be committed.
//
// A read lives in ONE server process's memory. The desktop's first run reads
// the library folder while the GPU runtime downloads and then restarts the
// backend onto that runtime, so by the time the owner answers the mapping
// questions the task that produced the answer is gone: committing by task id
// could only ever be "Task not found", with the result sitting in the dialog.
// So the module sends whichever of the two it actually has.

import { describe, it, expect, vi, beforeEach } from "vitest";

// Pattern for API-module tests: mock the singleton apiClient, assert the module
// sends what the backend's route expects.
vi.mock("../utils/apiClient", () => ({
  apiClient: { post: vi.fn(), get: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { startFolderStructureCommit } from "./folderStructure";

const ASSIGNMENTS = [{ relative_path: "Alice", kind: "person" }];
const RESULT = {
  root: { path: "/home/me/Pictures" },
  picture_count: 5,
  levels: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  apiClient.post.mockResolvedValue({ data: { task_id: "commit-1" } });
});

describe("starting a folder-structure commit", () => {
  it("names the task when the server still has one", async () => {
    await startFolderStructureCommit(
      "read-1",
      ASSIGNMENTS,
      "Generations",
      "local_import",
    );

    expect(apiClient.post).toHaveBeenCalledWith("/folder-structure/commit", {
      task_id: "read-1",
      assignments: ASSIGNMENTS,
      mode: "local_import",
      label: "Generations",
    });
  });

  it("sends the result instead when the task is gone", async () => {
    await startFolderStructureCommit(
      "",
      ASSIGNMENTS,
      "",
      "local_import",
      RESULT,
    );

    const body = apiClient.post.mock.calls[0][1];
    expect(body).toEqual({
      read_result: RESULT,
      assignments: ASSIGNMENTS,
      mode: "local_import",
    });
    expect(body).not.toHaveProperty("task_id");
  });

  it("never sends both, which the route refuses", async () => {
    await startFolderStructureCommit(
      "read-1",
      ASSIGNMENTS,
      "",
      "reference",
      RESULT,
    );

    const body = apiClient.post.mock.calls[0][1];
    expect(body.task_id).toBe("read-1");
    expect(body).not.toHaveProperty("read_result");
  });

  it("leaves the label out when there is none", async () => {
    await startFolderStructureCommit("read-1", [], "", "reference");

    expect(apiClient.post.mock.calls[0][1]).not.toHaveProperty("label");
  });
});
