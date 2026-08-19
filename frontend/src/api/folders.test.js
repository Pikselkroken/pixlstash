import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { apiClient } from "../utils/apiClient";
import {
  listReferenceFolders,
  listImportFolders,
  createFolder,
  patchFolder,
  deleteFolder,
  detectSidecars,
  browseFilesystem,
  createFilesystemFolder,
} from "./folders";

beforeEach(() => {
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.patch.mockReset();
  apiClient.delete.mockReset();
});

describe("api/folders", () => {
  it("listReferenceFolders GETs /reference-folders", async () => {
    apiClient.get.mockResolvedValue({ data: { folders: [] } });
    const result = await listReferenceFolders();
    expect(apiClient.get).toHaveBeenCalledWith("/reference-folders");
    expect(result).toEqual({ folders: [] });
  });

  it("listImportFolders GETs /import-folders", async () => {
    apiClient.get.mockResolvedValue({ data: { folders: [{ id: 1 }] } });
    const result = await listImportFolders();
    expect(apiClient.get).toHaveBeenCalledWith("/import-folders");
    expect(result).toEqual({ folders: [{ id: 1 }] });
  });

  // The two kinds share one editor, so the kind→path mapping is the part that
  // has to be right: a mix-up would write an import folder to the reference
  // collection.
  it("createFolder routes each kind to its own collection", async () => {
    apiClient.post.mockResolvedValue({ data: { id: 2 } });
    await createFolder("reference", { folder: "/a" });
    expect(apiClient.post).toHaveBeenCalledWith("/reference-folders", {
      folder: "/a",
    });
    await createFolder("import", { folder: "/b" });
    expect(apiClient.post).toHaveBeenCalledWith("/import-folders", {
      folder: "/b",
    });
  });

  it("patchFolder addresses the folder within its kind", async () => {
    apiClient.patch.mockResolvedValue({ data: { id: 3 } });
    await patchFolder("import", 3, { label: "Camera" });
    expect(apiClient.patch).toHaveBeenCalledWith("/import-folders/3", {
      label: "Camera",
    });
  });

  it("deleteFolder addresses the folder within its kind", async () => {
    apiClient.delete.mockResolvedValue({ data: {} });
    await deleteFolder("reference", 4);
    expect(apiClient.delete).toHaveBeenCalledWith("/reference-folders/4");
  });

  it("an unknown folder kind rejects instead of building a bad URL", async () => {
    await expect(createFolder("nonsense", {})).rejects.toThrow(
      /Unknown folder kind/,
    );
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it("detectSidecars asks about one reference path", async () => {
    apiClient.get.mockResolvedValue({ data: { found_tags: true } });
    const result = await detectSidecars("/photos/2026");
    expect(apiClient.get).toHaveBeenCalledWith(
      "/reference-folders/detect-sidecars",
      { params: { path: "/photos/2026" } },
    );
    expect(result).toEqual({ found_tags: true });
  });

  it("browseFilesystem defaults to hiding dot-entries", async () => {
    apiClient.get.mockResolvedValue({ data: { entries: [], path: "/" } });
    await browseFilesystem("/srv");
    expect(apiClient.get).toHaveBeenCalledWith("/filesystem/browse", {
      params: { path: "/srv", show_hidden: false, include_model_files: false },
    });
  });

  // A null path means "the server's default root", which the endpoint expresses
  // by the param being absent rather than null.
  it("browseFilesystem omits a null path", async () => {
    apiClient.get.mockResolvedValue({ data: { entries: [], path: "/" } });
    await browseFilesystem(null, { showHidden: true });
    expect(apiClient.get).toHaveBeenCalledWith("/filesystem/browse", {
      params: { path: undefined, show_hidden: true, include_model_files: false },
    });
  });

  // The shelf's `Add file` picker is the only caller that wants files listed,
  // so the flag is opt-in and every folder picker keeps its directory-only list.
  it("browseFilesystem asks for model files only when the picker wants them", async () => {
    apiClient.get.mockResolvedValue({ data: { entries: [], path: "/" } });
    await browseFilesystem("/srv", { includeModelFiles: true });
    expect(apiClient.get).toHaveBeenCalledWith("/filesystem/browse", {
      params: { path: "/srv", show_hidden: false, include_model_files: true },
    });
  });

  it("createFilesystemFolder POSTs the target path", async () => {
    apiClient.post.mockResolvedValue({ data: { path: "/srv/new" } });
    const result = await createFilesystemFolder("/srv/new");
    expect(apiClient.post).toHaveBeenCalledWith("/filesystem/folders", {
      path: "/srv/new",
    });
    expect(result).toEqual({ path: "/srv/new" });
  });
});
