import { describe, it, expect, beforeEach, vi } from "vitest";

// Pattern for API-module tests: mock the singleton apiClient, assert the module
// builds the right URL and returns response.data (never the axios envelope).
vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listOperations,
  getUndoState,
  undoLastOperation,
  redoOperation,
  undoOperation,
  undoBatch,
} from "./operations";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
});

describe("api/operations", () => {
  it("listOperations GETs /operations with the default limit only", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1 }] });
    const result = await listOperations();
    expect(apiClient.get).toHaveBeenCalledWith("/operations", {
      params: { limit: 50 },
    });
    expect(result).toEqual([{ id: 1 }]);
  });

  it("listOperations sends only the filters it was given", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listOperations({
      limit: 10,
      status: "undone",
      batchId: "b-1",
      opType: "tag_add",
    });
    expect(apiClient.get).toHaveBeenCalledWith("/operations", {
      params: {
        limit: 10,
        status: "undone",
        batch_id: "b-1",
        op_type: "tag_add",
      },
    });
  });

  it("getUndoState GETs /operations/undo-state and returns the body", async () => {
    apiClient.get.mockResolvedValue({
      data: {
        can_undo: true,
        can_redo: false,
        next_undo: null,
        next_redo: null,
      },
    });
    const result = await getUndoState();
    expect(apiClient.get).toHaveBeenCalledWith("/operations/undo-state");
    expect(result).toEqual({
      can_undo: true,
      can_redo: false,
      next_undo: null,
      next_redo: null,
    });
  });

  it("undoLastOperation POSTs with no body when no operation is named", async () => {
    apiClient.post.mockResolvedValue({ data: { picture_count: 2 } });
    const result = await undoLastOperation();
    expect(apiClient.post).toHaveBeenCalledWith("/operations/undo");
    expect(result).toEqual({ picture_count: 2 });
  });

  it("undoLastOperation POSTs the operation id when one is named", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await undoLastOperation({ operationId: 7 });
    expect(apiClient.post).toHaveBeenCalledWith("/operations/undo", {
      operation_id: 7,
    });
  });

  it("redoOperation POSTs /operations/redo with no body", async () => {
    apiClient.post.mockResolvedValue({ data: { operations: [] } });
    const result = await redoOperation();
    expect(apiClient.post).toHaveBeenCalledWith("/operations/redo");
    expect(result).toEqual({ operations: [] });
  });

  it("undoOperation POSTs the per-operation undo path", async () => {
    apiClient.post.mockResolvedValue({ data: { picture_ids: [3] } });
    const result = await undoOperation(42);
    expect(apiClient.post).toHaveBeenCalledWith("/operations/42/undo");
    expect(result).toEqual({ picture_ids: [3] });
  });

  it("undoBatch POSTs the batch undo path with the id encoded", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await undoBatch("batch/1 2");
    expect(apiClient.post).toHaveBeenCalledWith(
      "/operations/batches/batch%2F1%202/undo",
    );
  });

  it("every call accepts an explicit backend base", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    apiClient.post.mockResolvedValue({ data: {} });
    await listOperations({ baseUrl: "/be" });
    await getUndoState({ baseUrl: "/be" });
    await undoLastOperation({ baseUrl: "/be" });
    await redoOperation({ baseUrl: "/be" });
    await undoOperation(5, { baseUrl: "/be" });
    await undoBatch("b-2", { baseUrl: "/be" });
    expect(apiClient.get).toHaveBeenCalledWith("/be/operations", {
      params: { limit: 50 },
    });
    expect(apiClient.get).toHaveBeenCalledWith("/be/operations/undo-state");
    expect(apiClient.post).toHaveBeenCalledWith("/be/operations/undo");
    expect(apiClient.post).toHaveBeenCalledWith("/be/operations/redo");
    expect(apiClient.post).toHaveBeenCalledWith("/be/operations/5/undo");
    expect(apiClient.post).toHaveBeenCalledWith(
      "/be/operations/batches/b-2/undo",
    );
  });

  it("propagates a rejected request instead of swallowing it", async () => {
    apiClient.post.mockRejectedValue(new Error("409 nothing to undo"));
    await expect(undoLastOperation()).rejects.toThrow("409 nothing to undo");
  });
});
