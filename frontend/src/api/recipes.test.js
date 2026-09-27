// The wire form of `workflow_id` (#1623). Both reads take a list, and axios'
// default `workflow_id[]=` is a key FastAPI ignores: the Recipes tab then read
// 0 looks on every workflow while the server logged a clean 200.

import { describe, it, expect, beforeEach, vi } from "vitest";
import axios from "axios";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { listSavedRecipes, listUsedLooks } from "./recipes";

/** The query string axios would put on the wire for this call's config. */
function wireQuery(call) {
  const [url, config] = call;
  return axios.getUri({ url, ...config }).split("?")[1];
}

describe("recipes api", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiClient.get.mockResolvedValue({ data: [] });
  });

  it("sends each workflow id as its own workflow_id", async () => {
    await listUsedLooks(["a", "b"]);
    await listSavedRecipes(["a", "b"]);
    for (const call of apiClient.get.mock.calls) {
      expect(wireQuery(call)).toBe("workflow_id=a&workflow_id=b");
    }
  });

  it("takes one id as well as a list, and never names a stack", async () => {
    await listSavedRecipes("a");
    await listUsedLooks("a");
    for (const call of apiClient.get.mock.calls) {
      expect(wireQuery(call)).toBe("workflow_id=a");
    }
  });
});
