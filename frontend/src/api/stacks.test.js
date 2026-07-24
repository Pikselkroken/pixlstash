import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  getStack,
  listStackPictures,
  createStack,
  setStackOrder,
  removeStackMembers,
} from "./stacks";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.patch.mockReset();
  apiClient.delete.mockReset();
});

describe("api/stacks", () => {
  it("getStack returns the stack with its member ids", async () => {
    apiClient.get.mockResolvedValue({ data: { id: 3, picture_ids: [1, 2] } });
    const result = await getStack(3);
    expect(apiClient.get).toHaveBeenCalledWith("/stacks/3");
    expect(result.picture_ids).toEqual([1, 2]);
  });

  // The grid only ever needs the grid projection; asking for the full record
  // is markedly slower, so that default must not drift.
  it("listStackPictures defaults to the grid projection", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listStackPictures(3);
    expect(apiClient.get).toHaveBeenCalledWith("/stacks/3/pictures", {
      params: { fields: "grid" },
    });
  });

  it("listStackPictures forwards a sort and its direction", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listStackPictures(3, { sort: "DATE", descending: true });
    expect(apiClient.get).toHaveBeenCalledWith("/stacks/3/pictures", {
      params: { fields: "grid", sort: "DATE", descending: "true" },
    });
  });

  // No sort means "the stack's own order"; sending an empty sort would ask the
  // server to re-order the members instead.
  it("listStackPictures omits an absent sort", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listStackPictures(3, { sort: undefined, descending: undefined });
    expect(apiClient.get).toHaveBeenCalledWith("/stacks/3/pictures", {
      params: { fields: "grid" },
    });
  });

  it("createStack sends the ids in the caller's order", async () => {
    apiClient.post.mockResolvedValue({ data: { id: 9 } });
    await createStack([5, 3, 4], { baseUrl: "/be" });
    expect(apiClient.post).toHaveBeenCalledWith("/be/stacks", {
      picture_ids: [5, 3, 4],
    });
  });

  it("setStackOrder PATCHes the order sub-resource", async () => {
    apiClient.patch.mockResolvedValue({ data: {} });
    await setStackOrder(9, [3, 4, 5]);
    expect(apiClient.patch).toHaveBeenCalledWith("/stacks/9/order", {
      picture_ids: [3, 4, 5],
    });
  });

  // Dissolving is "remove every member", and the ids ride in a DELETE body,
  // which Axios only sends via config.data.
  it("removeStackMembers sends the ids as the DELETE body", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await removeStackMembers(9, [3, 4]);
    expect(apiClient.delete).toHaveBeenCalledWith("/stacks/9/members", {
      data: { picture_ids: [3, 4] },
    });
  });
});
