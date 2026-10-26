import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("./apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { post: vi.fn().mockResolvedValue({ data: {} }) },
}));

import { apiClient } from "./apiClient";
import { attachImportMetadata, stageFiles } from "../api/pictureImport.js";
import { readEagleLibrary } from "./eagleLibrary.js";

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

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
    tags: ["sea", null, "Blue Sky", 7],
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
  fileAt(`${LIB}/images/E.info/metadata.json`, ""),
  fileAt(`${LIB}/images/E.info/half-synced.png`),
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
    expect(library.unreadable).toBe(1);
  });

  it("drops values of the wrong type instead of failing", async () => {
    const files = [
      json(`${LIB}/metadata.json`, {
        folders: [null, { id: "F0" }, { id: "F1", name: "Ok", children: {} }],
      }),
      json(`${LIB}/images/A.info/metadata.json`, {
        name: "a",
        ext: "png",
        star: 4.5,
        folders: ["F0", "F1"],
      }),
      fileAt(`${LIB}/images/A.info/a.png`),
      json(`${LIB}/images/B.info/metadata.json`, {
        name: "b",
        ext: "png",
        star: "4",
        folders: {},
      }),
      fileAt(`${LIB}/images/B.info/b.png`),
    ];
    const library = await readEagleLibrary(files);
    expect(library.metadata).toEqual([
      { tags: [], score: null, sets: ["Ok"] },
      { tags: [], score: null, sets: [] },
    ]);
  });

  it("still imports the pictures when the folder tree will not parse", async () => {
    const files = libraryFiles();
    files[0] = fileAt(`${LIB}/metadata.json`, "{");
    const library = await readEagleLibrary(files);
    expect(library.files.map((f) => f.name)).toEqual(["fjord.jpg", "a_b.png"]);
    expect(library.metadata.map((m) => m.sets)).toEqual([[], []]);
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
