// The pull summary's sentences (#1440). The rules they exist to keep:
// a verdict names the machine it is about, "won't run" is never "broken",
// and anything unchecked says so rather than reading as fine.

import { describe, expect, it } from "vitest";

import { comfyuiHost, pullSummaryLines } from "./workflowPull";

const URL = "http://192.0.2.10:8188";

const kinds = (report) => report.lines.map((line) => line.kind);

describe("pullSummaryLines", () => {
  it("counts what was pulled and names the machine", () => {
    const report = pullSummaryLines(
      { listed: 3, pulled: 1, matched: 1, already_shipped: 1, nodes_checked: true },
      URL,
    );
    expect(report.headline).toBe(
      "Found 3 workflows on 192.0.2.10:8188: 1 new, 1 already here, 1 shipped with PixlStash.",
    );
  });

  it("says a ComfyUI with nothing saved has nothing, rather than 0 of anything", () => {
    expect(pullSummaryLines({ listed: 0, nodes_checked: true }, URL).headline).toBe(
      "192.0.2.10:8188 has no saved workflows to pull.",
    );
  });

  it("gives missing nodes, missing models and unread models three kinds", () => {
    const report = pullSummaryLines(
      {
        listed: 5,
        pulled: 5,
        nodes_checked: true,
        missing_nodes: 2,
        missing_node_classes: ["A", "B"],
        missing_models: 1,
        missing_model_files: ["x.safetensors"],
        models_unread: 3,
      },
      URL,
    );
    const byKind = Object.fromEntries(report.lines.map((l) => [l.kind, l]));
    expect(byKind.error.text).toBe(
      "2 workflows won't run on 192.0.2.10:8188: they use nodes that ComfyUI doesn't have.",
    );
    expect(byKind.error.names).toEqual(["A", "B"]);
    expect(byKind.warning.text).toBe(
      "1 workflow names a model file 192.0.2.10:8188 doesn't list.",
    );
    expect(byKind.unchecked.text).toBe(
      "Not checked on 192.0.2.10:8188: 3 model files in loaders PixlStash can't read.",
    );
    for (const line of report.lines) expect(line.text).not.toMatch(/broken/i);
    // The glyph carries the kind, so no two kinds may share one.
    const glyphs = new Set(
      report.lines.filter((l) => l.kind !== "info").map((l) => l.icon),
    );
    expect(glyphs.size).toBe(
      report.lines.filter((l) => l.kind !== "info").length,
    );
  });

  it("never reads as fine when ComfyUI could not be asked", () => {
    const report = pullSummaryLines(
      { listed: 4, pulled: 4, nodes_checked: false, nodes_unchecked: 4 },
      URL,
    );
    expect(kinds(report)).toContain("unchecked");
    expect(kinds(report)).not.toContain("error");
    expect(report.lines.find((l) => l.kind === "unchecked").text).toBe(
      "Not checked: PixlStash couldn't read which nodes and models 192.0.2.10:8188 has.",
    );
  });

  it("says where new workflows went, and that a hand-deleted file returns", () => {
    const report = pullSummaryLines(
      { listed: 2, pulled: 1, skipped_dismissed: 1, nodes_checked: true },
      URL,
    );
    const text = report.lines.map((l) => l.text).join(" ");
    expect(text).toContain(
      "A pulled workflow with no pictures yet counts as a one-off",
    );
    expect(report.lines.find((l) => l.action).action).toBe("show-one-offs");
    expect(text).toContain("1 workflow you deleted here was left out");
    expect(text).toContain(
      "One removed by hand from PixlStash's workflows folder comes back.",
    );
  });

  it("says nothing about hidden one-offs while the grid shows them", () => {
    const report = pullSummaryLines(
      { listed: 2, pulled: 2, nodes_checked: true },
      URL,
      { hideOneOffs: false },
    );
    expect(report.lines.some((l) => l.action)).toBe(false);
    // The delete rule is still said: it is true whatever the filter.
    expect(report.lines.map((l) => l.text).join(" ")).toContain(
      "stays out of later pulls",
    );
  });

  it("starts a sentence about an unnamed ComfyUI with a capital", () => {
    expect(pullSummaryLines({ listed: 0 }, null).headline).toBe(
      "This ComfyUI has no saved workflows to pull.",
    );
  });

  it("every unchecked line says it was not checked", () => {
    const report = pullSummaryLines(
      { listed: 3, nodes_checked: true, nodes_unchecked: 1, models_unread: 1 },
      URL,
    );
    const unchecked = report.lines.filter((l) => l.kind === "unchecked");
    expect(unchecked).toHaveLength(2);
    for (const line of unchecked) expect(line.text).toMatch(/^Not checked/);
  });
});

describe("comfyuiHost", () => {
  it("is the host of the URL, or a plain name when there is none", () => {
    expect(comfyuiHost(URL)).toBe("192.0.2.10:8188");
    expect(comfyuiHost(null)).toBe("this ComfyUI");
    expect(comfyuiHost("not a url")).toBe("not a url");
  });
});

describe("a workflow edited in ComfyUI", () => {
  it("is counted as changed and said to sit beside the earlier copy", () => {
    const report = pullSummaryLines(
      { listed: 3, pulled: 1, changed: 1, matched: 1, nodes_checked: true },
      URL,
    );
    expect(report.headline).toBe(
      "Found 3 workflows on 192.0.2.10:8188: 1 new, 1 changed, 1 already here.",
    );
    expect(report.lines.map((l) => l.text).join(" ")).toContain(
      "1 workflow was edited in 192.0.2.10:8188 since the last pull. The new version is stored beside the earlier copy, which stays.",
    );
  });
});
