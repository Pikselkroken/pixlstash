import { describe, it, expect } from "vitest";
import {
  candidateMegapixels,
  candidateSharpness,
  candidateSmartScore,
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
  candidateStackable,
  serverDetail,
  lockedPictureIds,
  lockedSets,
  mixedStackMembers,
  mixedStackEngineMarks,
  mixedStackPrimary,
  edgePercentText,
  lockedSetsSentence,
  isLockedRefusal,
  isMixedStackStackable,
  mixedStackLockedSets,
  mixedStackLockNote,
  mixedStackLockTitle,
  partialStackSentence,
  candidateBlockedBySets,
  lockedCandidateIds,
  RAW_COVER_BONUS,
  groupUnits,
  unitForPictureId,
  isUnitExcluded,
  includedUnits,
  unitCompositionLabel,
  stackVerdictLabel,
  candidateStackId,
  hasStrandedMember,
  flaggedStackIdSet,
  mixedStackTitle,
  mixedStackReason,
  mixedStackSuspects,
  DENSE_STACK_BADGE_BELOW_PX,
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

describe("utils/dedup - megapixels", () => {
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

describe("utils/dedup - the cover formula", () => {
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

describe("utils/dedup - compare highlighting", () => {
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

describe("utils/dedup - evidence and paths", () => {
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

describe("utils/dedup - confidence", () => {
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

describe("candidateSharpness", () => {
  // The server nulls missing/failed itself; the guard mirrors
  // candidateSmartScore's as a belt against older payloads.
  it("returns the metric only when it is displayable", () => {
    expect(candidateSharpness({ sharpness: 0.312 })).toBe(0.312);
    expect(candidateSharpness({ sharpness: 0 })).toBe(0);
    expect(candidateSharpness({ sharpness: null })).toBe(null);
    expect(candidateSharpness({ sharpness: -1.0 })).toBe(null);
    expect(candidateSharpness({})).toBe(null);
    expect(candidateSharpness(undefined)).toBe(null);
  });
});

describe("candidateSmartScore", () => {
  // NULL means not-yet-computed and -1.0 means computation failed; neither is
  // a number a person should read, so both come back as null and every
  // display simply omits the cell.
  it("returns the score only when it is displayable", () => {
    expect(candidateSmartScore({ smart_score: 3.7156 })).toBe(3.7156);
    expect(candidateSmartScore({ smart_score: 0 })).toBe(0);
    expect(candidateSmartScore({ smart_score: null })).toBe(null);
    expect(candidateSmartScore({ smart_score: -1.0 })).toBe(null);
    expect(candidateSmartScore({})).toBe(null);
    expect(candidateSmartScore(undefined)).toBe(null);
  });
});

describe("locked-set candidate helpers", () => {
  it("treats a missing stackable field as stackable", () => {
    // An older backend serves no `stackable`; defaulting to blocked would empty
    // every group on the queue.
    expect(candidateStackable({ picture_id: 1 })).toBe(true);
    expect(candidateStackable({ picture_id: 1, stackable: true })).toBe(true);
    expect(candidateStackable({ picture_id: 1, stackable: false })).toBe(false);
  });

  it("reads the blocking sets, tolerating an absent field", () => {
    expect(candidateBlockedBySets({ picture_id: 1 })).toEqual([]);
    expect(
      candidateBlockedBySets({
        picture_id: 1,
        blocked_by_sets: [{ id: 91, name: "Evaluation Set" }],
      }),
    ).toEqual([{ id: 91, name: "Evaluation Set" }]);
  });

  it("collects a group's locked candidate ids", () => {
    const group = {
      candidates: [
        { picture_id: 1, stackable: true },
        { picture_id: 2, stackable: false },
        { picture_id: 3 },
      ],
    };
    expect(lockedCandidateIds(group)).toEqual([2]);
    expect(lockedCandidateIds({ candidates: [] })).toEqual([]);
    expect(lockedCandidateIds(null)).toEqual([]);
  });
});

describe("verdict-refusal copy", () => {
  const reject = (detail) => ({ response: { data: { detail } } });

  it("builds a sentence from a structured locked-set refusal", () => {
    // The regression this exists for: the 423 detail is an OBJECT, and a
    // string-only reader dropped the one reason the user can act on.
    expect(
      serverDetail(
        reject({
          code: "set_locked",
          action: "stack duplicates together",
          sets: [{ id: 91, name: "Evaluation Set" }],
          picture_ids: [38025],
        }),
      ),
    ).toBe(
      "They are in the locked set 'Evaluation Set', which cannot gain or change members.",
    );
  });

  it("pluralises across several locked sets", () => {
    expect(
      serverDetail(
        reject({ code: "set_locked", sets: [{ name: "A" }, { name: "B" }] }),
      ),
    ).toContain("locked sets 'A, B'");
  });

  it("still quotes a plain string detail, and punctuates it", () => {
    expect(serverDetail(reject("a stack needs at least two pictures"))).toBe(
      "a stack needs at least two pictures.",
    );
    expect(serverDetail(reject("Already decided."))).toBe("Already decided.");
  });

  it("says nothing for a detail it does not understand", () => {
    expect(serverDetail(reject({ code: "something_else" }))).toBe("");
    expect(serverDetail(reject(["a", "b"]))).toBe("");
    expect(serverDetail(reject("   "))).toBe("");
    expect(serverDetail(undefined)).toBe("");
  });

  it("reads the picture ids a refusal named, for the flash", () => {
    expect(lockedPictureIds(reject({ picture_ids: [1, 2] }))).toEqual([1, 2]);
    expect(lockedPictureIds(reject({ code: "set_locked" }))).toEqual([]);
    expect(lockedPictureIds(undefined)).toEqual([]);
  });

  // The mixed-stack routes spell the same refusal `pictures_locked`. Reading
  // only the verdict routes' spelling is what makes half this surface fall
  // back to the generic sentence.
  it("recognises both spellings of the locked refusal", () => {
    expect(isLockedRefusal(reject({ code: "pictures_locked" }))).toBe(true);
    expect(isLockedRefusal(reject({ code: "set_locked" }))).toBe(true);
    expect(isLockedRefusal({ response: { status: 423, data: {} } })).toBe(true);
    expect(isLockedRefusal(reject("a stack needs two pictures"))).toBe(false);
    expect(isLockedRefusal(new Error("network"))).toBe(false);
    expect(isLockedRefusal(undefined)).toBe(false);
    expect(
      serverDetail(
        reject({ code: "pictures_locked", sets: [{ id: 3, name: "Frozen" }] }),
      ),
    ).toContain("locked set 'Frozen'");
  });

  // A 423 is fresher truth about a row than the page it was read from, so its
  // sets come back in the same shape `blocked_by_sets` arrives in.
  it("hands back the sets a refusal named, in the row's own shape", () => {
    expect(
      lockedSets(
        reject({ code: "pictures_locked", sets: [{ id: 3, name: "Frozen" }] }),
      ),
    ).toEqual([{ id: 3, name: "Frozen" }]);
    expect(lockedSets(reject({ code: "pictures_locked" }))).toEqual([]);
    expect(lockedSets(reject({ code: "something_else", sets: [{}] }))).toEqual(
      [],
    );
    expect(lockedSets(undefined)).toEqual([]);
  });

  it("words the sentence the same whether it came from a 423 or a row", () => {
    const sets = [{ id: 3, name: "Frozen" }];
    expect(lockedSetsSentence(sets)).toBe(
      serverDetail(reject({ code: "pictures_locked", sets })),
    );
    expect(lockedSetsSentence([])).toBe(
      "A locked set is freezing these pictures.",
    );
    expect(lockedSetsSentence(undefined)).toBe(
      "A locked set is freezing these pictures.",
    );
  });
});

describe("mixed-stack lock copy", () => {
  const frozen = (sets) => ({ stackable: false, blocked_by_sets: sets });

  // Only an explicit `false` blocks. Over-blocking a row the user could have
  // resolved is its own regression, and a silent one.
  it("treats only an explicit false as frozen", () => {
    expect(isMixedStackStackable({ stackable: true })).toBe(true);
    expect(isMixedStackStackable({})).toBe(true);
    expect(isMixedStackStackable(undefined)).toBe(true);
    expect(isMixedStackStackable({ stackable: false })).toBe(false);
    // A live row carries no lock copy at all, whatever else is on it.
    expect(mixedStackLockedSets({ blocked_by_sets: [{ name: "X" }] })).toEqual(
      [],
    );
    expect(mixedStackLockNote({ stackable: true })).toBe("");
    expect(mixedStackLockTitle({ stackable: true })).toBe("");
  });

  it("names the set, and every set, in the row's note", () => {
    expect(mixedStackLockNote(frozen([{ id: 3, name: "Frozen" }]))).toBe(
      "Frozen by locked set 'Frozen'",
    );
    expect(
      mixedStackLockNote(frozen([{ name: "Frozen" }, { name: "Archive" }])),
    ).toBe("Frozen by locked sets 'Frozen, Archive'");
    expect(mixedStackLockNote(frozen([]))).toBe("Frozen by a locked set");
    expect(mixedStackLockNote({ stackable: false })).toBe(
      "Frozen by a locked set",
    );
  });

  // Cause, then remedy: the set is the thing the user has to go and unlock, and
  // a blocked control with no way past it is a dead end.
  it("states the cause and the remedy in the tooltip", () => {
    const one = mixedStackLockTitle(frozen([{ name: "Frozen" }]));
    expect(one).toContain("the locked set 'Frozen'");
    expect(one).toContain("Unlock it to change this stack.");
    const many = mixedStackLockTitle(
      frozen([{ name: "Frozen" }, { name: "Archive" }]),
    );
    expect(many).toContain("the locked sets 'Frozen, Archive'");
    expect(many).toContain("Unlock them");
    expect(mixedStackLockTitle(frozen([]))).toContain("a locked set");
  });

  it("summarises a partial stack in one sentence", () => {
    expect(
      partialStackSentence(
        [
          {
            picture_id: 38025,
            reason: "set_locked",
            sets: [{ id: 91, name: "Evaluation Set" }],
          },
        ],
        3,
      ),
    ).toBe("Stacked 3; 1 picture stayed out (locked set 'Evaluation Set').");
  });

  it("pluralises the held-back count and stays silent when nothing was skipped", () => {
    expect(
      partialStackSentence(
        [
          { picture_id: 1, sets: [{ name: "A" }] },
          { picture_id: 2, sets: [{ name: "B" }] },
        ],
        5,
      ),
    ).toBe("Stacked 5; 2 pictures stayed out (locked sets 'A, B').");
    expect(partialStackSentence([], 4)).toBe("");
    expect(partialStackSentence(undefined, 4)).toBe("");
  });
});

// --- The unit model ---------------------------------------------------------
//
// A stack verdict moves whole STACKS, so the queue's smallest addressable thing
// is a unit: a loose picture, or a whole existing stack collapsed into one deck.
// The case that makes this load-bearing rather than tidy is the common one, a
// group that names ONE member of a four-deep stack, where a client sizing the
// deck from `candidates` draws a single picture and then silently moves four.

/** A group naming one member of a 4-stack plus two loose pictures. */
function mixedGroup() {
  return {
    signature: "mixed",
    cover_picture_id: 700,
    candidates: [
      { picture_id: 503, stack_id: 12, thumbnail_version: "a" },
      { picture_id: 700, thumbnail_version: "b" },
      { picture_id: 701, thumbnail_version: "c" },
    ],
    stacks: {
      12: {
        stack_id: 12,
        member_count: 4,
        leader_picture_id: 501,
        leader_thumbnail_version: "1024x768",
        matched_picture_ids: [503],
        stackable: true,
        blocked_by_sets: [],
      },
    },
  };
}

describe("groupUnits: the partition", () => {
  it("reads a candidate's stack id, or null when it has none", () => {
    expect(candidateStackId({ stack_id: 12 })).toBe(12);
    expect(candidateStackId({ stackId: 12 })).toBe(12);
    expect(candidateStackId({ stack_id: null })).toBeNull();
    expect(candidateStackId({})).toBeNull();
    expect(candidateStackId(undefined)).toBeNull();
  });

  // THE case: the group names one member, the deck stands for all four, and
  // the face is the LEADER, which is not the matched member.
  it("sizes a deck from the stack's live depth, not from the group's members", () => {
    const units = groupUnits(mixedGroup());
    expect(units).toHaveLength(3);

    const [deck, first, second] = units;
    expect(deck.kind).toBe("deck");
    expect(deck.stackId).toBe(12);
    expect(deck.depth).toBe(4);
    expect(deck.matchedCount).toBe(1);
    // The face is the leader, which the group never names as a candidate.
    expect(deck.coverPictureId).toBe(501);
    expect(deck.face).toBeNull();
    expect(deck.thumbnailVersion).toBe("1024x768");
    // Only the matched member is a group candidate the verdict can address.
    expect(deck.pictureIds).toEqual([503]);

    expect(first.kind).toBe("picture");
    expect(first.coverPictureId).toBe(700);
    expect(second.coverPictureId).toBe(701);
  });

  it("collapses every candidate sharing a stack id into one unit, in place", () => {
    const group = {
      candidates: [
        { picture_id: 1 },
        { picture_id: 2, stack_id: 9 },
        { picture_id: 3, stack_id: 9 },
        { picture_id: 4 },
      ],
      stacks: { 9: { stack_id: 9, member_count: 3, leader_picture_id: 2 } },
    };
    const units = groupUnits(group);
    // The stack's FIRST candidate holds its place in the strip; the second
    // folds in rather than taking a slot of its own.
    expect(units.map((u) => u.kind)).toEqual(["picture", "deck", "picture"]);
    expect(units[1].pictureIds).toEqual([2, 3]);
    expect(units[1].depth).toBe(3);
    expect(units[1].matchedCount).toBe(2);
    // The leader IS a candidate here, so the deck can draw its metadata.
    expect(units[1].face).toEqual({ picture_id: 2, stack_id: 9 });
  });

  // An older backend serves no `stacks` block. Collapsing by stack_id still
  // works; the depth degrades to what the group can see rather than to nothing.
  it("degrades to the matched count when the payload cannot size the stack", () => {
    const units = groupUnits({
      candidates: [
        { picture_id: 2, stack_id: 9 },
        { picture_id: 3, stack_id: 9 },
      ],
    });
    expect(units).toHaveLength(1);
    expect(units[0].kind).toBe("deck");
    expect(units[0].depth).toBe(2);
    expect(units[0].coverPictureId).toBe(2);
  });

  // A "stack" the payload sizes at one picture is not a stack; drawing edge
  // ticks and a count badge for it would be a lie about the library.
  it("renders a one-deep stack as a plain picture", () => {
    const units = groupUnits({
      candidates: [{ picture_id: 2, stack_id: 9 }, { picture_id: 3 }],
      stacks: { 9: { stack_id: 9, member_count: 1, leader_picture_id: 2 } },
    });
    expect(units[0].kind).toBe("picture");
    expect(units[0].depth).toBe(1);
  });

  it("survives an empty or absent group", () => {
    expect(groupUnits(null)).toEqual([]);
    expect(groupUnits({})).toEqual([]);
  });
});

describe("groupUnits: the lock rollup", () => {
  // A locked set freezes a WHOLE stack, including members outside the group,
  // so the deck's own `stackable` is the answer even when every visible
  // candidate says it is free.
  it("takes the served unit-level rollup over the candidates' own flags", () => {
    const units = groupUnits({
      candidates: [{ picture_id: 503, stack_id: 12, stackable: true }],
      stacks: {
        12: {
          stack_id: 12,
          member_count: 4,
          leader_picture_id: 501,
          stackable: false,
          blocked_by_sets: [{ id: 7, name: "Portfolio" }],
        },
      },
    });
    expect(units[0].stackable).toBe(false);
    expect(units[0].blockedBySets).toEqual([{ id: 7, name: "Portfolio" }]);
  });

  // The belt: a payload that predates the rollup still blocks a deck whose
  // visible member is frozen, rather than sending it into a refusal.
  it("still blocks a deck whose visible member is frozen", () => {
    const units = groupUnits({
      candidates: [
        {
          picture_id: 503,
          stack_id: 12,
          stackable: false,
          blocked_by_sets: [{ id: 3, name: "Prints" }],
        },
      ],
      stacks: { 12: { stack_id: 12, member_count: 4, leader_picture_id: 501 } },
    });
    expect(units[0].stackable).toBe(false);
    expect(units[0].blockedBySets).toEqual([{ id: 3, name: "Prints" }]);
  });
});

describe("unitForPictureId / isUnitExcluded / includedUnits", () => {
  const units = groupUnits(mixedGroup());

  // The leader is frequently not a group member, and it is what a cover choice
  // resolves to, so the deck has to answer to it as well as to its members.
  it("finds a deck by its matched member AND by its leader", () => {
    expect(unitForPictureId(units, 503)).toBe(units[0]);
    expect(unitForPictureId(units, 501)).toBe(units[0]);
    expect(unitForPictureId(units, 700)).toBe(units[1]);
    expect(unitForPictureId(units, 9999)).toBeNull();
    expect(unitForPictureId(units, null)).toBeNull();
  });

  it("reads a unit as out only when every picture it stands for is out", () => {
    expect(isUnitExcluded(units[0], [503])).toBe(true);
    expect(isUnitExcluded(units[0], [700])).toBe(false);
    expect(isUnitExcluded(units[1], [])).toBe(false);
  });

  it("counts included units, dropping the excluded and the frozen", () => {
    expect(includedUnits(units, [])).toHaveLength(3);
    expect(includedUnits(units, [503])).toHaveLength(2);
    const frozen = groupUnits({
      candidates: [{ picture_id: 1, stackable: false }, { picture_id: 2 }],
    });
    expect(includedUnits(frozen, [])).toHaveLength(1);
  });
});

describe("unitCompositionLabel: what the header says", () => {
  it("keeps the plain picture count when nothing is stacked", () => {
    expect(
      unitCompositionLabel(
        groupUnits({
          candidates: [{ picture_id: 1 }, { picture_id: 2 }, { picture_id: 3 }],
        }),
      ),
    ).toBe("3 pictures");
    expect(
      unitCompositionLabel(
        groupUnits({
          candidates: [{ picture_id: 1 }],
        }),
      ),
    ).toBe("1 picture");
  });

  it("names a deck and the strays beside it", () => {
    const group = mixedGroup();
    group.candidates = group.candidates.slice(0, 2);
    expect(unitCompositionLabel(groupUnits(group))).toBe(
      "Stack of 4 + 1 picture",
    );
    expect(unitCompositionLabel(groupUnits(mixedGroup()))).toBe(
      "Stack of 4 + 2 pictures",
    );
  });

  it("names two decks", () => {
    const units = groupUnits({
      candidates: [
        { picture_id: 1, stack_id: 12 },
        { picture_id: 2, stack_id: 13 },
      ],
      stacks: {
        12: { stack_id: 12, member_count: 5, leader_picture_id: 1 },
        13: { stack_id: 13, member_count: 3, leader_picture_id: 2 },
      },
    });
    expect(unitCompositionLabel(units)).toBe("Stack of 5 + stack of 3");
  });
});

describe("stackVerdictLabel: the button names its outcome", () => {
  const deck = (depth, id) => ({ kind: "deck", depth, coverPictureId: id });
  const loose = () => ({ kind: "picture", depth: 1 });

  it("says Stack N when every unit is a loose picture", () => {
    const label = stackVerdictLabel([loose(), loose(), loose()]);
    expect(label.full).toBe("Stack 3");
    // Nothing to shed, so it must never be given the classes that hide it.
    expect(label.degrades).toBe(false);
    expect(label.short).toBe("Stack 3");
  });

  it("says Add N to stack of M for a deck beside loose pictures", () => {
    const label = stackVerdictLabel([deck(4, 501), loose()]);
    expect(label.full).toBe("Add 1 to stack of 4");
    expect(label.mid).toBe("Add 1 to stack");
    expect(label.short).toBe("Add 1");
    expect(label.degrades).toBe(true);
  });

  it("says Merge N stacks for two decks", () => {
    const label = stackVerdictLabel([deck(5, 1), deck(3, 2)]);
    expect(label.full).toBe("Merge 2 stacks");
    expect(label.degrades).toBe(false);
  });

  // 11 of 1,726 unresolved groups on a real library are two stacks WITH a loose
  // picture alongside. "Merge 2 stacks" would move three things while naming
  // two, which is the class of lie this labelling exists to remove, rarity is
  // not a reason to tolerate it.
  it("names the loose pictures that fold in alongside a merge", () => {
    const label = stackVerdictLabel([deck(5, 1), deck(3, 2), loose()]);
    expect(label.full).toBe("Merge 2 stacks + 1 picture");
    expect(label.mid).toBe("Merge 2 stacks");
    expect(label.short).toBe("Merge");
    expect(label.degrades).toBe(true);
  });

  it("pluralises the loose pictures folded into a merge", () => {
    const label = stackVerdictLabel([deck(5, 1), deck(3, 2), loose(), loose()]);
    expect(label.full).toBe("Merge 2 stacks + 2 pictures");
  });

  // A group of one deck and nothing else poses no decision and is filtered out
  // of the queue upstream; the label must still be a sentence rather than throw.
  it("falls back to the plain count for a degenerate group", () => {
    expect(stackVerdictLabel([deck(4, 1)]).full).toBe("Stack 1");
    expect(stackVerdictLabel([]).full).toBe("Stack 0");
  });
});

// --- Mixed stacks (design D5) ------------------------------------------------

/**
 * One `MixedStackModel` row, in the backend's shape.
 * @param {Object} over - fields to override.
 */
function mixed(over = {}) {
  return {
    stack_id: 42,
    threshold: 0.9,
    member_count: 5,
    member_ids: [7, 8, 9, 10, 11],
    membership_fingerprint: "abc",
    component_count: 2,
    component_sizes: [4, 1],
    components: [[7, 8, 9, 10], [11]],
    largest_component_size: 4,
    stranded_picture_ids: [11],
    weakest_edge: 0.9,
    unhashed_picture_ids: [],
    suggested_action: "split",
    kept: false,
    leader_picture_id: 7,
    leader_thumbnail_version: null,
    ...over,
  };
}

describe("utils/dedup: the strong case, which is the only one marked", () => {
  // The whole reason the soft cases stay off the tiles: at the measured 12% a
  // mark is one tile in eight and stops being a warning at all.
  it("calls a stack strong only when a member is joined to nothing", () => {
    expect(hasStrandedMember(mixed())).toBe(true);
    expect(hasStrandedMember(mixed({ stranded_picture_ids: [] }))).toBe(false);
    expect(hasStrandedMember(undefined)).toBe(false);
  });

  // The flag set is keyed on STRINGS because the queue's deck reads its id out
  // of a different payload; comparing a number to a string silently flags
  // nothing at all.
  it("collects only the strong cases, as strings", () => {
    const flags = flaggedStackIdSet([
      mixed({ stack_id: 1 }),
      mixed({ stack_id: 2, stranded_picture_ids: [] }),
      mixed({ stack_id: 3, stranded_picture_ids: [4, 5] }),
    ]);
    expect([...flags].sort()).toEqual(["1", "3"]);
    expect(flags.has("2")).toBe(false);
  });

  it("survives an absent list", () => {
    expect(flaggedStackIdSet(undefined).size).toBe(0);
  });
});

describe("utils/dedup: what a mixed-stack row says", () => {
  // The same noun phrase the queue's deck uses, so the same stack reads the
  // same way wherever the user meets it.
  it("titles the row with the stack's live size", () => {
    expect(mixedStackTitle(mixed({ member_count: 12 }))).toBe("Stack of 12");
  });

  it("names the strangers in the strong case, and pluralises them", () => {
    expect(mixedStackReason(mixed())).toBe("1 picture doesn't match the rest");
    expect(mixedStackReason(mixed({ stranded_picture_ids: [11, 12] }))).toBe(
      "2 pictures don't match the rest",
    );
  });

  // The soft case blames nobody, because there is no single member the data
  // supports blaming.
  it("blames nobody in the soft case", () => {
    const soft = mixed({
      stranded_picture_ids: [],
      component_count: 2,
      components: [
        [7, 8],
        [9, 10],
      ],
    });
    expect(mixedStackReason(soft)).toBe("These don't all match");
  });

  it("counts the clusters when there are more than two of them", () => {
    const soft = mixed({
      stranded_picture_ids: [],
      component_count: 3,
      components: [
        [7, 8],
        [9, 10],
        [11, 12],
      ],
    });
    expect(mixedStackReason(soft)).toBe(
      "These don't all match: 3 groups that don't overlap",
    );
  });
});

describe("utils/dedup: the suspects a row shows", () => {
  it("leads with the stranded members", () => {
    expect(mixedStackSuspects(mixed())).toEqual([11]);
  });

  // The majority is what SURVIVES a split. Showing four of its members behind
  // a warning border would accuse the wrong pictures.
  it("never shows the majority cluster in the soft case", () => {
    const soft = mixed({
      stranded_picture_ids: [],
      member_count: 6,
      components: [
        [1, 2, 3, 4],
        [5, 6],
      ],
      largest_component_size: 4,
    });
    expect(mixedStackSuspects(soft)).toEqual([5, 6]);
  });

  it("caps the run and never repeats a picture", () => {
    const wide = mixed({
      stranded_picture_ids: [1, 2, 3],
      components: [[9], [1], [2], [3]],
    });
    expect(mixedStackSuspects(wide, 2)).toEqual([1, 2]);
    expect(new Set(mixedStackSuspects(wide)).size).toBe(
      mixedStackSuspects(wide).length,
    );
  });
});

describe("utils/dedup: the dense badge threshold", () => {
  // 168px is the `small` rung of the shared thumbnail ladder, not a number
  // invented here: the badge inverts exactly where the tile stops having room
  // for both a glyph and a numeral.
  it("is the ladder's small rung", () => {
    expect(DENSE_STACK_BADGE_BELOW_PX).toBe(168);
  });
});

// --- The Mixed stacks QUEUE ---------------------------------------------------

describe("utils/dedup: a mixed stack's members", () => {
  // `member_edges` is parallel to `member_ids`, and it is keyed by picture
  // rather than trusted positionally: a payload that lost the pairing must
  // degrade to "no edge known" rather than to another member's number.
  it("pairs each member with its own strongest edge", () => {
    const members = mixedStackMembers(
      mixed({
        member_edges: [
          {
            picture_id: 8,
            strongest_edge: 0.97,
            closest_picture_id: 9,
            nearest_edge: 0.97,
            nearest_picture_id: 9,
          },
          {
            picture_id: 7,
            strongest_edge: 0.94,
            closest_picture_id: 8,
            nearest_edge: 0.94,
            nearest_picture_id: 8,
          },
          {
            picture_id: 11,
            strongest_edge: null,
            closest_picture_id: null,
            nearest_edge: 0.89,
            nearest_picture_id: 8,
          },
        ],
      }),
    );
    expect(members.map((m) => m.pictureId)).toEqual([7, 8, 9, 10, 11]);
    expect(members[0].strongestEdge).toBe(0.94);
    expect(members[0].closestPictureId).toBe(8);
    expect(members[1].strongestEdge).toBe(0.97);
    // A member the payload names no edge for is not a member with a 0% edge.
    expect(members[2].strongestEdge).toBeNull();
    expect(members[4].strongestEdge).toBeNull();
    expect(members[4].stranded).toBe(true);
  });

  // The two numbers are NOT interchangeable and this is the case that proves
  // it: the stranger has no surviving edge (the verdict) and a measured 89% to
  // its closest sibling (the truth). Collapsing them is what made the page
  // print a dash and say the picture matched nothing.
  it("carries the unconditional closest match beside the thresholded one", () => {
    const members = mixedStackMembers(
      mixed({
        member_edges: [
          {
            picture_id: 11,
            strongest_edge: null,
            closest_picture_id: null,
            nearest_edge: 0.89,
            nearest_picture_id: 8,
          },
        ],
      }),
    );
    const stranger = members.find((m) => m.pictureId === 11);
    expect(stranger.strongestEdge).toBeNull();
    expect(stranger.nearestEdge).toBe(0.89);
    expect(stranger.nearestPictureId).toBe(8);
    // And nothing to compare against stays an absence, not a zero.
    expect(members[0].nearestEdge).toBeNull();
    expect(members[0].nearestPictureId).toBeNull();
  });

  it("degrades to no edges at all on a payload that carries none", () => {
    const members = mixedStackMembers(mixed());
    expect(members).toHaveLength(5);
    expect(members.every((m) => m.strongestEdge === null)).toBe(true);
  });

  it("marks the not-yet-analysed members as such, not as strangers", () => {
    const members = mixedStackMembers(
      mixed({ unhashed_picture_ids: [10], stranded_picture_ids: [10, 11] }),
    );
    expect(members.find((m) => m.pictureId === 10).unhashed).toBe(true);
    expect(members.find((m) => m.pictureId === 11).unhashed).toBe(false);
  });
});

describe("utils/dedup: the marks a row opens with", () => {
  it("takes the engine's strangers", () => {
    expect(mixedStackEngineMarks(mixed())).toEqual([11]);
  });

  // The one false positive this feature cannot afford: a member with no hash
  // can carry no edge, so the cohesion fold necessarily lists it as stranded.
  // Marking it would report "this does not belong" about a picture nothing has
  // compared yet. Same subtraction the backend's own evidence pills make.
  it("never pre-marks a picture that has not been analysed yet", () => {
    const marks = mixedStackEngineMarks(
      mixed({ stranded_picture_ids: [10, 11], unhashed_picture_ids: [10] }),
    );
    expect(marks).toEqual([11]);
  });

  it("falls back to the non-majority clusters when nobody is stranded", () => {
    const marks = mixedStackEngineMarks(
      mixed({
        stranded_picture_ids: [],
        member_ids: [1, 2, 3, 4, 5, 6],
        member_count: 6,
        components: [
          [1, 2, 3, 4],
          [5, 6],
        ],
        largest_component_size: 4,
      }),
    );
    expect(marks).toEqual([5, 6]);
  });
});

describe("utils/dedup: mixedStackSuspects matches its own docstring", () => {
  // The bug: the two readings are ALTERNATIVES, and the shipped code appended
  // the non-largest components unconditionally. A stack with one lone stranger
  // and a legitimate pair therefore accused the pair as well, and this list is
  // now what opens marked on the row, so a wrong id here is a wrong id in the
  // request.
  it("does not append whole clusters once a member is already stranded", () => {
    const both = mixed({
      member_ids: [1, 2, 3, 4, 5],
      member_count: 5,
      stranded_picture_ids: [5],
      components: [[1, 2, 3], [4], [5]],
      largest_component_size: 3,
    });
    expect(mixedStackSuspects(both)).toEqual([5]);
  });
});

describe("utils/dedup: what the primary button names", () => {
  it("splits the marked members while a majority survives", () => {
    const plan = mixedStackPrimary(mixed(), [11]);
    expect(plan.action).toBe("split");
    expect(plan.label).toBe("Split off 1");
    expect(plan.icon).toBe("call-split");
    expect(plan.pictureIds).toEqual([11]);
  });

  it("counts the marks, not the engine's opinion", () => {
    expect(mixedStackPrimary(mixed(), [9, 10, 11]).label).toBe("Split off 3");
  });

  // The dissolve boundary, in both directions. A stack needs two members, so
  // marks that would leave one dissolve it; the server applies the same floor,
  // and the button has to say so BEFORE the press rather than after it.
  it("flips to unstack the moment fewer than two would be left", () => {
    const row = mixed();
    expect(mixedStackPrimary(row, [9, 10, 11]).action).toBe("split");
    const over = mixedStackPrimary(row, [8, 9, 10, 11]);
    expect(over.action).toBe("unstack");
    expect(over.label).toBe("Unstack all 5");
    expect(over.icon).toBe("layers-off");
    // Every live member travels, because one call carries both outcomes.
    expect(over.pictureIds).toEqual([7, 8, 9, 10, 11]);
    // And back again.
    expect(mixedStackPrimary(row, [9, 10, 11]).label).toBe("Split off 3");
  });

  // "Split off 0" is a button that cannot do anything, and the server 400s an
  // empty list. Nothing marked means the only outcome left is to free the lot.
  it("names the unstack when nothing is marked at all", () => {
    const plan = mixedStackPrimary(mixed(), []);
    expect(plan.action).toBe("unstack");
    expect(plan.label).toBe("Unstack all 5");
    expect(plan.pictureIds).toHaveLength(5);
  });

  it("ignores a mark that is not a member of this stack", () => {
    expect(mixedStackPrimary(mixed(), [11, 9999]).label).toBe("Split off 1");
  });
});

describe("utils/dedup: one member's strongest match", () => {
  it("reads as a whole percentage", () => {
    expect(edgePercentText(0.90625)).toBe("91%");
    expect(edgePercentText(1)).toBe("100%");
  });

  // The en dash, never a zero: "0%" is a measured similarity and this is the
  // absence of one, which is precisely what the column exists to show.
  it("says the absence of an edge with the en dash", () => {
    expect(edgePercentText(null)).toBe("–");
    expect(edgePercentText(undefined)).toBe("–");
  });
});
