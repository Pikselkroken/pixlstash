import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { vSubmenuFit } from "./submenuFit";

const SRC = dirname(dirname(fileURLToPath(import.meta.url)));
const WINDOW_HEIGHT = 800;

/** A row at `rowTop` (32px tall) with a submenu of `height` hanging from it. */
function place(rowTop, height) {
  const row = document.createElement("div");
  const sub = document.createElement("div");
  row.appendChild(sub);
  row.getBoundingClientRect = () => ({ top: rowTop, bottom: rowTop + 32 });
  Object.defineProperty(sub, "offsetHeight", { value: height, configurable: true });
  vSubmenuFit.mounted(sub);
  return sub;
}

describe("vSubmenuFit", () => {
  const original = window.innerHeight;
  beforeAll(() => {
    window.innerHeight = WINDOW_HEIGHT;
  });
  afterAll(() => {
    window.innerHeight = original;
  });

  it("hangs down from its row when it fits below", () => {
    expect(place(100, 300).style.top).toBe("");
  });

  it("opens upwards, its bottom level with its row's, when it does not fit below", () => {
    // Row 600-632: 300 down would end at 900; up, it spans 332-632.
    expect(place(600, 300).style.top).toBe("-268px");
  });

  it("is pushed on screen from the top when it fits neither way", () => {
    // Row 400-432, 700 tall: down ends at 1100, up starts at -268.
    expect(place(400, 700).style.top).toBe("-392px");
  });

  it("measures again on update, from its natural place", () => {
    const sub = place(600, 300);
    sub.style.top = "-999px";
    Object.defineProperty(sub, "offsetHeight", { value: 100 });
    vSubmenuFit.updated(sub);
    expect(sub.style.top).toBe("");
  });
});

function vueFiles(dir = SRC) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return vueFiles(path);
    return entry.name.endsWith(".vue") ? [path] : [];
  });
}

// Every `.ctx-submenu` hangs from its row, so one without the directive runs
// off the bottom of the window again.
it("every .ctx-submenu carries v-submenu-fit", () => {
  const count = (text, re) => (text.match(re) ?? []).length;
  const missing = vueFiles().flatMap((path) => {
    const text = readFileSync(path, "utf8");
    const submenus =
      count(text, /class="ctx-submenu(?![-\w])/g) +
      count(text, /class: "ctx-submenu"/g);
    const fitted =
      count(text, /(?<=\s)v-submenu-fit(?=[\s>])/g) +
      count(text, /\[\[vSubmenuFit\]\]/g);
    return submenus === fitted ? [] : [relative(SRC, path)];
  });
  expect(missing).toEqual([]);
});
