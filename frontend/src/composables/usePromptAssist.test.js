// Photo look's remembered choices. What can silently go wrong: a stored
// default pins an owner to last release's look (sparse storage is the fix), an
// emptied row comes back on reload, typed text joins the list before a run
// was sent with it, or the sheet's choices leak into the Run dialog's. Typed
// entries live where the sheet step's typed Top and Shoes do, and behave alike.

import { beforeEach, describe, expect, it } from "vitest";
import { ref } from "vue";

import { usePromptAssist } from "./usePromptAssist";

const KEY = "pixlstash:promptAssist";
const RECENT = "pixlstash:characterSheetChoices";

function assist({ cameraDefault = "phone", context = "photo", defaultOn = true } = {}) {
  return {
    family: "minimax_h3_image",
    context,
    preset_version: 1,
    default_on: defaultOn,
    applied: true,
    categories: [
      {
        id: "camera",
        label: "Camera",
        default: cameraDefault,
        options: [
          { id: "phone", label: "Phone snapshot", text: "Taken on a phone." },
          { id: "film", label: "35 mm film", text: "Shot on 35 mm film." },
        ],
      },
      {
        id: "skin",
        label: "Skin and makeup",
        default: "none",
        options: [{ id: "bare", label: "Bare skin", text: "Natural skin." }],
      },
    ],
    parts: [],
    recorded: null,
  };
}

/** A fresh surface, as a reopened dialog reads it. */
const open = (catalogue = assist(), context = "photo") =>
  usePromptAssist(ref(catalogue), context);

beforeEach(() => localStorage.clear());

describe("usePromptAssist", () => {
  it("follows the shipped defaults, sends each row, and stores nothing untouched", () => {
    const look = open();
    expect(look.on.value).toBe(true);
    expect(look.fieldText("camera")).toBe("Phone snapshot");
    expect(look.fieldText("skin")).toBe("");
    // Leaving a field unchanged pins nothing.
    look.commit("camera", " Phone snapshot ");
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(look.request()).toEqual({
      context: "photo",
      choices: { camera: { id: "phone" }, skin: "none" },
    });
  });

  it("never-chosen follows a changed default; a chosen empty survives reload and it", () => {
    open().commit("skin", "Bare skin");
    open().commit("skin", "");
    // The next release moves Camera's default to film.
    const next = open(assist({ cameraDefault: "film" }));
    expect(next.fieldText("camera")).toBe("35 mm film");
    expect(next.fieldText("skin")).toBe("");
    expect(next.request().choices).toEqual({ camera: { id: "film" }, skin: "none" });
    expect(JSON.parse(localStorage.getItem(KEY)).minimax_h3_image.photo).toEqual({
      choices: { skin: "none" },
    });
  });

  it("an emptied row stays empty when its default changes", () => {
    open().commit("camera", "");
    expect(open(assist({ cameraDefault: "film" })).request().choices.camera).toBe("none");
  });

  it("resolves a preset's name or sentence to the preset, and keeps other text as typed", () => {
    const look = open();
    look.commit("camera", "35 MM FILM");
    expect(look.current("camera")).toEqual({ id: "film" });
    look.commit("camera", "Taken on a phone.");
    expect(look.current("camera")).toEqual({ id: "phone" });
    look.commit("camera", "  Old Pentax K1000 ");
    expect(look.request().choices.camera).toEqual({ text: "Old Pentax K1000" });
  });

  it("a typed text joins the entries once a run is sent, as the sheet's recents do", () => {
    // The sheet step's own typed values sit beside them, untouched.
    localStorage.setItem(RECENT, JSON.stringify({ size: "large", recent: { top: ["a coat"] } }));
    const look = open();
    look.commit("camera", "Old Pentax");
    expect(look.entries("camera")).toEqual([]);
    look.recordRun();
    expect(open().entries("camera")).toEqual(["Old Pentax"]);
    const stored = JSON.parse(localStorage.getItem(RECENT));
    expect(stored.recent).toEqual({
      top: ["a coat"],
      "photoLook:minimax_h3_image:photo:camera": ["Old Pentax"],
    });
    expect(stored.size).toBe("large");
    for (let n = 1; n <= 20; n += 1) {
      look.commit("camera", `Webcam ${n}`);
      look.recordRun();
    }
    look.commit("camera", "old pentax");
    look.recordRun();
    const kept = open().entries("camera");
    // RecentInput's cap, twenty.
    expect(kept).toHaveLength(20);
    // Used again: moved to the front, not doubled, and the oldest dropped.
    expect(kept[0]).toBe("old pentax");
    expect(kept.filter((e) => e.toLowerCase() === "old pentax")).toHaveLength(1);
    expect(kept).not.toContain("Webcam 1");
  });

  it("removing the entry a field shows empties it; Clear all empties the list", () => {
    const look = open();
    for (const text of ["Old Pentax", "Webcam"]) {
      look.commit("camera", text);
      look.recordRun();
    }
    look.remove("camera", "Old Pentax");
    expect(look.entries("camera")).toEqual(["Webcam"]);
    expect(look.fieldText("camera")).toBe("Webcam");
    look.remove("camera", "webcam");
    expect(look.fieldText("camera")).toBe("");
    look.commit("camera", "Old Pentax");
    look.recordRun();
    look.commit("camera", "Webcam");
    look.recordRun();
    look.clearAll("camera");
    expect(open().entries("camera")).toEqual([]);
  });

  it("Reset to defaults restores the rows and On, and keeps the entries", () => {
    const look = open();
    look.commit("camera", "Old Pentax");
    look.recordRun();
    look.setOn(false);
    expect(look.request()).toBeNull();
    expect(look.changed.value).toBe(true);
    look.reset();
    const again = open();
    expect(again.on.value).toBe(true);
    expect(again.fieldText("camera")).toBe("Phone snapshot");
    expect(again.entries("camera")).toEqual(["Old Pentax"]);
    expect(again.changed.value).toBe(false);
  });

  it("keeps the sheet's choices apart from the Run dialog's", () => {
    open(assist({ context: "sheet" }), "sheet").commit("camera", "35 mm film");
    open(assist({ context: "sheet" }), "sheet").setOn(false);
    const photo = open();
    expect(photo.fieldText("camera")).toBe("Phone snapshot");
    expect(photo.on.value).toBe(true);
    const sheet = open(assist({ context: "sheet" }), "sheet");
    expect(sheet.on.value).toBe(false);
    sheet.setOn(true);
    expect(sheet.request()).toEqual({
      context: "sheet",
      choices: { camera: { id: "film" }, skin: "none" },
    });
  });

  it("off by default where the server says so; turning it on is remembered per family", () => {
    const qwen = () => open({ ...assist({ defaultOn: false }), family: "qwen_image" });
    const look = qwen();
    expect(look.on.value).toBe(false);
    expect(look.request()).toBeNull();
    look.setOn(true);
    expect(qwen().on.value).toBe(true);
    expect(qwen().request()).not.toBeNull();
    // Another family is untouched, and back at the default nothing is stored.
    expect(open().on.value).toBe(true);
    qwen().setOn(false);
    expect(JSON.parse(localStorage.getItem(KEY)).qwen_image.photo).toEqual({});
  });

  it("a graph of no single family keeps its choices under the generic presets", () => {
    const generic = () => open({ ...assist(), family: null, presets: "_default" });
    generic().commit("camera", "");
    expect(generic().fieldText("camera")).toBe("");
    expect(JSON.parse(localStorage.getItem(KEY))._default.photo).toEqual({
      choices: { camera: "none" },
    });
  });

  it("survives storage that throws", () => {
    const broken = Storage.prototype.getItem;
    Storage.prototype.getItem = () => {
      throw new Error("blocked");
    };
    try {
      const look = open();
      expect(look.fieldText("camera")).toBe("Phone snapshot");
      look.commit("camera", "");
      expect(look.fieldText("camera")).toBe("");
    } finally {
      Storage.prototype.getItem = broken;
    }
  });
});
