// The one location draw (#2819): opened for a location, or on the draw under
// way when that is another location's.

import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useLocationDrawStore } from "./useLocationDrawStore";
import { useNoticeStore } from "./useNoticeStore";

beforeEach(() => setActivePinia(createPinia()));

describe("the location draw", () => {
  it("opens for the location asked, or a new one", () => {
    const draws = useLocationDrawStore();
    draws.openFor({ id: 30 });
    expect(draws.dialogOpen).toBe(true);
    expect(draws.dialogLocation).toEqual({ id: 30 });
    draws.closeDialog();
    draws.openFor(null);
    expect(draws.dialogLocation).toBeNull();
  });

  it("opens on the draw under way rather than start a second, and says so", () => {
    const draws = useLocationDrawStore();
    draws.openFor({ id: 30 });
    draws.report({ locationId: 30, name: "The pub", status: "Filming the place…", fraction: 0.3 });
    draws.closeDialog();
    draws.openFor({ id: 31 });
    expect(draws.dialogOpen).toBe(true);
    expect(draws.dialogLocation).toEqual({ id: 30 });
    expect(useNoticeStore().notices.at(-1)?.text).toContain("The pub is still being drawn");
    // Its own location opens it as it is.
    draws.closeDialog();
    draws.openFor({ id: 30 });
    expect(draws.dialogLocation).toEqual({ id: 30 });
  });

  it("merges each report into the draw's summary, and ends it on null", () => {
    const draws = useLocationDrawStore();
    draws.report({ locationId: null, name: "Yard", status: "Drawing…", fraction: 0.1 });
    draws.report({ status: "Wide pictures to choose from", fraction: 1, ready: true });
    expect(draws.job).toEqual({ locationId: null, name: "Yard", status: "Wide pictures to choose from", fraction: 1, ready: true });
    expect(draws.jobFor(null)).toBe(draws.job);
    expect(draws.jobFor(30)).toBeNull();
    draws.report(null);
    expect(draws.job).toBeNull();
  });
});
