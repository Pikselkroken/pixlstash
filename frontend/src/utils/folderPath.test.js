import { describe, it, expect } from "vitest";

import { folderOrigin, splitFolderPath } from "./folderPath";

describe("splitFolderPath", () => {
  it("says a home directory as one word and keeps the last segment whole", () => {
    expect(splitFolderPath("/home/me/.cache/huggingface/hub")).toEqual({
      home: true,
      head: "/.cache/huggingface",
      tail: "/hub",
    });
    expect(splitFolderPath("/Users/me/models")).toEqual({
      home: true,
      head: "",
      tail: "/models",
    });
    expect(splitFolderPath("C:\\Users\\me\\loras\\sdxl")).toEqual({
      home: true,
      head: "\\loras",
      tail: "\\sdxl",
    });
  });

  it("leaves a path outside any home directory alone", () => {
    expect(splitFolderPath("/mnt/archive/loras")).toEqual({
      home: false,
      head: "/mnt/archive",
      tail: "/loras",
    });
    // A sibling whose name merely starts like a home is not one, and nor are
    // the shared folders that sit beside the homes.
    expect(splitFolderPath("/homes/x").home).toBe(false);
    expect(splitFolderPath("/Users/Shared/models").home).toBe(false);
    expect(splitFolderPath("C:\\Users\\Public\\models").home).toBe(false);
  });

  it("keeps a home directory itself as a path, not a bare word", () => {
    expect(splitFolderPath("/home/me")).toEqual({
      home: false,
      head: "/home",
      tail: "/me",
    });
  });

  it("reads a Windows home written with forward slashes", () => {
    expect(splitFolderPath("C:/Users/me/loras").home).toBe(true);
  });
});

describe("folderOrigin", () => {
  it("says where a folder is from its drive, home as ~", () => {
    expect(folderOrigin("/home/me/ComfyUI/models", "/")).toEqual({
      head: "~/ComfyUI/",
      tail: "models",
    });
    expect(folderOrigin("/mnt/scratch/ComfyUI/models", "/mnt/scratch")).toEqual(
      {
        head: "ComfyUI/",
        tail: "models",
      },
    );
    expect(folderOrigin("D:\\ComfyUI\\models", "D:\\")).toEqual({
      head: "ComfyUI\\",
      tail: "models",
    });
    // On the root drive the path stays absolute.
    expect(folderOrigin("/opt/models", "/")).toEqual({
      head: "/opt/",
      tail: "models",
    });
  });

  it("says the parent for a folder already named by its own last segment", () => {
    expect(
      folderOrigin("/home/me/.insightface/models", "/", { parent: true }),
    ).toEqual({
      head: "~/",
      tail: ".insightface",
    });
    expect(folderOrigin("/home/me/models", "/", { parent: true })).toEqual({
      head: "",
      tail: "~",
    });
    expect(folderOrigin("/models", "/", { parent: true })).toEqual({
      head: "",
      tail: "/",
    });
    // A folder at its drive's root keeps the mount point rather than nothing.
    expect(
      folderOrigin("/mnt/scratch/models", "/mnt/scratch", { parent: true }),
    ).toEqual({
      head: "/mnt/",
      tail: "scratch",
    });
  });

  it("keeps a home-level parent's whole name ahead of the ellipsis", () => {
    // Two namesakes one level under home must not both read `~/ComfyUI-…`.
    expect(
      folderOrigin("/home/me/ComfyUI-portable/models", "/", { parent: true }),
    ).toEqual({ head: "~/", tail: "ComfyUI-portable" });
  });

  it("drops the mount point only at a separator", () => {
    expect(folderOrigin("/mnt/scratchy/models", "/mnt/scratch").head).toBe(
      "/mnt/scratchy/",
    );
  });
});
