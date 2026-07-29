import { describe, it, expect } from "vitest";
import {
  candidateMegapixels,
  isRawCandidate,
  coverScore,
  pickCoverIndex,
  suggestedCoverId,
  bestOf,
  orderEvidence,
  shortenPath,
  showsPath,
  confidenceLabel,
  candidateSizeMb,
  evidenceLabel,
  RAW_COVER_BONUS,
} from "./dedup";

const candidate = (over = {}) => ({
  id: 1,
  width: 4000,
  height: 3000,
  tag_count: 0,
  score: 0,
  format: "JPEG",
  captured_at: "2026-05-12T14:22:00Z",
  ...over,
});

describe("utils/dedup — megapixels", () => {
  it("derives megapixels from the dimensions", () => {
    expect(candidateMegapixels(candidate({ width: 6000, height: 4000 }))).toBe(
      24,
    );
  });

  it("prefers an explicit megapixel field", () => {
    expect(candidateMegapixels(candidate({ megapixels: 12.2 }))).toBe(12.2);
  });

  it("returns 0 for unknown dimensions rather than NaN", () => {
    expect(candidateMegapixels({ width: null, height: null })).toBe(0);
    expect(candidateMegapixels(null)).toBe(0);
  });
});

describe("utils/dedup — the cover formula", () => {
  it("scores pixels x4 + tags x3 + score x2", () => {
    const c = candidate({ width: 1000, height: 1000, tag_count: 2, score: 3 });
    // 1 MP -> 4, 2 tags -> 6, score 3 -> 6
    expect(coverScore(c)).toBeCloseTo(16);
  });

  it("adds the RAW bonus", () => {
    const jpeg = candidate({ width: 1000, height: 1000 });
    const raw = candidate({ width: 1000, height: 1000, format: "ARW" });
    expect(isRawCandidate(raw)).toBe(true);
    expect(coverScore(raw) - coverScore(jpeg)).toBe(RAW_COVER_BONUS);
  });

  it("picks the higher-scoring candidate", () => {
    const list = [
      candidate({ picture_id: 1, width: 1920, height: 1440 }),
      candidate({ picture_id: 2, width: 4032, height: 3024, tag_count: 4 }),
    ];
    expect(pickCoverIndex(list)).toBe(1);
  });

  // The original beats the copy that was made from it.
  it("breaks a tie to the oldest capture time", () => {
    const list = [
      candidate({ picture_id: 1, captured_at: "2026-06-11T18:40:00Z" }),
      candidate({ picture_id: 2, captured_at: "2026-06-03T09:11:00Z" }),
    ];
    expect(pickCoverIndex(list)).toBe(1);
  });

  it("keeps the first candidate when a tie has no usable dates", () => {
    const list = [
      candidate({ picture_id: 1, captured_at: null, created_at: null }),
      candidate({ picture_id: 2, captured_at: null, created_at: null }),
    ];
    expect(pickCoverIndex(list)).toBe(0);
  });

  it("returns -1 for an empty candidate list", () => {
    expect(pickCoverIndex([])).toBe(-1);
    expect(pickCoverIndex(null)).toBe(-1);
  });

  // The backend runs the same formula; its answer is authoritative so the queue
  // and a later rescan cannot disagree about the cover.
  it("suggestedCoverId honours the server preselection", () => {
    const group = {
      cover_picture_id: 99,
      candidates: [candidate({ picture_id: 1 }), candidate({ picture_id: 2 })],
    };
    expect(suggestedCoverId(group)).toBe(99);
  });

  it("suggestedCoverId falls back to the local formula", () => {
    const group = {
      candidates: [
        candidate({ picture_id: 1, width: 1000, height: 1000 }),
        candidate({ picture_id: 2, width: 6000, height: 4000 }),
      ],
    };
    expect(suggestedCoverId(group)).toBe(2);
  });

  it("suggestedCoverId is null for a group with no candidates", () => {
    expect(suggestedCoverId({ candidates: [] })).toBe(null);
    expect(suggestedCoverId(null)).toBe(null);
  });
});

describe("utils/dedup — compare highlighting", () => {
  it("bestOf returns the maximum of a read field", () => {
    const list = [{ size: 8.4 }, { size: 1.1 }, { size: 12.6 }];
    expect(bestOf(list, (c) => c.size)).toBe(12.6);
  });

  it("bestOf ignores unusable values", () => {
    const list = [{ size: null }, { size: "nope" }, { size: 3 }];
    expect(bestOf(list, (c) => c.size)).toBe(3);
    expect(bestOf([], (c) => c.size)).toBe(0);
  });
});

describe("utils/dedup — evidence and paths", () => {
  // Counter-evidence first, because a collapsed row only has room for two pills
  // and the warning is the half that matters.
  it("orderEvidence puts counter-evidence first", () => {
    const why = [
      { label: "96% visual match" },
      { label: "Different resolution", against: true },
      { label: "Same capture second" },
      { label: "One is a re-export", against: true },
    ];
    expect(orderEvidence(why).map((w) => w.label)).toEqual([
      "Different resolution",
      "One is a re-export",
      "96% visual match",
      "Same capture second",
    ]);
  });

  it("orderEvidence tolerates a missing list", () => {
    expect(orderEvidence(undefined)).toEqual([]);
  });

  it("shortenPath keeps the last two segments", () => {
    expect(shortenPath("/shoots/may/june/DSC_4417.jpg")).toBe(
      "…/june/DSC_4417.jpg",
    );
  });

  it("shortenPath leaves a short path alone", () => {
    expect(shortenPath("/may/DSC_4417.jpg")).toBe("/may/DSC_4417.jpg");
    expect(shortenPath("")).toBe("");
  });

  // A managed library picture's path is an implementation detail; only a
  // reference-folder picture needs it to tell the copies apart.
  it("showsPath is true only for a reference-folder picture with a path", () => {
    expect(showsPath({ reference_folder_id: 3, file_path: "/a/b.jpg" })).toBe(
      true,
    );
    // The server sends no path for a managed picture, and a stray one must not
    // start leaking the library's layout into the UI either.
    expect(
      showsPath({ reference_folder_id: null, file_path: "/a/b.jpg" }),
    ).toBe(false);
    expect(showsPath({ reference_folder_id: 3, file_path: null })).toBe(false);
  });

  it("candidateSizeMb converts the stored byte count", () => {
    expect(candidateSizeMb({ size_bytes: 8400000 })).toBeCloseTo(8.4);
    expect(candidateSizeMb({ size_bytes: null })).toBe(0);
  });

  it("evidenceLabel reads the backend's pill text", () => {
    expect(evidenceLabel({ text: "Identical file hash" })).toBe(
      "Identical file hash",
    );
    expect(evidenceLabel(null)).toBe("");
  });
});

describe("utils/dedup — confidence", () => {
  // "Exact" is a different claim from "100% similar"; blurring them makes a
  // near-duplicate suggestion look more certain than it is.
  it("labels the exact tier distinctly", () => {
    expect(confidenceLabel({ kind: "exact", confidence: 1 })).toEqual({
      exact: true,
      label: "Exact",
    });
  });

  it("labels a near tier as a rounded percentage", () => {
    expect(confidenceLabel({ kind: "near", confidence: 0.964 })).toEqual({
      exact: false,
      label: "96% similar",
    });
  });

  it("falls back when the confidence is missing", () => {
    expect(confidenceLabel({ kind: "near" }).label).toBe("Similar");
  });
});
