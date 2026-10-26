import { describe, it, expect, vi } from "vitest";

vi.mock("./apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { post: vi.fn().mockResolvedValue({ data: {} }) },
}));

import { apiClient } from "./apiClient";
import { attachImportMetadata, stageFiles } from "../api/pictureImport.js";
import { readEagleLibrary } from "./eagleLibrary.js";

function fileAt(path, content = "x") {
  const file = new File([content], path.split("/").pop());
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
}

function json(path, value) {
  return fileAt(path, JSON.stringify(value));
}

const LIB = "Mine.library";
const libraryFiles = () => [
  json(`${LIB}/metadata.json`, {
    folders: [
      {
        id: "F1",
        name: "Travel",
        children: [{ id: "F2", name: "Norway", children: [] }],
      },
    ],
  }),
  fileAt(`${LIB}/tags.json`, "{}"),
  json(`${LIB}/images/A.info/metadata.json`, {
    name: "fjord",
    ext: "jpg",
    tags: ["sea", "Blue Sky"],
    folders: ["F2", "gone"],
    star: 4,
    isDeleted: false,
  }),
  fileAt(`${LIB}/images/A.info/fjord.jpg`),
  fileAt(`${LIB}/images/A.info/fjord_thumbnail.png`),
  json(`${LIB}/images/B.info/metadata.json`, {
    name: "trashed",
    ext: "png",
    isDeleted: true,
  }),
  fileAt(`${LIB}/images/B.info/trashed.png`),
  json(`${LIB}/images/C.info/metadata.json`, {
    name: "layout",
    ext: "psd",
    tags: [],
    folders: [],
  }),
  fileAt(`${LIB}/images/C.info/layout.psd`),
  json(`${LIB}/images/D.info/metadata.json`, {
    name: "a/b",
    ext: "png",
    folders: ["F1"],
    star: 0,
  }),
  fileAt(`${LIB}/images/D.info/a_b_thumbnail.png`),
  fileAt(`${LIB}/images/D.info/a_b.png`),
];

describe("readEagleLibrary", () => {
  it("reads originals with their tags, rating and folder paths", async () => {
    const library = await readEagleLibrary(libraryFiles());
    expect(library.files.map((f) => f.name)).toEqual(["fjord.jpg", "a_b.png"]);
    expect(library.metadata).toEqual([
      { tags: ["sea", "Blue Sky"], score: 4, sets: ["Travel / Norway"] },
      { tags: [], score: null, sets: ["Travel"] },
    ]);
    expect(library.trashed).toBe(1);
    expect(library.unsupported).toBe(1);
  });

  it("refuses a folder that is not an Eagle library", async () => {
    await expect(
      readEagleLibrary([fileAt("Pictures/cat.png")]),
    ).rejects.toThrow(/not an Eagle library/);
  });
});

describe("stageFiles", () => {
  it("sends attached metadata parallel to the files", async () => {
    const tagged = new File(["a"], "a.png");
    const plain = new File(["b"], "b.png");
    attachImportMetadata(tagged, { tags: ["sea"], score: 2, sets: [] });
    await stageFiles({ backendUrl: "", stagingId: "s", files: [tagged, plain] });
    const form = apiClient.post.mock.calls.at(-1)[1];
    expect(JSON.parse(form.get("file_metadata"))).toEqual([
      { tags: ["sea"], score: 2, sets: [] },
      null,
    ]);
  });

  it("sends no metadata field for an ordinary import", async () => {
    await stageFiles({
      backendUrl: "",
      stagingId: "s",
      files: [new File(["c"], "c.png")],
    });
    expect(apiClient.post.mock.calls.at(-1)[1].has("file_metadata")).toBe(false);
  });
});
