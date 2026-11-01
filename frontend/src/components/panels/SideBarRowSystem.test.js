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
// The scoped sheets: Media's own, and the nav rows every workspace's sidebar
// shares (SidebarNav.css), checked as one.
const scopedCss = read("./SideBar.css") + "\n" + read("./SidebarNav.css");

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
  it("lives in the unscoped sheet", () => {
    expect(globalCss).toContain(".ps-row-glyph");
    expect(globalCss).toContain("--indent-step");
  });

  it("the refused-drop state is unscoped too", () => {
    // #757 shipped `.not-droppable` twice, and a second copy is how the two
    // drift apart again.
    expect(globalCss).toContain(".sidebar-list-item.not-droppable");
    expect(scopedCss).not.toContain("not-droppable");
  });
});

describe("every workspace's sidebar is one sidebar", () => {
  // Media, Workflows and Models each drew their own rows and only Media could
  // be resized; the three drifted apart in glyph size, gaps and type.
  for (const file of [
    "./SideBar.vue",
    "./WorkflowsSidebar.vue",
    "./ModelsSideBar.vue",
  ]) {
    it(`${file} wears the shared nav sheet, rows and resize handle`, () => {
      const sfc = read(file);
      expect(sfc).toContain('<style scoped src="./SidebarNav.css"></style>');
      expect(sfc).toMatch(/<SidebarResizeHandle\b/);
      expect(sfc).toMatch(/class="[^"]*\bsidebar-panel\b/);
      expect(sfc).toMatch(/class="[^"]*\bsidebar-nav-list\b/);
      expect(sfc).toContain('class="sidebar-list-icon');
      expect(sfc).toContain('class="sidebar-section-header"');
    });
  }

  it("Media imports the shared sheet before its own, so its rules refine it", () => {
    const sfc = read("./SideBar.vue");
    expect(sfc.indexOf('src="./SidebarNav.css"')).toBeLessThan(
      sfc.indexOf('src="./SideBar.css"'),
    );
  });
});

describe("every row starts at one left edge", () => {
  const ROW_TYPES = [
    ".sidebar-list-item",
    ".sidebar-section-header",
  ];

  it("derives padding-left from depth in one shared rule", () => {
    const body = ruleBody(globalCss, ROW_TYPES[0]);
    expect(body).toBeTruthy();
    expect(body).toContain("var(--depth, 0) * var(--indent-step)");
  });

  it("covers every row type in that rule", () => {
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
    const body = ruleBody(globalCss, ".ps-row-glyph--empty");
    expect(body).toContain("visibility: hidden");
    expect(body).not.toContain("display");
  });

  it("keeps a fixed width so an absent glyph still holds its column", () => {
    const body = ruleBody(globalCss, ".ps-row-glyph");
    expect(body).toContain("width: var(--gutter-glyph)");
  });

  it("section heads wear the section label's ink, and only that", () => {
    // Full ink made an 11px caps head read as one more row (design "Quiet
    // label"). The head takes .section-label's 70%; nothing dims it further.
    const ink =
      "color: rgba(var(--v-theme-sidebar-text), var(--opacity-text-secondary))";
    expect(
      ruleBodies(scopedCss, ".sidebar-section-header").some((b) =>
        b.includes(ink),
      ),
    ).toBe(true);
    for (const sel of [
      ".sidebar-section-header",
      ".sidebar-section-title",
      ".sidebar-media-list .sidebar-section-header",
    ]) {
      for (const body of ruleBodies(scopedCss, sel)) {
        expect(body, `${sel} must not carry its own opacity`).not.toMatch(
          /(^|[;{\s])opacity\s*:/,
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

describe("a hovered row's thumbnail stays put", () => {
  it("no thumbnail rule moves or zooms the picture", () => {
    // A 4% hover zoom on a 20px picture resampled it and made it flutter.
    // Hover is the row's wash (visual-language.md §11), not the picture.
    const thumbs = [
      ".sidebar-character-thumb",
      ".sidebar-set-thumb-image",
      ".sidebar-collapsed-flyout-thumb",
    ];
    for (const rule of code(scopedCss).split("}")) {
      // The last two parts, so a rule nested in @media keeps its selector.
      const parts = rule.split("{");
      if (parts.length < 2) continue;
      const [sel, body] = parts.slice(-2);
      if (!thumbs.some((t) => sel.includes(t))) continue;
      expect(body, sel.trim()).not.toMatch(/transform|scale|translate|zoom/);
    }
  });
});
