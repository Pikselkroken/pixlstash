// The two things on this layer nothing else can prove.
//
// The cover URL join lives here for `pictureThumbnailUrl`'s stated reason -
// the path is part of a contract and a second spelling of it elsewhere is the
// drift this layer exists to prevent - so it is tested here rather than
// through the component that used to spell it.
//
// `listWorkflowCards`'s query string is the other, and for a harder reason:
// **every consumer of it mocks this module**, so the store, the filter panel
// and the view all assert the ARGUMENTS the store passed and none of them can
// see whether a flag became a URL. Deleting the query build left the whole
// suite green and both Filters checkboxes inert (F7).

import { describe, expect, it, vi } from "vitest";

const get = vi.fn();
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  appendShareToken: (url) => `${url}&token=test-share-token`,
  apiClient: {
    get: (...args) => get(...args),
    patch: vi.fn(),
    put: vi.fn(),
    post: vi.fn(),
  },
}));

import { listWorkflowCards, workflowCoverUrl } from "./workflows";

describe("workflowCoverUrl", () => {
  // The payload's own shape, from `_covers`: an object since #1465, of which
  // this function reads `url` and the crop helpers read the rest.
  const cover = {
    url: "/pictures/thumbnails/12.webp?v=3",
    thumbnail_width: 384,
    thumbnail_height: 561,
    square_crop_x: 0,
    square_crop_y: 120,
    square_crop_side: 384,
  };

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

  it("gives an entry with no url nothing, not a broken-image path", () => {
    // `/api/v1undefined` is truthy, so a `v-if` on it draws a broken-image
    // glyph where the caller meant "this cover has no picture".
    expect(workflowCoverUrl({})).toBe("");
    expect(workflowCoverUrl(null)).toBe("");
  });
});

describe("listWorkflowCards", () => {
  const answer = { data: { cards: [], one_offs: 3, hidden: 2 } };

  it("asks for the plain grid with no query at all", async () => {
    get.mockResolvedValue(answer);
    await listWorkflowCards();
    expect(get).toHaveBeenCalledWith("/workflows");
  });

  it("puts each Filters checkbox on the URL under the name the route takes", async () => {
    // The route's own spelling (`pixlstash/routes/workflows.py`), and `true`
    // rather than `1`: FastAPI takes both, but the OpenAPI surface and every
    // backend test say `true`, and one spelling is what keeps them checkable
    // against each other.
    get.mockResolvedValue(answer);
    await listWorkflowCards({ includeOneOffs: true });
    expect(get).toHaveBeenCalledWith("/workflows?include_one_offs=true");

    await listWorkflowCards({ includeHidden: true });
    expect(get).toHaveBeenCalledWith("/workflows?include_hidden=true");

    await listWorkflowCards({ includeHidden: true, includeOneOffs: true });
    expect(get).toHaveBeenCalledWith(
      "/workflows?include_hidden=true&include_one_offs=true",
    );
  });

  // False is the server's own default, so sending it is noise on every URL in
  // the log and in the cache key.
  it("sends nothing for a flag that is off", async () => {
    get.mockResolvedValue(answer);
    await listWorkflowCards({ includeHidden: false, includeOneOffs: false });
    expect(get).toHaveBeenCalledWith("/workflows");
  });

  it("hands back the counts, which are not the length of `cards`", async () => {
    get.mockResolvedValue(answer);
    expect(await listWorkflowCards()).toEqual({
      cards: [],
      one_offs: 3,
      hidden: 2,
    });
  });
});
