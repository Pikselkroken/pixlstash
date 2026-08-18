import { describe, expect, it, vi } from "vitest";
import { activateOnEnterOrSpace } from "./keyboardActivation.js";

function keyboardEvent(target, key, init = {}) {
  return {
    target,
    currentTarget: target,
    key,
    view: window,
    preventDefault: vi.fn(),
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    ...init,
  };
}

describe("activateOnEnterOrSpace", () => {
  it.each(["Enter", " "])("activates a composite row with %j", (key) => {
    const row = document.createElement("div");
    const click = vi.fn();
    row.addEventListener("click", click);
    const event = keyboardEvent(row, key);

    expect(activateOnEnterOrSpace(event)).toBe(true);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
  });

  it("preserves multi-select modifiers on the synthetic click", () => {
    const row = document.createElement("div");
    let clickEvent;
    row.addEventListener("click", (event) => {
      clickEvent = event;
    });

    activateOnEnterOrSpace(
      keyboardEvent(row, "Enter", { ctrlKey: true, shiftKey: true }),
    );

    expect(clickEvent.ctrlKey).toBe(true);
    expect(clickEvent.shiftKey).toBe(true);
  });

  it("does not activate a parent row for a nested control's key press", () => {
    const row = document.createElement("div");
    const child = document.createElement("button");
    row.append(child);
    const event = keyboardEvent(row, "Enter", { target: child });

    expect(activateOnEnterOrSpace(event)).toBe(false);
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  it("ignores unrelated keys", () => {
    const row = document.createElement("div");
    const event = keyboardEvent(row, "ArrowDown");

    expect(activateOnEnterOrSpace(event)).toBe(false);
    expect(event.preventDefault).not.toHaveBeenCalled();
  });
});
