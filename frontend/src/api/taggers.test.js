import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { listTaggers, getLabelThresholds } from "./taggers";

beforeEach(() => {
  apiClient.get.mockReset();
});

describe("api/taggers", () => {
  it("listTaggers GETs /taggers and returns plugins plus settings", async () => {
    apiClient.get.mockResolvedValue({
      data: { plugins: [{ name: "a" }], settings: { active_tag_plugin: "a" } },
    });
    const result = await listTaggers();
    expect(apiClient.get).toHaveBeenCalledWith("/taggers");
    expect(result.plugins).toEqual([{ name: "a" }]);
  });

  it("listTaggers requests the taggers route", async () => {
    apiClient.get.mockResolvedValue({ data: {} });
    await listTaggers();
    expect(apiClient.get).toHaveBeenCalledWith("/taggers");
  });

  it("getLabelThresholds sends the previewed offset", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await getLabelThresholds(0.1);
    expect(apiClient.get).toHaveBeenCalledWith("/tagger/label-thresholds", {
      params: { offset: 0.1 },
    });
  });

  // No offset must mean "use the saved one", so the param is omitted rather
  // than sent as null (which the server would read as an explicit 0).
  it("getLabelThresholds omits a missing offset", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await getLabelThresholds();
    expect(apiClient.get).toHaveBeenCalledWith("/tagger/label-thresholds", {
      params: {},
    });
  });

  it("getLabelThresholds sends an explicit zero offset", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await getLabelThresholds(0);
    expect(apiClient.get).toHaveBeenCalledWith("/tagger/label-thresholds", {
      params: { offset: 0 },
    });
  });
});
