import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  getAuthState,
  changePassword,
  listTokens,
  createToken,
  patchToken,
  deleteToken,
  uploadWatermark,
  deleteWatermark,
} from "./users";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.patch.mockReset();
  apiClient.delete.mockReset();
});

describe("api/users", () => {
  it("getAuthState GETs /users/me/auth", async () => {
    apiClient.get.mockResolvedValue({
      data: { username: "owner", has_password: true },
    });
    const result = await getAuthState();
    expect(apiClient.get).toHaveBeenCalledWith("/users/me/auth");
    expect(result.username).toBe("owner");
  });

  // The initial claim has no current password; sending null (not omitting the
  // key) is what tells the server this is that case.
  it("changePassword sends a null current password on the initial claim", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    await changePassword({ current_password: null, new_password: "hunter22" });
    expect(apiClient.post).toHaveBeenCalledWith("/users/me/auth", {
      current_password: null,
      new_password: "hunter22",
    });
  });

  it("listTokens GETs the token collection", async () => {
    apiClient.get.mockResolvedValue({ data: [{ id: 1 }] });
    const result = await listTokens();
    expect(apiClient.get).toHaveBeenCalledWith("/users/me/token");
    expect(result).toEqual([{ id: 1 }]);
  });

  it("listTokens prefixes an explicit backend base", async () => {
    apiClient.get.mockResolvedValue({ data: [] });
    await listTokens({ baseUrl: "/be" });
    expect(apiClient.get).toHaveBeenCalledWith("/be/users/me/token");
  });

  // The plaintext token comes back once, in the create response; losing it
  // here would leave the user with an unusable share link.
  it("createToken returns the minted secret", async () => {
    apiClient.post.mockResolvedValue({ data: { id: 3, token: "s3cret" } });
    const body = { scope: "READ", resource_type: "project", resource_id: 2 };
    const result = await createToken(body, { baseUrl: "/be" });
    expect(apiClient.post).toHaveBeenCalledWith("/be/users/me/token", body);
    expect(result.token).toBe("s3cret");
  });

  it("patchToken addresses one token", async () => {
    apiClient.patch.mockResolvedValue({ data: {} });
    await patchToken(3, { watermark: true });
    expect(apiClient.patch).toHaveBeenCalledWith("/users/me/token/3", {
      watermark: true,
    });
  });

  it("deleteToken revokes one token", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await deleteToken(3);
    expect(apiClient.delete).toHaveBeenCalledWith("/users/me/token/3");
  });

  // Multipart is the one place the JSON default content type must be
  // overridden, and the file has to arrive under the "file" field.
  it("uploadWatermark posts the file as multipart form data", async () => {
    apiClient.post.mockResolvedValue({ data: {} });
    const file = new File(["x"], "mark.png", { type: "image/png" });
    await uploadWatermark(file);
    const [url, form, config] = apiClient.post.mock.calls[0];
    expect(url).toBe("/users/me/watermark");
    expect(form).toBeInstanceOf(FormData);
    expect(form.get("file").name).toBe("mark.png");
    expect(config).toEqual({
      headers: { "Content-Type": "multipart/form-data" },
    });
  });

  it("deleteWatermark DELETEs the watermark", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await deleteWatermark();
    expect(apiClient.delete).toHaveBeenCalledWith("/users/me/watermark");
  });
});
