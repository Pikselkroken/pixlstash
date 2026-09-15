import { describe, it, expect, beforeEach } from "vitest";
import { onMenuKeydown } from "./menuKeyboard.js";

function buildMenu(html) {
  document.body.innerHTML = `<div class="ctx-menu">${html}</div>`;
  const menu = document.querySelector(".ctx-menu");
  menu.addEventListener("keydown", onMenuKeydown);
  return menu;
}

function press(menu, key) {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
  menu.dispatchEvent(event);
  return event;
}

const ROWS = `
  <button class="ctx-item" id="a">A</button>
  <div class="ctx-sep"></div>
  <button class="ctx-item" id="b">B</button>
  <button class="ctx-item" id="c">C</button>
`;

describe("onMenuKeydown", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("takes focus into the menu and wraps in both directions", () => {
    const menu = buildMenu(ROWS);
    press(menu, "ArrowDown");
    expect(document.activeElement.id).toBe("a");
    press(menu, "ArrowDown");
    press(menu, "ArrowDown");
    expect(document.activeElement.id).toBe("c");
    press(menu, "ArrowDown");
    expect(document.activeElement.id).toBe("a");
    press(menu, "ArrowUp");
    expect(document.activeElement.id).toBe("c");
  });

  it("jumps to the ends on Home and End", () => {
    const menu = buildMenu(ROWS);
    press(menu, "End");
    expect(document.activeElement.id).toBe("c");
    press(menu, "Home");
    expect(document.activeElement.id).toBe("a");
  });

  it("skips disabled rows by either spelling", () => {
    const menu = buildMenu(`
      <button class="ctx-item" id="a">A</button>
      <button class="ctx-item" id="b" disabled>B</button>
      <button class="ctx-item ctx-item--disabled" id="c">C</button>
      <button class="ctx-item" id="d">D</button>
    `);
    press(menu, "ArrowDown");
    press(menu, "ArrowDown");
    expect(document.activeElement.id).toBe("d");
  });

  it("leaves a submenu's rows to the submenu", () => {
    const menu = buildMenu(`
      <button class="ctx-item" id="a">A</button>
      <div class="ctx-submenu">
        <button class="ctx-item" id="sub">Sub</button>
      </div>
      <button class="ctx-item" id="b">B</button>
    `);
    press(menu, "ArrowDown");
    expect(document.activeElement.id).toBe("a");
    // Steps over the submenu's row, not into it.
    press(menu, "ArrowDown");
    expect(document.activeElement.id).toBe("b");
  });

  it("claims the navigation keys and nothing else", () => {
    const menu = buildMenu(ROWS);
    expect(press(menu, "ArrowDown").defaultPrevented).toBe(true);
    expect(press(menu, "Escape").defaultPrevented).toBe(false);
    expect(press(menu, "Enter").defaultPrevented).toBe(false);
  });

  it("does nothing when the menu holds no enabled row", () => {
    const menu = buildMenu(`<button class="ctx-item" id="a" disabled>A</button>`);
    expect(press(menu, "ArrowDown").defaultPrevented).toBe(false);
    expect(document.activeElement.id).not.toBe("a");
  });
});
