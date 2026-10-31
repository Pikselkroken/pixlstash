// AppButton's `icon-left` takes a bare mdi name: the button adds `mdi-` itself.
//
// Settings › ComfyUI passed `icon-left="mdi-refresh"`, which rendered
// `mdi-mdi-refresh`, a glyph that does not exist. On an icon-only ghost button
// that leaves nothing on screen, so every row's Test and ⋯ menu (rename, edit
// address, link again, unlink, remove) was there but invisible. A component
// test cannot see a missing glyph, so the rule is asserted over every template.

import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

// vitest runs from the frontend/ package root.
const SRC = join(process.cwd(), "src");

function vueFiles(dir) {
  const found = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) found.push(...vueFiles(path));
    else if (name.endsWith(".vue")) found.push(path);
  }
  return found;
}

describe("AppButton icon names", () => {
  it("no template passes icon-left an mdi- prefixed name", () => {
    const files = vueFiles(SRC);
    expect(files.length).toBeGreaterThan(50);
    const prefixed = files.flatMap((path) =>
      readFileSync(path, "utf8")
        .split("\n")
        .map((line, i) => ({ line, at: `${path.slice(SRC.length)}:${i + 1}` }))
        .filter(({ line }) => /icon-left="[^"]*\bmdi-/.test(line))
        .map(({ at }) => at),
    );
    expect(prefixed).toEqual([]);
  });
});
