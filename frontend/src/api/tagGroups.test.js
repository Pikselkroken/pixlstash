// The tag-groups import route (`POST /tag_groups/import`). The flags travel as
// the strings "true"/"false" in form fields; the file under `file`.

import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { importTagGroups, listGroupTags } from "./tagGroups";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
});

describe("api/tagGroups: the paged tag list", () => {
  it("gets one page with the choice, filter, sort and offset as query params", async () => {
    apiClient.get.mockResolvedValue({ data: { total: 2, tags: [] } });
    const body = await listGroupTags({ group: "none", q: "pin", sort: "name", offset: 200, limit: 200 });
    expect(apiClient.get).toHaveBeenCalledWith("/tag_groups/tags", {
      params: { group: "none", q: "pin", sort: "name", offset: 200, limit: 200 },
    });
    expect(body).toEqual({ total: 2, tags: [] });
  });
});

describe("api/tagGroups: import", () => {
  it("posts the file with both flags as multipart form fields", async () => {
    apiClient.post.mockResolvedValue({ data: { plan: { filed: 1 } } });
    const file = new File(["x"], "danbooru_tags.xlsx");

    const body = await importTagGroups(file, { dryRun: true, overwriteUser: true });

    const [url, form, config] = apiClient.post.mock.calls[0];
    expect(url).toBe("/tag_groups/import");
    expect(form).toBeInstanceOf(FormData);
    expect(form.get("file").name).toBe("danbooru_tags.xlsx");
    expect(form.get("dry_run")).toBe("true");
    expect(form.get("overwrite_user")).toBe("true");
    expect(config.timeout).toBeGreaterThan(60_000);
    expect(body).toEqual({ plan: { filed: 1 } });
  });

  it("sends both flags as \"false\" by default", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await importTagGroups(new File(["x"], "t.csv"));
    const form = apiClient.post.mock.calls[0][1];
    expect(form.get("dry_run")).toBe("false");
    expect(form.get("overwrite_user")).toBe("false");
  });
});
