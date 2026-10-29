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
  const text = (origin) => origin.head + origin.tail;

  it("says the home folder as ~", () => {
    expect(folderOrigin("/home/me/ComfyUI/models", "/")).toEqual({
      head: "~/ComfyUI",
      tail: "/models",
    });
    // Without its own last segment, for a row already named by it.
    expect(text(folderOrigin("/home/me/.config/pixlstash-desktop/models", "/", false))).toBe(
      "~/.config/pixlstash-desktop",
    );
    expect(folderOrigin("/home/me/models", "/", false)).toEqual({ head: "", tail: "~" });
  });

  it("starts where the drive's mount point ends", () => {
    expect(text(folderOrigin("/mnt/scratch/Forge-old/models", "/mnt/scratch"))).toBe(
      "Forge-old/models",
    );
    expect(text(folderOrigin("/mnt/scratch/Forge/models", "/mnt/scratch/"))).toBe("Forge/models");
    // A trailing separator on the path still leaves its own name out.
    expect(folderOrigin("/mnt/scratch/Forge/models/", "/mnt/scratch", false)).toEqual({
      head: "",
      tail: "Forge",
    });
    expect(text(folderOrigin("D:\\SD\\models", "D:\\"))).toBe("SD\\models");
    // A folder whose parent is the mount point says the mount, never nothing.
    expect(text(folderOrigin("/mnt/scratch/models", "/mnt/scratch", false))).toBe("/mnt/scratch");
  });

  it("keeps a path whole that is not under the mount point", () => {
    // The root drive's "/" is not stripped, nor a sibling that only starts alike.
    expect(text(folderOrigin("/opt/models", "/"))).toBe("/opt/models");
    expect(text(folderOrigin("/mnt/scratchy/models", "/mnt/scratch"))).toBe("/mnt/scratchy/models");
    // As long as the mount point, with a separator where it would end.
    expect(text(folderOrigin("/srv/archive/models", "/mnt/scratch"))).toBe("/srv/archive/models");
    expect(text(folderOrigin("/models", "/", false))).toBe("/");
    expect(text(folderOrigin("/srv/models", ""))).toBe("/srv/models");
  });
});
