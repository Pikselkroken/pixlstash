// The auto-stack dialog is the single consent for a bulk change, so these tests
// pin what the user is promised before they give it: the full row list
// including the zero deletions, a stable layout while the dry run lands, and a
// confirm button that cannot fire on numbers nobody has seen.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import DedupAutoStackDialog from "./DedupAutoStackDialog.vue";
import AppButton from "./AppButton.vue";

const AppDialogStub = {
  name: "AppDialog",
  template:
    "<div><slot name='header-right'/><slot/><slot name='footer'/></div>",
};

const globalOpts = {
  global: { stubs: { "v-icon": true, AppDialog: AppDialogStub } },
};

// The backend's dry-run report. Every figure about the run rides in
// `dry_run_summary`, derived from one read of one group list so the rows cannot
// disagree with each other. `groups_by_tier` counts only what the run would act
// on (exact-only), so the "left in the queue" figure is deliberately a separate
// prop fed from the counts endpoint's per-tier split.
const SUMMARY = {
  groups: 1240,
  groups_by_tier: { exact: 1240, near: 0, embedding: 0 },
  pictures: 2600,
  covers_gaining_tags: 300,
  covers_gaining_score: 120,
  covers_gaining_metadata: 318,
};

const PREVIEW = {
  dry_run: true,
  groups: 1240,
  pictures: 2600,
  dry_run_summary: SUMMARY,
  failures: [],
};

function mountDialog(props = {}) {
  return mount(DedupAutoStackDialog, {
    ...globalOpts,
    props: { open: true, preview: PREVIEW, queueRemaining: 88, ...props },
  });
}

/** The footer buttons, in render order: Cancel, Create N stacks. */
function footerButtons(wrapper) {
  return wrapper.findAllComponents(AppButton);
}

describe("DedupAutoStackDialog: the dry run", () => {
  it("states the zero deletions out loud alongside the other counts", () => {
    // "Nothing is deleted" is the invariant the whole feature rests on; hiding
    // the row because it is zero is how the user stops believing it.
    const rows = mountDialog().findAll(".as-row");
    expect(rows).toHaveLength(5);
    expect(rows[4].find(".as-term").text()).toContain("Files deleted");
    expect(rows[4].find(".as-value").text()).toBe("0");
    expect(rows[0].find(".as-value").text()).toBe((1240).toLocaleString());
    expect(rows[1].find(".as-value").text()).toBe((2600).toLocaleString());
  });

  // The design promises this row, and it is the answer to the only real
  // question a bulk stack raises: whether collapsing copies loses anything.
  it("reports how many covers gain metadata from the copies", () => {
    const rows = mountDialog().findAll(".as-row");
    expect(rows[2].find(".as-term").text()).toContain(
      "Covers gaining metadata",
    );
    expect(rows[2].find(".as-value").text()).toBe((318).toLocaleString());
  });

  // One read, one set of numbers. Taking the run's counts off the top level
  // while the metadata row came from the summary is how two rows of the same
  // dialog end up describing two different moments.
  it("reads the run's counts from the dry-run summary, not the envelope", () => {
    const rows = mountDialog({
      preview: {
        ...PREVIEW,
        groups: 7,
        pictures: 9,
        dry_run_summary: SUMMARY,
      },
    }).findAll(".as-row");
    expect(rows[0].find(".as-value").text()).toBe((1240).toLocaleString());
    expect(rows[1].find(".as-value").text()).toBe((2600).toLocaleString());
  });

  // A server that predates the summary still gets a working dialog rather than
  // a column of zeroes over a live Create button.
  it("falls back to the envelope when no summary is served", () => {
    const rows = mountDialog({
      preview: { dry_run: true, groups: 12, pictures: 30, failures: [] },
    }).findAll(".as-row");
    expect(rows[0].find(".as-value").text()).toBe("12");
    expect(rows[1].find(".as-value").text()).toBe("30");
    expect(rows[2].find(".as-value").text()).toBe("0");
  });

  it("keeps every row in place while the dry run is in flight", () => {
    // Swapping the rows for a spinner would resize the dialog and move the
    // confirm button out from under the pointer when the counts land.
    const rows = mountDialog({ preview: null, loading: true }).findAll(
      ".as-row",
    );
    expect(rows).toHaveLength(5);
    // The three rows the dry run reports go to placeholders; the queue count
    // and the zero deletions are known without it and stay readable.
    expect(rows.map((r) => r.find(".as-value").text())).toEqual([
      "–",
      "–",
      "–",
      "88",
      "0",
    ]);
  });

  it("spells the undo out in keycaps", () => {
    const kbds = mountDialog().findAll(".as-reversible kbd");
    expect(kbds.map((k) => k.text())).toEqual(["Ctrl", "Z"]);
  });
});

describe("DedupAutoStackDialog: the confirmation", () => {
  it("refuses to confirm before the counts land, or when there is nothing to stack", () => {
    // Confirming during the dry run would act on numbers the user never saw.
    const loading = footerButtons(
      mountDialog({ preview: null, loading: true }),
    )[1];
    expect(loading.find("button").attributes("disabled")).toBeDefined();

    const empty = footerButtons(
      mountDialog({
        preview: {
          ...PREVIEW,
          groups: 0,
          dry_run_summary: {
            ...SUMMARY,
            groups: 0,
            groups_by_tier: { exact: 0, near: 0, embedding: 0 },
          },
        },
      }),
    )[1];
    expect(empty.find("button").attributes("disabled")).toBeDefined();
  });

  // A failed dry run and a genuinely empty one must not be the same screen: a
  // column of zeroes reads as "there is nothing to stack" when the truth is
  // "nobody was able to ask".
  it("says so when the dry run could not be read", () => {
    const wrapper = mountDialog({ preview: null, previewFailed: true });
    expect(wrapper.find(".as-failed").exists()).toBe(true);
    expect(
      wrapper.findAll(".as-row").map((r) => r.find(".as-value").text()),
    ).toEqual(["–", "–", "–", "88", "0"]);
    expect(
      footerButtons(wrapper)[1].find("button").attributes("disabled"),
    ).toBeDefined();
  });

  it("stays quiet about failure on a dry run that worked", () => {
    expect(mountDialog().find(".as-failed").exists()).toBe(false);
  });

  // The consent has to describe what the run actually does to the copies it
  // collapses, or the user is agreeing to a word ("stack") rather than an
  // outcome.
  it("promises that the other copies and their metadata survive", () => {
    const lede = mountDialog().find(".as-lede").text();
    expect(lede).toContain("stays in your library");
    expect(lede).toContain("moves onto the stack");
  });

  it("names the count it is about to create and emits the confirmation", () => {
    const wrapper = mountDialog();
    const button = footerButtons(wrapper)[1];
    expect(button.text()).toContain(`Create ${(1240).toLocaleString()} stacks`);

    button.find("button").trigger("click");
    expect(wrapper.emitted("confirm")).toHaveLength(1);
  });
});
