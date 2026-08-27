// The model shelf's icon routes.
//
// `setModelIcon` is where the thumbnail verb actually failed: it built the
// FormData correctly and then let the transport's JSON default rewrite it into
// `{"file":{}}` (see `utils/apiClient.js`). The transport now clears that
// header, so what is worth guarding HERE is the half this module owns - the
// body really is a multipart form, and the bytes really are under the field
// name the route reads (`file: UploadFile = File(...)`, `routes/model_icons.py`).

import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), post: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import { clearModelIcons, modelIconUrl, setModelIcon } from "./modelIcons";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
});

describe("api/modelIcons", () => {
  it("posts a picture's thumbnail bytes as a multipart form", async () => {
    // A Blob, not a File: the library route sends what
    // `getPictureThumbnailBlob` returned, which has no name of its own. The
    // route reads the bytes and sniffs them, so it never needs one.
    apiClient.post.mockResolvedValue({ data: { model_id: 12, icon_sha256: "ab" } });
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "image/webp" });

    const body = await setModelIcon(12, blob);

    const [url, form] = apiClient.post.mock.calls[0];
    expect(url).toBe("/models/12/icon");
    expect(form).toBeInstanceOf(FormData);
    expect(form.get("file")).toBeInstanceOf(Blob);
    expect(form.get("file").size).toBe(3);
    expect(body).toEqual({ model_id: 12, icon_sha256: "ab" });
  });

  it("sends a chosen file the same way", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    const file = new File(["x"], "mark.png", { type: "image/png" });
    await setModelIcon(4, file);
    expect(apiClient.post.mock.calls[0][1].get("file").name).toBe("mark.png");
  });

  it("clears by id list, in one request", async () => {
    apiClient.post.mockResolvedValue({ data: { cleared: [1] } });
    const body = await clearModelIcons([1, 2]);
    expect(apiClient.post).toHaveBeenCalledWith("/models/icons/clear", {
      ids: [1, 2],
    });
    expect(body).toEqual({ cleared: [1] });
  });

  it("addresses a stored icon by content hash", () => {
    expect(modelIconUrl("abc123")).toContain("/model-icons/abc123");
  });
});
