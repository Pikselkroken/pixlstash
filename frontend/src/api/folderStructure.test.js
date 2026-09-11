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
import {
  captionAnswerFor,
  startFolderStructureCommit,
} from "./folderStructure";

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
  // A caller that passes no answer has asked nobody, so the default omits the
  // key and lets the import probe - the same as passing `null`. Defaulting to
  // `[]` instead made every pre-caption call claim "read nothing".
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
    expect(apiClient.post.mock.calls[0][1]).not.toHaveProperty("captions");
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
    expect(body).not.toHaveProperty("captions");
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

  // `[]` and no key at all are two different instructions: `[]` is the owner
  // answering "read nothing", no key asks the import to probe the known
  // conventions. A local import answers; `reference` refuses the pair.
  it("sends an empty answer in a local import rather than omitting it", async () => {
    await startFolderStructureCommit("read-1", [], "", "local_import", null, []);

    expect(apiClient.post.mock.calls[0][1].captions).toEqual([]);
  });

  it("omits the key in a local import when nobody was asked", async () => {
    await startFolderStructureCommit(
      "read-1",
      [],
      "",
      "local_import",
      null,
      null,
    );

    expect(apiClient.post.mock.calls[0][1]).not.toHaveProperty("captions");
  });

  it("sends the answers it is given in a local import", async () => {
    const answers = [{ suffix: ".txt", kind: "tags" }];
    await startFolderStructureCommit(
      "read-1",
      [],
      "",
      "local_import",
      null,
      answers,
    );

    expect(apiClient.post.mock.calls[0][1].captions).toEqual(answers);
  });

  it("never sends captions with a reference folder, which the route refuses", async () => {
    await startFolderStructureCommit("read-1", [], "", "reference", null, [
      { suffix: ".txt", kind: "tags" },
    ]);

    expect(apiClient.post.mock.calls[0][1]).not.toHaveProperty("captions");
  });
});

describe("captionAnswerFor", () => {
  it("answers '[]' for a complete read, whatever it found", () => {
    expect(captionAnswerFor({ captions: [], captions_complete: true })).toEqual(
      [],
    );
    // `captions_complete` absent is complete: only `false` says it stopped.
    expect(captionAnswerFor({ captions: [] })).toEqual([]);
    expect(
      captionAnswerFor({ captions: [{ suffix: ".txt", kind: "tags" }] }),
    ).toEqual([]);
  });

  it("answers null for a read that stopped early", () => {
    // Nobody could have been asked about the folders the walk never reached.
    expect(
      captionAnswerFor({ captions: [], captions_complete: false }),
    ).toBeNull();
  });

  it("answers null for a read with no captions array at all", () => {
    // From before the caption card: it sniffed nothing and asked nobody, so
    // `[]` would claim "read nothing" for a walk that never looked. Only a
    // result carrying the array is an answer source.
    expect(captionAnswerFor({ picture_count: 5, levels: [] })).toBeNull();
    expect(captionAnswerFor({ captions: null })).toBeNull();
    expect(captionAnswerFor(null)).toBeNull();
    expect(captionAnswerFor()).toBeNull();
  });
});
