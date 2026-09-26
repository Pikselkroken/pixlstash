// The wire form of `workflow_key`. Both reads take a list, and axios' default
// `workflow_key[]=` is a key FastAPI ignores: the Recipes tab then read 0
// looks on every card while the server logged a clean 200.

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

  it("sends each workflow key as its own workflow_key", async () => {
    await listUsedLooks(["a", "b"]);
    await listSavedRecipes(["a", "b"]);
    for (const call of apiClient.get.mock.calls) {
      expect(wireQuery(call)).toBe("workflow_key=a&workflow_key=b");
    }
  });

  it("asks for whole_stack=false only when told to", async () => {
    await listUsedLooks(["a"], { wholeStack: false });
    await listSavedRecipes(["a"], { wholeStack: false });
    await listSavedRecipes(["a"]);
    const [used, saved, plain] = apiClient.get.mock.calls.map(wireQuery);
    expect(used).toBe("workflow_key=a&whole_stack=false");
    expect(saved).toBe("workflow_key=a&whole_stack=false");
    expect(plain).toBe("workflow_key=a");
  });
});
