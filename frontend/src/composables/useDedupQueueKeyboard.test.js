import { describe, it, expect, beforeEach, vi } from "vitest";
import { createDedupKeyHandler } from "./useDedupQueueKeyboard";

/**
 * A stand-in with the same surface as the dedup store, so the key model can be
 * exercised without Pinia, without the network, and without a mounted view.
 */
function makeStore() {
  return {
    groups: [
      {
        signature: "g1",
        candidates: [{ picture_id: 1 }, { picture_id: 2 }, { picture_id: 3 }],
      },
      { signature: "g2", candidates: [{ picture_id: 4 }, { picture_id: 5 }] },
    ],
    focusIndex: 0,
    busy: false,
    get focusedGroup() {
      return this.groups[this.focusIndex] ?? null;
    },
    focusNext: vi.fn(),
    focusPrev: vi.fn(),
    setFocus: vi.fn(),
    focusStart: vi.fn(),
    focusEnd: vi.fn(),
    setCover: vi.fn(),
    toggleExcluded: vi.fn(),
    coverIdFor: vi.fn(() => 2),
    stack: vi.fn(),
    keepSeparate: vi.fn(),
  };
}

function keyEvent(key, over = {}) {
  return {
    key,
    repeat: false,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    target: { tagName: "DIV", isContentEditable: false },
    preventDefault: vi.fn(),
    ...over,
  };
}

let store;
let compareOpen;
let deps;
let handle;

beforeEach(() => {
  store = makeStore();
  compareOpen = false;
  deps = {
    store,
    isCompareOpen: () => compareOpen,
    openCompare: vi.fn(() => {
      compareOpen = true;
    }),
    closeCompare: vi.fn(() => {
      compareOpen = false;
    }),
    undo: vi.fn(),
    isReadOnly: vi.fn(() => false),
    isBlocked: vi.fn(() => false),
    onEscape: vi.fn(),
    onExclusionRefused: vi.fn(),
    toggleExpansion: vi.fn(),
  };
  handle = createDedupKeyHandler(deps);
});

describe("dedup keyboard - moving the focus", () => {
  it("ArrowDown moves down, ArrowUp moves up", () => {
    handle(keyEvent("ArrowDown"));
    expect(store.focusNext).toHaveBeenCalledTimes(1);
    handle(keyEvent("ArrowUp"));
    expect(store.focusPrev).toHaveBeenCalledTimes(1);
  });

  // Amendment #3: the letter aliases left navigation - k became Keep
  // separate and j is deliberately unclaimed. Neither may move the focus.
  it("j and k no longer navigate", () => {
    handle(keyEvent("j"));
    handle(keyEvent("k"));
    expect(store.focusNext).not.toHaveBeenCalled();
    expect(store.focusPrev).not.toHaveBeenCalled();
  });

  // Home goes through the store's own jump-to-start: after an End jump the
  // window no longer contains the top, so a bare setFocus(0) could not reach
  // it - the store resets to the first page instead.
  it("Home jumps to the first group through the store", () => {
    handle(keyEvent("Home"));
    expect(store.focusStart).toHaveBeenCalledTimes(1);
    expect(store.setFocus).not.toHaveBeenCalled();
  });

  // The regression this pins: End used to focus the last LOADED row
  // (groups.length - 1), so on a paging queue it had to be pressed once per
  // page. The store's focusEnd pages the rest in and lands on the true end,
  // so one press is the whole gesture.
  it("End goes to the queue's true end through the store, and claims the key", () => {
    const event = keyEvent("End");
    handle(event);
    expect(store.focusEnd).toHaveBeenCalledTimes(1);
    expect(store.setFocus).not.toHaveBeenCalled();
    expect(event.preventDefault).toHaveBeenCalled();
  });

  it("PageDown and PageUp move a screenful, as the view measures it", () => {
    deps.pageRows = () => 7;
    handle = createDedupKeyHandler(deps);
    store.focusIndex = 9;
    handle(keyEvent("PageDown"));
    expect(store.setFocus).toHaveBeenCalledWith(16);
    handle(keyEvent("PageUp"));
    expect(store.setFocus).toHaveBeenCalledWith(2);
  });

  it("falls back to a conservative page when the viewport is unmeasurable", () => {
    // A list that has not laid out yet reports 0 rows. Moving by zero would be
    // a dead key, and guessing a whole screen would overshoot the queue.
    deps.pageRows = () => 0;
    handle = createDedupKeyHandler(deps);
    handle(keyEvent("PageDown"));
    expect(store.setFocus).toHaveBeenCalledWith(5);
  });

  it("claims the page keys so the scroll container does not also move", () => {
    // The page keys would otherwise scroll the list out from under the cursor
    // AND move the focus, which lands the user two screens from where they are.
    const event = keyEvent("PageDown");
    handle(event);
    expect(event.preventDefault).toHaveBeenCalled();
  });

  // Reading the queue is not a verdict, so navigation stays live for a share
  // session that cannot act on it.
  it("still navigates in a read-only session", () => {
    deps.isReadOnly.mockReturnValue(true);
    handle(keyEvent("ArrowDown"));
    expect(store.focusNext).toHaveBeenCalled();
  });
});

describe("dedup keyboard - verdicts", () => {
  it("Enter stacks the focused group", () => {
    handle(keyEvent("Enter"));
    expect(store.stack).toHaveBeenCalledWith(store.groups[0]);
  });

  // Amendment #3: S is a SYNONYM of Enter - the owner's S-for-Stack slip is
  // now self-healing, so S must never keep-separate again.
  it("S stacks too, as Enter's synonym", () => {
    handle(keyEvent("s"));
    expect(store.stack).toHaveBeenCalledWith(store.groups[0]);
    expect(store.keepSeparate).not.toHaveBeenCalled();
  });

  it("K keeps the focused group separate", () => {
    handle(keyEvent("k"));
    expect(store.keepSeparate).toHaveBeenCalledWith(store.groups[0]);
    expect(store.stack).not.toHaveBeenCalled();
  });

  it("acts on whichever group is focused, not the first", () => {
    store.focusIndex = 1;
    handle(keyEvent("Enter"));
    expect(store.stack).toHaveBeenCalledWith(store.groups[1]);
  });

  // A held Enter would otherwise empty the queue in one press.
  it("declines an auto-repeated key", () => {
    handle(keyEvent("Enter", { repeat: true }));
    expect(store.stack).not.toHaveBeenCalled();
  });

  // A verdict already in flight must not be double-sent by an impatient press.
  it("declines while a verdict is in flight", () => {
    store.busy = true;
    handle(keyEvent("Enter"));
    handle(keyEvent("s"));
    handle(keyEvent("k"));
    expect(store.stack).not.toHaveBeenCalled();
    expect(store.keepSeparate).not.toHaveBeenCalled();
  });

  it("declines a verdict in a read-only session", () => {
    deps.isReadOnly.mockReturnValue(true);
    handle(keyEvent("Enter"));
    handle(keyEvent("s"));
    handle(keyEvent("k"));
    expect(store.stack).not.toHaveBeenCalled();
    expect(store.keepSeparate).not.toHaveBeenCalled();
  });

  it("does nothing at all when the queue is empty", () => {
    store.groups = [];
    store.focusIndex = -1;
    handle(keyEvent("Enter"));
    handle(keyEvent("x"));
    handle(keyEvent("1"));
    expect(store.stack).not.toHaveBeenCalled();
    expect(store.toggleExcluded).not.toHaveBeenCalled();
    expect(store.setCover).not.toHaveBeenCalled();
  });
});

describe("dedup keyboard - cover and exclusion", () => {
  it("1 to 9 point at that candidate and make it the cover", () => {
    handle(keyEvent("3"));
    expect(store.setCover).toHaveBeenCalledWith("g1", 3);
  });

  // A group of two must not accept a 5; silently picking the last candidate
  // would set a cover the user never asked for.
  it("ignores a digit past the end of the group", () => {
    store.focusIndex = 1;
    handle(keyEvent("5"));
    expect(store.setCover).not.toHaveBeenCalled();
  });

  // Every other key this handler recognises is claimed whether or not it had
  // anything to do. A digit that fell through unclaimed would reach the app
  // shell, which owns keys of its own, from the one surface that says the
  // number keys belong to the focused group.
  it("claims a digit past the end rather than letting it fall through", () => {
    store.focusIndex = 1;
    const event = keyEvent("5");
    handle(event);
    expect(event.preventDefault).toHaveBeenCalled();
  });

  // X is a one-key action with no confirmation. The store refuses an exclusion
  // that would drop the group below the two members a stack needs; refusing it
  // silently is how a key stops being trusted.
  it("reports a refused exclusion so the view can narrate it", () => {
    store.toggleExcluded.mockReturnValue(false);
    handle(keyEvent("x"));
    expect(deps.onExclusionRefused).toHaveBeenCalledWith(store.groups[0]);
  });

  it("stays quiet when the exclusion was applied", () => {
    store.toggleExcluded.mockReturnValue(true);
    handle(keyEvent("x"));
    expect(deps.onExclusionRefused).not.toHaveBeenCalled();
  });

  it("X leaves the candidate under the cursor out of the stack", () => {
    handle(keyEvent("x"));
    expect(store.toggleExcluded).toHaveBeenCalledWith(store.groups[0], 2);
  });

  it("X does nothing when the group has no cover to point at", () => {
    store.coverIdFor.mockReturnValue(null);
    handle(keyEvent("x"));
    expect(store.toggleExcluded).not.toHaveBeenCalled();
  });
});

describe("dedup keyboard - Compare", () => {
  it("C opens Compare on the focused group", () => {
    handle(keyEvent("c"));
    expect(deps.openCompare).toHaveBeenCalled();
  });

  it("Escape closes Compare", () => {
    compareOpen = true;
    handle(keyEvent("Escape"));
    expect(deps.closeCompare).toHaveBeenCalled();
  });

  // Compare exists so the decision is made without a second trip - and a run
  // of decisions without reopening it every time. The verdict keys leave the
  // dialog up; the auto-advance flips it to the next group, and the view
  // closes it only when the queue runs out.
  it("Enter and S both stack, K keeps separate, none closing Compare", () => {
    compareOpen = true;
    handle(keyEvent("Enter"));
    handle(keyEvent("s"));
    expect(store.stack).toHaveBeenCalledTimes(2);
    expect(store.stack).toHaveBeenCalledWith(store.groups[0]);
    expect(deps.closeCompare).not.toHaveBeenCalled();

    handle(keyEvent("k"));
    expect(store.keepSeparate).toHaveBeenCalledWith(store.groups[0]);
    expect(deps.closeCompare).not.toHaveBeenCalled();
  });

  // The dialog renders the focused group, so a focus move flips it in place
  // and no place is lost - Up/Down switch the compared group. k must NEVER
  // navigate here: it is a verdict key now (amendment #3).
  it("Up/Down switch the compared group in place; j and k do not", () => {
    compareOpen = true;
    handle(keyEvent("ArrowDown"));
    expect(store.focusNext).toHaveBeenCalledTimes(1);
    handle(keyEvent("ArrowUp"));
    expect(store.focusPrev).toHaveBeenCalledTimes(1);
    handle(keyEvent("j"));
    expect(store.focusNext).toHaveBeenCalledTimes(1);
    expect(store.focusPrev).toHaveBeenCalledTimes(1);
  });

  // Reading the queue is not a verdict, and neither is comparing it.
  it("still switches groups inside Compare in a read-only session", () => {
    compareOpen = true;
    deps.isReadOnly.mockReturnValue(true);
    handle(keyEvent("ArrowDown"));
    expect(store.focusNext).toHaveBeenCalled();
  });

  // A multi-row leap behind the dialog reads as the queue teleporting, so
  // the jump keys stay quiet there.
  it("swallows the jump keys while Compare is open", () => {
    compareOpen = true;
    handle(keyEvent("Home"));
    handle(keyEvent("End"));
    handle(keyEvent("PageDown"));
    handle(keyEvent("PageUp"));
    expect(store.setFocus).not.toHaveBeenCalled();
    expect(store.focusStart).not.toHaveBeenCalled();
    expect(store.focusEnd).not.toHaveBeenCalled();
  });

  // Compare is the view that shows the fields a cover is chosen on, so making
  // the user close it to press a number would be the second trip Compare
  // exists to remove.
  it("keeps the cover and exclusion keys live inside Compare", () => {
    compareOpen = true;
    handle(keyEvent("2"));
    expect(store.setCover).toHaveBeenCalledWith("g1", 2);
    handle(keyEvent("x"));
    expect(store.toggleExcluded).toHaveBeenCalledWith(store.groups[0], 2);
  });

  it("declines the cover keys inside Compare in a read-only session", () => {
    compareOpen = true;
    deps.isReadOnly.mockReturnValue(true);
    handle(keyEvent("2"));
    handle(keyEvent("x"));
    expect(store.setCover).not.toHaveBeenCalled();
    expect(store.toggleExcluded).not.toHaveBeenCalled();
  });
});

describe("dedup keyboard: E opens the stack in place", () => {
  it("hands the focused group to the view's toggle and claims the key", () => {
    const event = keyEvent("e");
    handle(event);
    expect(deps.toggleExpansion).toHaveBeenCalledWith(store.groups[0]);
    // Claimed, so it never reaches the app shell underneath.
    expect(event.preventDefault).toHaveBeenCalled();
  });

  // Opening a deck is looking, not deciding, so it survives the guard that
  // takes the verdict keys away: exactly as C does.
  it("still works in a read-only session", () => {
    deps.isReadOnly.mockReturnValue(true);
    handle(keyEvent("e"));
    expect(deps.toggleExpansion).toHaveBeenCalledTimes(1);
  });

  // Disclosure, not a mode: the verdict the user was about to give is the same
  // verdict a moment after they opened a deck to check it.
  it("leaves the verdict keys untouched", () => {
    handle(keyEvent("e"));
    handle(keyEvent("Enter"));
    expect(store.stack).toHaveBeenCalledWith(store.groups[0]);
    handle(keyEvent("k"));
    expect(store.keepSeparate).toHaveBeenCalledWith(store.groups[0]);
  });

  // Compare has its own expansion on its own control. One key meaning two
  // different bands on one screen is how a key stops being trusted.
  it("does not open a second band from inside Compare", () => {
    compareOpen = true;
    handle(keyEvent("e"));
    expect(deps.toggleExpansion).not.toHaveBeenCalled();
  });

  // The default is a no-op rather than a crash: the model predates the band and
  // is mounted by tests that never pass the dep.
  it("no-ops when the view supplies no toggle", () => {
    const bare = createDedupKeyHandler({ ...deps, toggleExpansion: undefined });
    expect(() => bare(keyEvent("e"))).not.toThrow();
  });
});

describe("dedup keyboard - select all", () => {
  it("Ctrl+A selects every loaded group and claims the key", () => {
    store.selectAll = vi.fn();
    const event = keyEvent("a", { ctrlKey: true });
    handle(event);
    expect(store.selectAll).toHaveBeenCalled();
    expect(event.preventDefault).toHaveBeenCalled();
  });

  it("stays quiet while Compare is open - its keys own that surface", () => {
    store.selectAll = vi.fn();
    compareOpen = true;
    handle(keyEvent("a", { ctrlKey: true }));
    expect(store.selectAll).not.toHaveBeenCalled();
  });
});

describe("dedup keyboard - the blink compare (zoom)", () => {
  let zoomOpen;
  let zoom;

  beforeEach(() => {
    zoomOpen = false;
    zoom = {
      isOpen: () => zoomOpen,
      open: vi.fn(() => {
        zoomOpen = true;
      }),
      close: vi.fn(() => {
        zoomOpen = false;
      }),
      flip: vi.fn(),
      to: vi.fn(),
      togglePixels: vi.fn(),
      step: vi.fn(),
      // Reports whether the pan applied. False means "nothing to pan", which
      // is what lets the key fall through to the flip.
      pan: vi.fn(() => true),
    };
    compareOpen = true;
    handle = createDedupKeyHandler({ ...deps, zoom });
  });

  it("Z opens the zoom from Compare", () => {
    handle(keyEvent("z"));
    expect(zoom.open).toHaveBeenCalled();
  });

  it("arrows flip in place and digits jump - never the cover keys", () => {
    zoomOpen = true;
    handle(keyEvent("ArrowRight"));
    handle(keyEvent("ArrowLeft"));
    handle(keyEvent("3"));
    expect(zoom.flip).toHaveBeenCalledWith(1);
    expect(zoom.flip).toHaveBeenCalledWith(-1);
    expect(zoom.to).toHaveBeenCalledWith(2);
    // In the zoom, a digit FLIPS; it must not silently re-pick the cover.
    expect(store.setCover).not.toHaveBeenCalled();
    expect(store.focusNext).not.toHaveBeenCalled();
  });

  it("P toggles actual pixels", () => {
    zoomOpen = true;
    handle(keyEvent("p"));
    expect(zoom.togglePixels).toHaveBeenCalled();
  });

  it("plus and minus step the zoom", () => {
    zoomOpen = true;
    handle(keyEvent("+"));
    handle(keyEvent("-"));
    expect(zoom.step).toHaveBeenCalledWith(1);
    expect(zoom.step).toHaveBeenCalledWith(-1);
  });

  it("accepts = for zoom in, so it needs no Shift", () => {
    // `=` is the unshifted key on the same cap as `+` on most layouts.
    zoomOpen = true;
    handle(keyEvent("="));
    expect(zoom.step).toHaveBeenCalledWith(1);
  });

  it("Shift plus an arrow pans instead of flipping", () => {
    zoomOpen = true;
    handle(keyEvent("ArrowRight", { shiftKey: true }));
    expect(zoom.pan).toHaveBeenCalledWith(1, 0);
    // The whole point: the flip must NOT also fire, or the image the user is
    // panning changes under them.
    expect(zoom.flip).not.toHaveBeenCalled();
  });

  it("pans in all four directions", () => {
    zoomOpen = true;
    for (const [key, vector] of [
      ["ArrowLeft", [-1, 0]],
      ["ArrowRight", [1, 0]],
      ["ArrowUp", [0, -1]],
      ["ArrowDown", [0, 1]],
    ]) {
      handle(keyEvent(key, { shiftKey: true }));
      expect(zoom.pan).toHaveBeenCalledWith(vector[0], vector[1]);
    }
  });

  it("an unmodified arrow still flips, which is the core gesture", () => {
    zoomOpen = true;
    handle(keyEvent("ArrowRight"));
    expect(zoom.flip).toHaveBeenCalledWith(1);
    expect(zoom.pan).not.toHaveBeenCalled();
  });

  it("falls through to the flip when there is nothing to pan", () => {
    // At Fit the image does not overflow, so a pan is meaningless. Eating the
    // key there would make Shift+Arrow feel broken rather than unavailable.
    zoomOpen = true;
    zoom.pan = vi.fn(() => false);
    handle(keyEvent("ArrowRight", { shiftKey: true }));
    expect(zoom.flip).toHaveBeenCalledWith(1);
  });

  it("leaves the zoom keys alone when the zoom is shut", () => {
    zoomOpen = false;
    handle(keyEvent("+"));
    handle(keyEvent("ArrowRight", { shiftKey: true }));
    expect(zoom.step).not.toHaveBeenCalled();
    expect(zoom.pan).not.toHaveBeenCalled();
  });

  it("Escape peels one layer: zoom first, Compare second", () => {
    zoomOpen = true;
    handle(keyEvent("Escape"));
    expect(zoom.close).toHaveBeenCalled();
    expect(deps.closeCompare).not.toHaveBeenCalled();

    zoomOpen = false;
    handle(keyEvent("Escape"));
    expect(deps.closeCompare).toHaveBeenCalled();
  });

  it("Enter stacks from inside the zoom, closing the zoom but not Compare", () => {
    zoomOpen = true;
    handle(keyEvent("Enter"));
    expect(zoom.close).toHaveBeenCalled();
    // Compare stays up: the next group appears in it, un-zoomed.
    expect(deps.closeCompare).not.toHaveBeenCalled();
    expect(store.stack).toHaveBeenCalled();
  });
});

describe("dedup keyboard - Escape", () => {
  // Escape is the way out of a row's buttons. A key that visibly does nothing
  // is a key the user stops trusting.
  it("calls onEscape when Compare is closed", () => {
    handle(keyEvent("Escape"));
    expect(deps.onEscape).toHaveBeenCalled();
  });

  // The popover that raised the block guard is exactly the thing Escape has to
  // be able to dismiss, so Escape resolves before the guard.
  it("still calls onEscape while another surface blocks the queue", () => {
    deps.isBlocked.mockReturnValue(true);
    handle(keyEvent("Escape"));
    expect(deps.onEscape).toHaveBeenCalled();
  });

  it("closes Compare rather than calling onEscape while Compare is open", () => {
    compareOpen = true;
    handle(keyEvent("Escape"));
    expect(deps.closeCompare).toHaveBeenCalled();
    expect(deps.onEscape).not.toHaveBeenCalled();
  });

  // Reading the queue is not a verdict, so the way out stays live in a share
  // session.
  it("works in a read-only session", () => {
    deps.isReadOnly.mockReturnValue(true);
    handle(keyEvent("Escape"));
    expect(deps.onEscape).toHaveBeenCalled();
  });
});

describe("dedup keyboard - Enter belongs to a focused control", () => {
  // A user who tabbed onto Compare and pressed Enter must get Compare, not a
  // stack of the group behind it.
  it("declines Enter while a button has focus", () => {
    handle(keyEvent("Enter", { target: { tagName: "BUTTON" } }));
    expect(store.stack).not.toHaveBeenCalled();
  });

  it("declines Enter while a link or a role=button has focus", () => {
    handle(
      keyEvent("Enter", {
        target: { tagName: "A", getAttribute: () => "/somewhere" },
      }),
    );
    handle(
      keyEvent("Enter", {
        target: { tagName: "SPAN", getAttribute: () => "button" },
      }),
    );
    expect(store.stack).not.toHaveBeenCalled();
  });

  // Only Enter is the button's key. The queue keeps the rest, which is what
  // lets a user act without first tabbing back out of the row.
  it("keeps the other keys while a button has focus", () => {
    const onButton = { tagName: "BUTTON" };
    handle(keyEvent("ArrowDown", { target: onButton }));
    handle(keyEvent("k", { target: onButton }));
    expect(store.focusNext).toHaveBeenCalled();
    expect(store.keepSeparate).toHaveBeenCalled();
  });

  it("declines Enter on a dialog button while Compare is open", () => {
    compareOpen = true;
    handle(keyEvent("Enter", { target: { tagName: "BUTTON" } }));
    expect(store.stack).not.toHaveBeenCalled();
    expect(deps.closeCompare).not.toHaveBeenCalled();
  });
});

describe("dedup keyboard - undo and the guards", () => {
  it("Ctrl+Z and Cmd+Z both undo", () => {
    handle(keyEvent("z", { ctrlKey: true }));
    handle(keyEvent("z", { metaKey: true }));
    expect(deps.undo).toHaveBeenCalledTimes(2);
  });

  // Undo is the escape hatch a user reaches for precisely when a dialog is up
  // and something went wrong.
  it("undoes even while Compare is open", () => {
    compareOpen = true;
    handle(keyEvent("z", { ctrlKey: true }));
    expect(deps.undo).toHaveBeenCalled();
  });

  it("leaves redo to the app shell", () => {
    handle(keyEvent("z", { ctrlKey: true, shiftKey: true }));
    handle(keyEvent("y", { ctrlKey: true }));
    expect(deps.undo).not.toHaveBeenCalled();
  });

  it("declines undo in a read-only session", () => {
    deps.isReadOnly.mockReturnValue(true);
    handle(keyEvent("z", { ctrlKey: true }));
    expect(deps.undo).not.toHaveBeenCalled();
  });

  // A text field keeps its own editing keys, including its native undo stack.
  it("declines every key while a text field has focus", () => {
    const typing = { tagName: "INPUT", isContentEditable: false };
    handle(keyEvent("Enter", { target: typing }));
    handle(keyEvent("s", { target: typing }));
    handle(keyEvent("z", { target: typing, ctrlKey: true }));
    expect(store.stack).not.toHaveBeenCalled();
    expect(store.keepSeparate).not.toHaveBeenCalled();
    expect(deps.undo).not.toHaveBeenCalled();
  });

  it("declines inside a contenteditable region", () => {
    handle(
      keyEvent("Enter", {
        target: { tagName: "DIV", isContentEditable: true },
      }),
    );
    expect(store.stack).not.toHaveBeenCalled();
  });

  // Vuetify renders a slider's focusable thumb as a div, not an input. Its
  // arrows must operate the slider ALONE - acting on the queue as well would
  // double every press: one size step and one row moved.
  it("leaves every key to a focused slider thumb", () => {
    const thumb = {
      tagName: "DIV",
      isContentEditable: false,
      getAttribute: (name) => (name === "role" ? "slider" : null),
    };
    handle(keyEvent("ArrowDown", { target: thumb }));
    handle(keyEvent("Enter", { target: thumb }));
    handle(keyEvent("s", { target: thumb }));
    expect(store.focusNext).not.toHaveBeenCalled();
    expect(store.stack).not.toHaveBeenCalled();
    expect(store.keepSeparate).not.toHaveBeenCalled();
  });

  // The tier popover blocks only the keys pressed inside itself; the view
  // needs the event to make that call.
  it("hands the event to isBlocked so a popover can scope its block", () => {
    deps.isBlocked.mockReturnValue(false);
    const event = keyEvent("ArrowDown");
    handle(event);
    expect(deps.isBlocked).toHaveBeenCalledWith(event);
  });

  // The auto-stack dialog owns the screen while it is up.
  it("goes quiet while another modal blocks the queue", () => {
    deps.isBlocked.mockReturnValue(true);
    handle(keyEvent("Enter"));
    handle(keyEvent("ArrowDown"));
    expect(store.stack).not.toHaveBeenCalled();
    expect(store.focusNext).not.toHaveBeenCalled();
  });

  it("leaves browser chords alone", () => {
    handle(keyEvent("s", { ctrlKey: true }));
    handle(keyEvent("Enter", { altKey: true }));
    expect(store.stack).not.toHaveBeenCalled();
    expect(store.keepSeparate).not.toHaveBeenCalled();
  });
});

describe("dedup keyboard: digits address units, not candidates", () => {
  // A deck occupies one slot and one index however many of its members the
  // group named, so the number under a tile is the number that selects it. A
  // handler still indexing `candidates` would put `2` on the deck's second
  // member: a tile that does not exist.
  it("indexes the strip's units and resolves a deck to its leader", () => {
    store.groups[0] = {
      signature: "g1",
      candidates: [
        { picture_id: 503, stack_id: 12 },
        { picture_id: 504, stack_id: 12 },
        { picture_id: 700 },
      ],
      stacks: {
        12: { stack_id: 12, member_count: 4, leader_picture_id: 501 },
      },
    };
    // Two tiles, not three: the deck, then the loose picture.
    handle(keyEvent("1"));
    expect(store.setCover).toHaveBeenCalledWith("g1", 501);
    handle(keyEvent("2"));
    expect(store.setCover).toHaveBeenCalledWith("g1", 700);

    // And nothing beyond the second tile, even though a third candidate exists.
    store.setCover.mockClear();
    const event = keyEvent("3");
    handle(event);
    expect(store.setCover).not.toHaveBeenCalled();
    expect(event.preventDefault).toHaveBeenCalled();
  });

  // The band shows a deck's members, and it must not become a second thing the
  // digits can address: `1`-`9` keep meaning the strip's tiles whether it is
  // open or not, or the same key means two things on one screen.
  it("keeps addressing units while an expansion is open", () => {
    store.groups[0] = {
      signature: "g1",
      candidates: [
        { picture_id: 503, stack_id: 12 },
        { picture_id: 504, stack_id: 12 },
        { picture_id: 700 },
      ],
      stacks: {
        12: { stack_id: 12, member_count: 4, leader_picture_id: 501 },
      },
    };
    handle(keyEvent("e"));
    expect(deps.toggleExpansion).toHaveBeenCalledTimes(1);

    handle(keyEvent("1"));
    expect(store.setCover).toHaveBeenCalledWith("g1", 501);
    handle(keyEvent("2"));
    expect(store.setCover).toHaveBeenCalledWith("g1", 700);
    // Not 503/504: the deck's own members are never addressable by digit.
    expect(store.setCover).toHaveBeenCalledTimes(2);
  });

  // A frozen unit cannot lead a stack it is not in, exactly as a click on it
  // does not set the cover. Still claimed, so the key never falls through.
  it("declines a frozen unit but keeps the key claimed", () => {
    store.groups[0] = {
      signature: "g1",
      candidates: [{ picture_id: 1, stackable: false }, { picture_id: 2 }],
    };
    const event = keyEvent("1");
    handle(event);
    expect(store.setCover).not.toHaveBeenCalled();
    expect(event.preventDefault).toHaveBeenCalled();
  });
});

// ── Parameterised for a second queue ────────────────────────────────────────
//
// The Mixed stacks page is the third dedup queue, and it drives THIS handler
// rather than a copy of it: the five decline guards, the claim contract, the
// Escape layering and the Compare-open branch are identical there, and a second
// implementation would be a second place for them to drift. Three hooks carry
// the three facts that genuinely differ.

describe("createDedupKeyHandler - a second queue's rows", () => {
  /** A row whose addressable things are a stack's members, not a group's units. */
  const row = { stack_id: 42, member_ids: [7, 8, 9] };

  function mixedDeps(over = {}) {
    const mixedStore = {
      ...makeStore(),
      groups: [row],
      focusIndex: 0,
      get focusedGroup() {
        return row;
      },
    };
    return {
      ...deps,
      store: mixedStore,
      unitsOf: () =>
        row.member_ids.map((id) => ({ coverPictureId: id, stackable: true })),
      signatureOf: (r) => r.stack_id,
      ...over,
    };
  }

  // The digits address the same tiles on both queues and mean the same thing.
  it("points the digits at whatever the surface says its row holds", () => {
    const d = mixedDeps();
    const handler = createDedupKeyHandler(d);
    const event = keyEvent("2");
    handler(event);
    expect(event.preventDefault).toHaveBeenCalled();
    // Keyed on the row's own id, not on a `signature` a stack does not have.
    expect(d.store.setCover).toHaveBeenCalledWith(42, 8);
  });

  it("still claims a digit that addresses nothing", () => {
    const d = mixedDeps();
    const handler = createDedupKeyHandler(d);
    const event = keyEvent("9");
    handler(event);
    expect(event.preventDefault).toHaveBeenCalled();
    expect(d.store.setCover).not.toHaveBeenCalled();
  });

  // S is Stack in the review queue, and a user trained there presses it here
  // meaning Stack. On a queue whose primary is Split, that is the opposite act,
  // so the key is claimed and answered rather than run.
  it("hands S to the surface instead of the primary when asked to", () => {
    const onStackSynonym = vi.fn();
    const d = mixedDeps({ onStackSynonym });
    const handler = createDedupKeyHandler(d);
    const event = keyEvent("s");
    handler(event);
    expect(event.preventDefault).toHaveBeenCalled();
    expect(onStackSynonym).toHaveBeenCalledWith(row);
    expect(d.store.stack).not.toHaveBeenCalled();
    // Enter is untouched: it is still the primary.
    handler(keyEvent("Enter"));
    expect(d.store.stack).toHaveBeenCalledWith(row);
  });

  it("keeps S as Enter's synonym when no hook is given", () => {
    const d = mixedDeps();
    createDedupKeyHandler(d)(keyEvent("s"));
    expect(d.store.stack).toHaveBeenCalledWith(row);
  });

  // The same layering inside Compare, because a verdict there is the point of
  // having Compare at all.
  it("answers S the same way from inside Compare", () => {
    const onStackSynonym = vi.fn();
    const d = mixedDeps({ onStackSynonym, isCompareOpen: () => true });
    createDedupKeyHandler(d)(keyEvent("s"));
    expect(onStackSynonym).toHaveBeenCalledWith(row);
    expect(d.store.stack).not.toHaveBeenCalled();
  });

  // X is the SAME gesture on both queues: it acts on the item under the cursor,
  // which `coverIdFor` names.
  it("points X at the item under the cursor", () => {
    const d = mixedDeps();
    d.store.coverIdFor = vi.fn(() => 9);
    d.store.toggleExcluded = vi.fn(() => true);
    const handler = createDedupKeyHandler(d);
    const event = keyEvent("x");
    handler(event);
    expect(event.preventDefault).toHaveBeenCalled();
    expect(d.store.toggleExcluded).toHaveBeenCalledWith(row, 9);
  });

  // The default is the review queue's own behaviour, unchanged: the two new
  // hooks must be invisible to every existing caller.
  it("leaves the review queue's digits and S exactly as they were", () => {
    const handler = createDedupKeyHandler(deps);
    handler(keyEvent("3"));
    expect(store.setCover).toHaveBeenCalledWith("g1", 3);
    handler(keyEvent("s"));
    expect(store.stack).toHaveBeenCalledWith(store.groups[0]);
  });
});
