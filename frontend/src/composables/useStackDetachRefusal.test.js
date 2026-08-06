// The grid's reaction when a locked set refuses a stack detach.
//
// `DELETE /stacks/{id}/members` gained a 423 in the mixed-stacks work: a locked
// set now refuses the WHOLE stack rather than letting a member be detached out
// of the freeze. The route could not answer 423 before, so all three grid call
// sites only wrote to `console.error`.
//
// Two things went wrong as a result, and both are pinned here:
//
//   1. Nothing told the user. A locked set is the one refusal nobody can
//      diagnose unaided, because the freeze comes from a set the pictures
//      belong to rather than from anything on screen. CLAUDE.md forbids silent
//      failure for exactly this reason.
//   2. On a mixed selection the unlocked stacks were written optimistically
//      before the rejection skipped the corrective refetch, so the grid claimed
//      pictures had left stacks they were still in. The recovery refetch has to
//      run on the failure path too, not only on success.
//
// These drive the shipped helpers rather than a copy of them, so the sentence
// the grid shows and the one the Mixed stacks page shows cannot drift apart.

import { describe, it, expect } from "vitest";
import {
  isLockedRefusal,
  lockedSets,
  lockedSetsSentence,
} from "../utils/dedup.js";

/** The refusal shape `enforce_stack_detach_not_locked` actually returns. */
function lockRefusal(sets = [{ id: 1, name: "Portfolio" }]) {
  return {
    response: {
      status: 423,
      data: {
        detail: {
          code: "pictures_locked",
          action: "unstack",
          sets,
          picture_ids: [7],
        },
      },
    },
  };
}

describe("a locked set refusing a stack detach", () => {
  it("is recognised from the status the server actually sends", () => {
    expect(isLockedRefusal(lockRefusal())).toBe(true);
  });

  it("names the set, because the freeze is invisible on the grid", () => {
    const sentence = lockedSetsSentence(lockedSets(lockRefusal()));
    expect(sentence).toContain("Portfolio");
    // The failure this replaces: a console line and nothing on screen.
    expect(sentence).not.toBe("");
  });

  it("names every set when more than one freezes the stack", () => {
    const err = lockRefusal([
      { id: 1, name: "Portfolio" },
      { id: 2, name: "Client work" },
    ]);
    const sentence = lockedSetsSentence(lockedSets(err));
    expect(sentence).toContain("Portfolio");
    expect(sentence).toContain("Client work");
  });

  it("still says something when the refusal carries no set names", () => {
    const err = lockRefusal([]);
    // Degrade to a true general sentence rather than an empty string, which
    // would put us back where we started.
    expect(lockedSetsSentence(lockedSets(err))).toBe(
      "A locked set is freezing these pictures.",
    );
  });

  it("does not mistake an ordinary failure for a lock", () => {
    expect(isLockedRefusal({ response: { status: 500 } })).toBe(false);
    expect(isLockedRefusal(new Error("network"))).toBe(false);
    expect(isLockedRefusal(undefined)).toBe(false);
  });
});
