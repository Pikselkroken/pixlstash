/**
 * The sidebar row system (issue #760, docs/design/visual-language.md §5.1).
 *
 * These assert the STYLESHEET, not a rendered component, because the bugs being
 * pinned are cascade bugs: a row type quietly reintroducing its own
 * `padding-left`, or an `.active` rule that adds the selection rail instead of
 * recolouring it. Both shipped here before, and both are invisible to a test
 * that only mounts a component and checks classes.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const read = (name) =>
  readFileSync(fileURLToPath(new URL(name, import.meta.url)), "utf8");

const globalCss = read("./SideBar.global.css");
const scopedCss = read("./SideBar.css");
const folderTreeNode = read("../editors/FolderTreeNode.vue");

/** Strip comments so prose about a bug is never mistaken for the bug. */
const code = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");

/**
 * Declaration blocks of EVERY rule whose selector list contains `selector`.
 *
 * Every, not the first: a row type is matched both by the shared row rule and
 * by its own rule, and it is the second one that reintroduces a private inset.
 * Returning only the first hit silently passed that exact regression.
 */
function ruleBodies(css, selector) {
  return code(css)
    .split("}")
    .filter((r) => {
      const [sel] = r.split("{");
      // Collapse whitespace: prettier wraps long descendant selectors across
      // lines, and a matcher that depends on where it wrapped is a test that
      // breaks on reformatting rather than on a defect.
      const want = selector.replace(/\s+/g, " ").trim();
      return sel
        .split(",")
        .map((s) => s.replace(/\s+/g, " ").trim())
        .includes(want);
    })
    .map((r) => r.split("{")[1])
    .filter(Boolean);
}

/** The single rule that defines `selector`, asserted to be unambiguous. */
function ruleBody(css, selector) {
  const bodies = ruleBodies(css, selector);
  return bodies.length ? bodies[bodies.length - 1] : null;
}

describe("the row system is defined once, unscoped", () => {
  it("lives in the unscoped sheet so it reaches FolderTreeNode", () => {
    // SideBar.css is `<style scoped>`, so a rule there cannot style a row
    // rendered by a child component. This placement is the fix.
    expect(globalCss).toContain(".sidebar-row-glyph");
    expect(globalCss).toContain("--indent-step");
  });

  it("FolderTreeNode keeps no private copy of the row styles", () => {
    // It used to, and the copy had drifted to a different inset with no base
    // rail, so selecting a nested folder shifted its label 3px right.
    const style = folderTreeNode.split("<style")[1] ?? "";
    for (const dup of [
      ".sidebar-folder-row {",
      ".sidebar-folder-children {",
      ".sidebar-folder-label {",
    ]) {
      expect(style).not.toContain(dup);
    }
  });
});

describe("every row starts at one left edge", () => {
  const ROW_TYPES = [
    ".sidebar-list-item",
    ".sidebar-project-tree-row",
    ".sidebar-project-tree-subheader",
    ".sidebar-folder-row",
    ".sidebar-folder-section-header",
    ".sidebar-section-header",
  ];

  it("derives padding-left from depth in one shared rule", () => {
    const body = ruleBody(globalCss, ROW_TYPES[0]);
    expect(body).toBeTruthy();
    expect(body).toContain("var(--depth, 0) * var(--indent-step)");
  });

  it("covers all six row types in that rule", () => {
    const shared = code(globalCss)
      .split("}")
      .find((r) => r.includes("var(--depth, 0) * var(--indent-step)"));
    const selectors = shared
      .split("{")[0]
      .split(",")
      .map((s) => s.trim());
    for (const type of ROW_TYPES) expect(selectors).toContain(type);
  });

  it("no row type sets its own padding-left or padding shorthand", () => {
    // The regression that produced six left edges: each row type owning its own
    // horizontal inset. Vertical padding stays per-type on purpose.
    for (const css of [globalCss, scopedCss]) {
      for (const type of ROW_TYPES) {
        for (const body of ruleBodies(css, type)) {
          if (body.includes("var(--depth, 0)")) continue; // the shared rule
          expect(
            body,
            `${type} must not set its own horizontal inset`,
          ).not.toMatch(/(^|\s|;)padding-left\s*:/);
          expect(
            body,
            `${type} must not use the padding shorthand, it resets the inset`,
          ).not.toMatch(/(^|\s|;)padding\s*:/);
        }
      }
    }
  });
});

describe("the selection rail is reserved, never added on select", () => {
  it("the shared row rule reserves a transparent rail", () => {
    const shared = code(globalCss)
      .split("}")
      .find((r) => r.includes("var(--depth, 0) * var(--indent-step)"));
    expect(shared).toContain("border-left: 3px solid transparent");
  });

  it("no .active rule sets border-left width, only its colour", () => {
    // `border-left: 3px solid <colour>` on `.active` widens the box by 3px and
    // moves the label. Recolouring an already-reserved rail does not.
    for (const css of [globalCss, scopedCss]) {
      for (const rule of code(css).split("}")) {
        const [selector, body = ""] = rule.split("{");
        if (!selector.includes(".active") && !selector.includes(".droppable")) {
          continue;
        }
        expect(
          body,
          `${selector.trim()} must recolour the rail, not re-declare it`,
        ).not.toMatch(/border-left\s*:\s*\d/);
      }
    }
  });
});

describe("optional glyphs keep their box", () => {
  it("hides an absent glyph with visibility, not display", () => {
    const body = ruleBody(globalCss, ".sidebar-row-glyph--empty");
    expect(body).toContain("visibility: hidden");
    expect(body).not.toContain("display");
  });

  it("keeps a fixed width so an absent glyph still holds its column", () => {
    const body = ruleBody(globalCss, ".sidebar-row-glyph");
    expect(body).toContain("width: var(--gutter-glyph)");
  });

  it("an empty caption goes inert but keeps its add action operable", () => {
    // The way out of an empty group is its `+`. Fading or disabling the whole
    // row would take that with it and strand the user.
    const body = ruleBody(
      scopedCss,
      ".sidebar-project-tree-subheader--empty .sidebar-project-tree-subheader-label",
    );
    expect(body, "the label, not the row, carries the fade").toContain(
      "opacity: var(--opacity-disabled",
    );

    const sideBar = read("./SideBar.vue");
    // The chevron's visibility and the row's inert state read the SAME helper,
    // so they cannot disagree.
    for (const group of ["projectHasPeople", "projectHasSets"]) {
      expect(sideBar).toContain(`'sidebar-row-glyph--empty': !${group}(p.id)`);
      expect(sideBar).toContain(
        `:aria-disabled="!${group}(p.id) || undefined"`,
      );
    }
  });

  it("rank is carried by type, not indent alone", () => {
    // A project and the captions nested under it must differ in type. When they
    // did not, indent had to signal rank as well as depth, which is what forced
    // a 24px step and pushed names to 91px in a 240px rail.
    const project = ruleBody(scopedCss, ".sidebar-project-tree-label");
    const caption = ruleBody(
      scopedCss,
      ".sidebar-project-tree-subheader-label",
    );
    expect(project).toContain("font-size: var(--text-sm)");
    expect(caption).toContain("font-size: var(--text-2xs)");
  });

  it("no structural label or caret in the project tree is dimmed", () => {
    // Three separate defects came out of dimming things in this column:
    //   - caption at 0.7 became quieter than the names it heads, inverting the
    //     hierarchy;
    //   - its caret was not dimmed with it, so the caret outshone its label,
    //     while the project's caret at 0.5 was far dimmer than its own;
    //   - a caret at 0.7 of a 0.7 label measures 2.95:1 in light, under the
    //     3:1 a disclosure glyph owes as a graphical object (WCAG 1.4.11).
    // Rank is size, weight, tracking and indent. Not opacity.
    for (const sel of [
      ".sidebar-project-tree-label",
      ".sidebar-project-tree-chevron",
      ".sidebar-project-tree-subheader-label",
      ".sidebar-project-tree-sub-chevron",
    ]) {
      for (const body of ruleBodies(scopedCss, sel)) {
        expect(body, `${sel} must not carry its own opacity`).not.toMatch(
          /opacity\s*:/,
        );
      }
    }
  });

  it("leaves no v-show or display:none on a reserved glyph", () => {
    // Both remove the box and the row jumps. Two separate local hacks existed
    // to patch this after the fact; neither should come back.
    expect(scopedCss).not.toContain('[style*="display: none"]');
    const sideBar = read("./SideBar.vue");
    expect(sideBar).not.toContain('style="visibility: hidden"');
    for (const m of sideBar.matchAll(/<v-icon[^>]*sidebar-row-glyph[^>]*>/g)) {
      expect(m[0]).not.toContain("v-show");
    }
  });
});
