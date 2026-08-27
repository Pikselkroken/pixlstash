// `backendUrl` is defaulted, never threaded.
//
// App.vue used to pass `:backend-url="BACKEND_URL"` down 40 bindings. Commit
// a956343d deleted the pass-downs and gave the prop `default: () => API_BASE_URL`
// in each component instead - except ImageGrid.vue, which kept a bare
// `backendUrl: String`. A prop with no default is `undefined`, so every URL the
// grid builds by hand became "undefined/pictures/thumbnails/<id>.webp" and every
// thumbnail in the library rendered as a broken-image icon. The same `undefined`
// also silently short-circuited the `if (!props.backendUrl) return` guards that
// load picture plugins and taggers.
//
// The bug is invisible in isolation: each component looks fine on its own, and
// the parent that used to supply the value no longer exists. So the invariant is
// asserted over the whole component tree rather than per component.

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

/** The `backendUrl:` entry of a `defineProps({...})` block, if the file has one. */
function backendUrlPropDeclaration(source) {
  const match = source.match(/^\s*backendUrl:\s*(.+)$/m);
  return match ? match[1].trim() : null;
}

describe("every component that takes backendUrl defaults it", () => {
  const components = vueFiles(SRC).map((path) => ({
    path: path.slice(SRC.length),
    source: readFileSync(path, "utf8"),
  }));

  it("finds components to check (the walk itself is not silently empty)", () => {
    expect(components.length).toBeGreaterThan(50);
    expect(
      components.filter((c) => backendUrlPropDeclaration(c.source)).length,
    ).toBeGreaterThan(10);
  });

  it("declares no bare `backendUrl: String`", () => {
    const undefaulted = components
      .filter((c) => {
        const declaration = backendUrlPropDeclaration(c.source);
        return declaration && !declaration.includes("default");
      })
      .map((c) => c.path);

    // ImageGrid.vue was the one that shipped, and it broke every thumbnail.
    expect(undefaulted).toEqual([]);
  });
});
