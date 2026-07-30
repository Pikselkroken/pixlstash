// The streaming "still scanning" banner above the duplicate queue.
//
// The banner is what makes a partial queue honest, so the tests pin when it
// appears and disappears, that it never states a figure it does not have, and
// that its progress track is announced as a progress bar rather than as a
// decorative strip.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import DedupScanBanner from "./DedupScanBanner.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

/** The store's normalised scan record, which is what the banner is given. */
function scan(overrides = {}) {
  return {
    status: "running",
    scanned: 1200,
    total: 48000,
    percent: 2.5,
    buckets: 0,
    totalBuckets: 0,
    error: null,
    ...overrides,
  };
}

/** Text with runs of whitespace collapsed, so template wrapping is not asserted. */
function textOf(wrapper) {
  return wrapper.text().replace(/\s+/g, " ");
}

function mountBanner(overrides = {}) {
  return mount(DedupScanBanner, {
    ...globalOpts,
    props: { scan: scan(overrides) },
  });
}

describe("DedupScanBanner", () => {
  it("reports how far the comparison has got", () => {
    // Without the counts a short queue reads as "you have almost no
    // duplicates" rather than "the scan is 2% in".
    const text = textOf(mountBanner());
    expect(text).toContain("Still scanning.");
    expect(text).toContain(
      `${(1200).toLocaleString()} of ${(48000).toLocaleString()} pictures compared.`,
    );
    expect(text).toContain("Groups appear here as they are found.");
  });

  // Tier 2 streams its groups in as each candidate bucket finishes, so a scope
  // whose picture total is not known yet still has honest progress to report.
  it("counts candidate batches when there is no picture total yet", () => {
    const text = textOf(
      mountBanner({ total: 0, scanned: 0, buckets: 3, totalBuckets: 12 }),
    );
    expect(text).toContain("3 of 12 candidate batches compared.");
    expect(text).not.toContain("pictures compared");
  });

  it("renders nothing once the scan reaches 100 percent", () => {
    // A banner that stays up after the pass finishes tells the user the list
    // is still incomplete when it is not.
    expect(mountBanner({ percent: 100 }).find(".scan-banner").exists()).toBe(
      false,
    );
  });

  it("renders nothing when the scan is not running", () => {
    // A finished, idle or failed scan must not leave a banner frozen at 99
    // percent. `pending` and `running` are the only live statuses.
    for (const status of ["idle", "complete", "failed"]) {
      expect(
        mountBanner({ status, percent: 40 }).find(".scan-banner").exists(),
      ).toBe(false);
    }
    expect(
      mountBanner({ status: "pending", percent: 0 })
        .find(".scan-banner")
        .exists(),
    ).toBe(true);
  });

  // The server reports no time estimate, and inventing one from a bucket rate
  // would be a guess presented as a fact. A wrong estimate is worse than none.
  it("states no time remaining, because the server does not know one", () => {
    expect(textOf(mountBanner())).not.toContain("min left");
  });

  it("announces the track as a progress bar", () => {
    // The track is the only progress readout; unlabelled it is invisible to a
    // screen reader (WCAG 1.3.1).
    const track = mountBanner({ percent: 42 }).find(".scan-banner__track");
    expect(track.attributes("role")).toBe("progressbar");
    expect(track.attributes("aria-label")).toBe("Duplicate scan progress");
    expect(track.attributes("aria-valuenow")).toBe("42");
    expect(track.attributes("aria-valuemin")).toBe("0");
    expect(track.attributes("aria-valuemax")).toBe("100");
  });

  it("fills the track to the same figure it prints", () => {
    // A bar and a number that disagree make the whole readout untrustworthy.
    const wrapper = mountBanner({ percent: 42.4 });
    expect(textOf(wrapper)).toContain("42%");
    expect(wrapper.find(".scan-banner__fill").attributes("style")).toContain(
      "width: 42%",
    );
  });

  it("treats unknown progress as zero instead of NaN", () => {
    // The first poll of a fresh scan can arrive without a percent.
    const wrapper = mountBanner({ percent: undefined });
    expect(textOf(wrapper)).toContain("0%");
    expect(
      wrapper.find(".scan-banner__track").attributes("aria-valuenow"),
    ).toBe("0");
  });
});
