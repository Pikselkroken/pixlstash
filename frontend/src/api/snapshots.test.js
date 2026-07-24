import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listSnapshots,
  getSnapshotStatus,
  createSnapshot,
  renameSnapshot,
  deleteSnapshot,
  previewRestore,
  previewRestoreBatch,
  executeRestore,
  executeRestoreBatch,
} from "./snapshots";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.patch.mockReset();
  apiClient.delete.mockReset();
});

describe("api/snapshots", () => {
  // The call sites this module replaced hardcoded "/api/v1/..."; the prefix is
  // the interceptor's job, so every URL here must be unprefixed.
  it("addresses snapshots without the /api/v1 prefix", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listSnapshots();
    expect(apiClient.get).toHaveBeenCalledWith("/snapshots");
  });

  it("listSnapshots returns the body", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1 }] });
    expect(await listSnapshots()).toEqual([{ id: 1 }]);
  });

  it("getSnapshotStatus GETs /snapshots/status", async () => {
    apiClient.get.mockResolvedValue({ data: { active_job: null } });
    const result = await getSnapshotStatus();
    expect(apiClient.get).toHaveBeenCalledWith("/snapshots/status");
    expect(result).toEqual({ active_job: null });
  });

  it("createSnapshot sends the label when given one", async () => {
    apiClient.post.mockResolvedValue({ data: { id: 7 } });
    const result = await createSnapshot("before import");
    expect(apiClient.post).toHaveBeenCalledWith("/snapshots", {
      label: "before import",
    });
    expect(result).toEqual({ id: 7 });
  });

  it("createSnapshot sends an empty body when the label is falsy", async () => {
    apiClient.post.mockResolvedValue({ data: { id: 8 } });
    await createSnapshot("");
    expect(apiClient.post).toHaveBeenCalledWith("/snapshots", {});
  });

  it("renameSnapshot PATCHes the label", async () => {
    apiClient.patch.mockResolvedValue({ data: { id: 3, label: "new" } });
    const result = await renameSnapshot(3, "new");
    expect(apiClient.patch).toHaveBeenCalledWith("/snapshots/3", {
      label: "new",
    });
    expect(result).toEqual({ id: 3, label: "new" });
  });

  it("deleteSnapshot DELETEs the snapshot", async () => {
    apiClient.delete.mockResolvedValue({ data: { ok: true } });
    await deleteSnapshot(3);
    expect(apiClient.delete).toHaveBeenCalledWith("/snapshots/3");
  });

  it("previewRestore GETs the whole-snapshot preview", async () => {
    apiClient.get.mockResolvedValue({ data: { changes: 2 } });
    const result = await previewRestore(5);
    expect(apiClient.get).toHaveBeenCalledWith("/snapshots/5/restore/preview");
    expect(result).toEqual({ changes: 2 });
  });

  it("previewRestoreBatch POSTs the resource refs", async () => {
    apiClient.post.mockResolvedValue({ data: { changes: 1 } });
    const resources = [{ kind: "PICTURE", id: 9 }];
    await previewRestoreBatch(5, resources);
    expect(apiClient.post).toHaveBeenCalledWith(
      "/snapshots/5/restore/preview/batch",
      { resources },
    );
  });

  it("executeRestore POSTs an empty body for a whole-vault restore", async () => {
    apiClient.post.mockResolvedValue({ data: { job: 1 } });
    await executeRestore(5);
    expect(apiClient.post).toHaveBeenCalledWith("/snapshots/5/restore", {});
  });

  it("executeRestoreBatch defaults the dependency confirmation to false", async () => {
    apiClient.post.mockResolvedValue({ data: { job: 2 } });
    const resources = [{ kind: "PICTURE", id: 9 }];
    await executeRestoreBatch(5, resources);
    expect(apiClient.post).toHaveBeenCalledWith("/snapshots/5/restore/batch", {
      resources,
      confirm_restore_dependencies: false,
    });
  });

  it("executeRestoreBatch forwards an explicit dependency confirmation", async () => {
    apiClient.post.mockResolvedValue({ data: { job: 3 } });
    const resources = [{ kind: "PICTURE", id: 9 }];
    await executeRestoreBatch(5, resources, true);
    expect(apiClient.post).toHaveBeenCalledWith("/snapshots/5/restore/batch", {
      resources,
      confirm_restore_dependencies: true,
    });
  });
});
