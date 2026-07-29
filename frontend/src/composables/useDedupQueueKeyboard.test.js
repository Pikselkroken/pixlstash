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
  };
  handle = createDedupKeyHandler(deps);
});

describe("dedup keyboard — moving the focus", () => {
  it("ArrowDown and j both move down", () => {
    handle(keyEvent("ArrowDown"));
    handle(keyEvent("j"));
    expect(store.focusNext).toHaveBeenCalledTimes(2);
  });

  it("ArrowUp and k both move up", () => {
    handle(keyEvent("ArrowUp"));
    handle(keyEvent("k"));
    expect(store.focusPrev).toHaveBeenCalledTimes(2);
  });

  it("Home and End jump to the ends of the queue", () => {
    handle(keyEvent("Home"));
    expect(store.setFocus).toHaveBeenCalledWith(0);
    handle(keyEvent("End"));
    expect(store.setFocus).toHaveBeenCalledWith(1);
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

describe("dedup keyboard — verdicts", () => {
  it("Enter stacks the focused group", () => {
    handle(keyEvent("Enter"));
    expect(store.stack).toHaveBeenCalledWith(store.groups[0]);
  });

  it("S keeps the focused group separate", () => {
    handle(keyEvent("s"));
    expect(store.keepSeparate).toHaveBeenCalledWith(store.groups[0]);
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
    expect(store.stack).not.toHaveBeenCalled();
    expect(store.keepSeparate).not.toHaveBeenCalled();
  });

  it("declines a verdict in a read-only session", () => {
    deps.isReadOnly.mockReturnValue(true);
    handle(keyEvent("Enter"));
    handle(keyEvent("s"));
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

describe("dedup keyboard — cover and exclusion", () => {
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

describe("dedup keyboard — Compare", () => {
  it("C opens Compare on the focused group", () => {
    handle(keyEvent("c"));
    expect(deps.openCompare).toHaveBeenCalled();
  });

  it("Escape closes Compare", () => {
    compareOpen = true;
    handle(keyEvent("Escape"));
    expect(deps.closeCompare).toHaveBeenCalled();
  });

  // Compare exists so the decision is made without a second trip.
  it("Enter and S still give a verdict from inside Compare", () => {
    compareOpen = true;
    handle(keyEvent("Enter"));
    expect(deps.closeCompare).toHaveBeenCalled();
    expect(store.stack).toHaveBeenCalledWith(store.groups[0]);

    compareOpen = true;
    handle(keyEvent("s"));
    expect(store.keepSeparate).toHaveBeenCalledWith(store.groups[0]);
  });

  // An arrow key moving the row behind an open dialog is how a user loses
  // their place. Only the row-to-row keys are swallowed.
  it("swallows the row-to-row keys while Compare is open", () => {
    compareOpen = true;
    handle(keyEvent("ArrowDown"));
    handle(keyEvent("ArrowUp"));
    handle(keyEvent("Home"));
    expect(store.focusNext).not.toHaveBeenCalled();
    expect(store.focusPrev).not.toHaveBeenCalled();
    expect(store.setFocus).not.toHaveBeenCalled();
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

describe("dedup keyboard — select all", () => {
  it("Ctrl+A selects every loaded group and claims the key", () => {
    store.selectAll = vi.fn();
    const event = keyEvent("a", { ctrlKey: true });
    handle(event);
    expect(store.selectAll).toHaveBeenCalled();
    expect(event.preventDefault).toHaveBeenCalled();
  });

  it("stays quiet while Compare is open — its keys own that surface", () => {
    store.selectAll = vi.fn();
    compareOpen = true;
    handle(keyEvent("a", { ctrlKey: true }));
    expect(store.selectAll).not.toHaveBeenCalled();
  });
});

describe("dedup keyboard — the blink compare (zoom)", () => {
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
    };
    compareOpen = true;
    handle = createDedupKeyHandler({ ...deps, zoom });
  });

  it("Z opens the zoom from Compare", () => {
    handle(keyEvent("z"));
    expect(zoom.open).toHaveBeenCalled();
  });

  it("arrows flip in place and digits jump — never the cover keys", () => {
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

  it("Escape peels one layer: zoom first, Compare second", () => {
    zoomOpen = true;
    handle(keyEvent("Escape"));
    expect(zoom.close).toHaveBeenCalled();
    expect(deps.closeCompare).not.toHaveBeenCalled();

    zoomOpen = false;
    handle(keyEvent("Escape"));
    expect(deps.closeCompare).toHaveBeenCalled();
  });

  it("Enter stacks from inside the zoom, closing both layers", () => {
    zoomOpen = true;
    handle(keyEvent("Enter"));
    expect(zoom.close).toHaveBeenCalled();
    expect(deps.closeCompare).toHaveBeenCalled();
    expect(store.stack).toHaveBeenCalled();
  });
});

describe("dedup keyboard — Escape", () => {
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

describe("dedup keyboard — Enter belongs to a focused control", () => {
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
    handle(keyEvent("s", { target: onButton }));
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

describe("dedup keyboard — undo and the guards", () => {
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
