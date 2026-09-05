// The URLs the BROWSER loads by itself - an `<img src>`, a download - rather
// than through Axios.
//
// Deliberately NOT co-located per resource module, and deliberately UNMOCKED,
// because the bug it guards lives in exactly the gap a per-module test with a
// mocked `../utils/apiClient` cannot see: `/api/v1` is added by the request
// interceptor, so a URL built from `apiClient.defaults.baseURL` is missing it
// and lands on the SPA fallback, which answers 200 with HTML. Every existing
// mock of these builders hardcodes the prefixed string, which is why the suite
// stayed green while the model shelf drew no marks at all. Here the real
// module computes the base, so the assertion can fail.

import { describe, it, expect } from "vitest";

import { characterThumbnailUrl } from "./characters";
import { modelIconUrl } from "./modelIcons";
import { runSampleUrl } from "./modelImports";
import { pictureSetThumbnailUrl } from "./pictureSets";
import { pictureThumbnailUrl } from "./pictures";
import { API_BASE_URL } from "../utils/apiClient";

describe("browser-loaded URLs carry the API prefix", () => {
  const cases = [
    [
      "characterThumbnailUrl",
      characterThumbnailUrl(12),
      "/characters/12/thumbnail",
    ],
    [
      "pictureSetThumbnailUrl",
      pictureSetThumbnailUrl(5),
      "/picture_sets/5/thumbnail",
    ],
    ["modelIconUrl", modelIconUrl("abc123"), "/model-icons/abc123"],
    [
      "runSampleUrl",
      runSampleUrl(3, "run one", "s.png"),
      "/model-folders/3/runs/run%20one/samples/s.png",
    ],
    // The one that was already right, kept here as the positive control: if the
    // prefix ever moves, this fails alongside the others rather than leaving
    // the failure looking like a quirk of the shelf's four.
    [
      "pictureThumbnailUrl",
      pictureThumbnailUrl(7),
      "/pictures/thumbnails/7.webp",
    ],
  ];

  it.each(cases)("%s", (_name, url, path) => {
    expect(url).toBe(`${API_BASE_URL}${path}`);
  });
});
