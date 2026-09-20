// The one function here that is not a request: the cover URL join.
//
// It lives on the api layer for `pictureThumbnailUrl`'s stated reason - the
// path is part of a contract and a second spelling of it elsewhere is the
// drift this layer exists to prevent - so it is tested here rather than
// through the component that used to spell it.

import { describe, expect, it, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  appendShareToken: (url) => `${url}&token=test-share-token`,
  apiClient: { get: vi.fn(), patch: vi.fn(), put: vi.fn(), post: vi.fn() },
}));

import { workflowCoverUrl } from "./workflows";

describe("workflowCoverUrl", () => {
  // The payload's own shape, from `_cover_urls`.
  const cover = "/pictures/thumbnails/12.webp?v=3";

  it("prefixes the API base, so the src names a route that exists", () => {
    // Used verbatim the browser asks the PAGE origin for this path, which
    // nothing serves, and every cover on the grid breaks.
    expect(workflowCoverUrl(cover)).toContain("/api/v1/pictures/thumbnails/12");
    expect(workflowCoverUrl(cover)).not.toMatch(/^\/pictures\//);
  });

  it("carries the share token, which an <img> cannot get from Axios", () => {
    expect(workflowCoverUrl(cover)).toContain("token=test-share-token");
  });

  it("keeps the server's cache-buster", () => {
    // `v` is `thumbnail_cache_version`; dropping it serves a stale bitmap
    // after a regeneration.
    expect(workflowCoverUrl(cover)).toContain("v=3");
  });
});
