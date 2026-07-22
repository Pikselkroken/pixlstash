import { describe, it, expect } from "vitest";
import {
  DEFAULT_RETENTION_DAYS,
  EXEMPT_REASON_LOCKED,
  EXEMPT_REASON_PROTECTED,
  LOCKED_BADGE_LABEL,
  LOCKED_BADGE_TITLE,
  NEVER_SELECT_VALUE,
  PROTECTED_BADGE_LABEL,
  PROTECTED_BADGE_TITLE,
  RETENTION_DAY_OPTIONS,
  buildPurgeBadge,
  daysUntilPurge,
  normalizeRetentionDays,
  parseServerTimestamp,
  purgeCountdownLabel,
  resolveExemptReason,
  retentionLabel,
  retentionSelectOptions,
  retentionToSelectValue,
  selectValueToRetention,
} from "./retention.js";

describe("normalizeRetentionDays", () => {
  it("preserves an explicit null as Never", () => {
    expect(normalizeRetentionDays(null)).toBeNull();
  });

  it("keeps every offered window", () => {
    for (const days of RETENTION_DAY_OPTIONS) {
      expect(normalizeRetentionDays(days)).toBe(days);
    }
  });

  it("accepts a numeric string", () => {
    expect(normalizeRetentionDays("60")).toBe(60);
  });

  it("keeps an out-of-band positive value rather than snapping it", () => {
    expect(normalizeRetentionDays(45)).toBe(45);
  });

  it("falls back when the server said nothing usable", () => {
    expect(normalizeRetentionDays(undefined)).toBe(DEFAULT_RETENTION_DAYS);
    expect(normalizeRetentionDays("")).toBe(DEFAULT_RETENTION_DAYS);
    expect(normalizeRetentionDays("soon")).toBe(DEFAULT_RETENTION_DAYS);
    expect(normalizeRetentionDays(0)).toBe(DEFAULT_RETENTION_DAYS);
    expect(normalizeRetentionDays(-5)).toBe(DEFAULT_RETENTION_DAYS);
  });

  it("honours a caller-supplied fallback", () => {
    expect(normalizeRetentionDays(undefined, null)).toBeNull();
  });
});

describe("retentionLabel", () => {
  it("labels a window in days", () => {
    expect(retentionLabel(30)).toBe("30 days");
    expect(retentionLabel(1)).toBe("1 day");
  });

  it("labels null as Never", () => {
    expect(retentionLabel(null)).toBe("Never");
    expect(retentionLabel(undefined)).toBe("Never");
  });
});

describe("select value round-trip", () => {
  it("maps days to and from the select string", () => {
    expect(retentionToSelectValue(90)).toBe("90");
    expect(selectValueToRetention("90")).toBe(90);
  });

  it("maps Never to and from the sentinel", () => {
    expect(retentionToSelectValue(null)).toBe(NEVER_SELECT_VALUE);
    expect(selectValueToRetention(NEVER_SELECT_VALUE)).toBeNull();
  });

  it("treats an unparseable select value as Never", () => {
    expect(selectValueToRetention("junk")).toBeNull();
    expect(selectValueToRetention(undefined)).toBeNull();
  });
});

describe("retentionSelectOptions", () => {
  it("offers the four windows plus Never", () => {
    const options = retentionSelectOptions(30);
    expect(options.map((o) => o.value)).toEqual([
      "30",
      "60",
      "90",
      "120",
      NEVER_SELECT_VALUE,
    ]);
  });

  it("keeps the list unchanged for Never", () => {
    expect(retentionSelectOptions(null)).toHaveLength(
      RETENTION_DAY_OPTIONS.length + 1,
    );
  });

  it("inserts an out-of-band current value in sorted order", () => {
    const options = retentionSelectOptions(45);
    expect(options.map((o) => o.value)).toEqual([
      "30",
      "45",
      "60",
      "90",
      "120",
      NEVER_SELECT_VALUE,
    ]);
  });

  it("prefers the server-declared choices, sorted and de-duplicated", () => {
    const options = retentionSelectOptions(14, [180, 14, 14, 7]);
    expect(options.map((o) => o.value)).toEqual([
      "7",
      "14",
      "180",
      NEVER_SELECT_VALUE,
    ]);
  });

  it("falls back to the local list when the server sent nothing usable", () => {
    expect(retentionSelectOptions(30, []).map((o) => o.value)).toEqual([
      "30",
      "60",
      "90",
      "120",
      NEVER_SELECT_VALUE,
    ]);
    expect(retentionSelectOptions(30, null).map((o) => o.value)).toEqual([
      "30",
      "60",
      "90",
      "120",
      NEVER_SELECT_VALUE,
    ]);
  });
});

describe("parseServerTimestamp", () => {
  it("treats a naive ISO string as UTC", () => {
    expect(parseServerTimestamp("2026-08-01T00:00:00")).toBe(
      Date.UTC(2026, 7, 1),
    );
  });

  it("leaves an explicit Z alone", () => {
    expect(parseServerTimestamp("2026-08-01T00:00:00Z")).toBe(
      Date.UTC(2026, 7, 1),
    );
  });

  it("leaves a numeric offset alone", () => {
    expect(parseServerTimestamp("2026-08-01T02:00:00+02:00")).toBe(
      Date.UTC(2026, 7, 1),
    );
  });

  it("returns null for absent or unparseable input", () => {
    expect(parseServerTimestamp(null)).toBeNull();
    expect(parseServerTimestamp("")).toBeNull();
    expect(parseServerTimestamp("not a date")).toBeNull();
    expect(parseServerTimestamp(12345)).toBeNull();
  });
});

describe("daysUntilPurge", () => {
  const now = Date.UTC(2026, 6, 22, 12, 0, 0);

  it("rounds a partial day up", () => {
    expect(daysUntilPurge("2026-07-22T18:00:00Z", now)).toBe(1);
    expect(daysUntilPurge("2026-07-24T00:00:00Z", now)).toBe(2);
  });

  it("counts whole days", () => {
    expect(daysUntilPurge("2026-08-21T12:00:00Z", now)).toBe(30);
  });

  it("clamps a past timestamp at zero", () => {
    expect(daysUntilPurge("2026-07-01T00:00:00Z", now)).toBe(0);
  });

  it("returns null when nothing is scheduled", () => {
    expect(daysUntilPurge(null, now)).toBeNull();
    expect(daysUntilPurge(undefined, now)).toBeNull();
  });
});

describe("purgeCountdownLabel", () => {
  it("renders the countdown", () => {
    expect(purgeCountdownLabel(12)).toBe("12 days left");
    expect(purgeCountdownLabel(1)).toBe("1 day left");
  });

  it("names the last day explicitly", () => {
    expect(purgeCountdownLabel(0)).toBe("Purges today");
  });

  it("renders nothing when nothing is scheduled", () => {
    expect(purgeCountdownLabel(null)).toBe("");
    expect(purgeCountdownLabel(undefined)).toBe("");
  });
});

describe("resolveExemptReason", () => {
  it("reads an explicit protected reason", () => {
    expect(
      resolveExemptReason({
        auto_purge_exempt: true,
        auto_purge_exempt_reason: "protected",
      }),
    ).toBe(EXEMPT_REASON_PROTECTED);
  });

  it("reads an explicit locked reason", () => {
    expect(
      resolveExemptReason({
        auto_purge_exempt: true,
        auto_purge_exempt_reason: "locked",
      }),
    ).toBe(EXEMPT_REASON_LOCKED);
  });

  it("returns null for a picture that is not exempt", () => {
    expect(
      resolveExemptReason({
        auto_purge_exempt: false,
        auto_purge_exempt_reason: null,
      }),
    ).toBeNull();
  });

  it("trusts an explicit null reason over a stale exempt boolean", () => {
    expect(
      resolveExemptReason({
        auto_purge_exempt: false,
        auto_purge_exempt_reason: null,
      }),
    ).toBeNull();
  });

  // Version-skew guard: the reason field is newer than the boolean.
  it("falls back to protected when the server sent no reason", () => {
    expect(resolveExemptReason({ auto_purge_exempt: true })).toBe(
      EXEMPT_REASON_PROTECTED,
    );
  });

  it("falls back to protected for an unknown reason string", () => {
    expect(
      resolveExemptReason({
        auto_purge_exempt: true,
        auto_purge_exempt_reason: "some-future-reason",
      }),
    ).toBe(EXEMPT_REASON_PROTECTED);
  });

  it("stays null when neither field marks the picture exempt", () => {
    expect(resolveExemptReason({})).toBeNull();
    expect(resolveExemptReason(null)).toBeNull();
    expect(resolveExemptReason(undefined)).toBeNull();
  });
});

describe("buildPurgeBadge", () => {
  const now = Date.UTC(2026, 6, 22, 12, 0, 0);
  const formatDate = () => "21 Aug 2026";
  const build = (picture) => buildPurgeBadge(picture, { now, formatDate });

  it("badges a reference-folder original as protected", () => {
    const badge = build({
      auto_purge_exempt: true,
      auto_purge_exempt_reason: "protected",
      purge_at: null,
    });
    expect(badge).toEqual({
      kind: "protected",
      icon: "mdi-shield-check-outline",
      label: PROTECTED_BADGE_LABEL,
      title: PROTECTED_BADGE_TITLE,
    });
  });

  it("badges a locked-set picture distinctly, naming the set lock", () => {
    const badge = build({
      auto_purge_exempt: true,
      auto_purge_exempt_reason: "locked",
      purge_at: null,
    });
    expect(badge.kind).toBe("locked");
    expect(badge.icon).toBe("mdi-lock-outline");
    expect(badge.label).toBe(LOCKED_BADGE_LABEL);
    expect(badge.title).toBe(LOCKED_BADGE_TITLE);
    // The two exempt states must not be confusable.
    expect(badge.label).not.toBe(PROTECTED_BADGE_LABEL);
    expect(badge.title).toMatch(/locked set/i);
    expect(badge.title).toMatch(/unlock/i);
  });

  it("counts down for a non-exempt picture", () => {
    const badge = build({
      auto_purge_exempt: false,
      auto_purge_exempt_reason: null,
      purge_at: "2026-08-21T12:00:00Z",
    });
    expect(badge).toEqual({
      kind: "countdown",
      icon: "mdi-delete-clock-outline",
      label: "30 days left",
      title: "Auto-deletes 21 Aug 2026",
    });
  });

  it("emphasises the last day", () => {
    expect(
      build({ auto_purge_exempt: false, purge_at: "2026-07-23T00:00:00Z" })
        .kind,
    ).toBe("countdown-urgent");
    const today = build({
      auto_purge_exempt: false,
      purge_at: "2026-07-22T00:00:00Z",
    });
    expect(today.kind).toBe("countdown-urgent");
    expect(today.label).toBe("Purges today");
    expect(today.title).toBe("Auto-deletes today");
  });

  // The regression this field exists to fix: a locked picture used to be served
  // a past `purge_at`, which clamped to 0 and showed a permanent urgent badge.
  it("never shows an urgent countdown for a locked picture with a stale purge_at", () => {
    const badge = build({
      auto_purge_exempt: true,
      auto_purge_exempt_reason: "locked",
      purge_at: "2026-07-01T00:00:00Z",
    });
    expect(badge.kind).toBe("locked");
    expect(badge.label).not.toMatch(/today|left/i);
  });

  it("prefers protected when the backend reports both", () => {
    // The backend collapses the overlap to "protected"; honour that verbatim.
    expect(
      build({
        auto_purge_exempt: true,
        auto_purge_exempt_reason: "protected",
        purge_at: null,
      }).kind,
    ).toBe("protected");
  });

  it("returns no badge when nothing is scheduled and nothing is exempt", () => {
    expect(build({ auto_purge_exempt: false, purge_at: null })).toBeNull();
    expect(build(null)).toBeNull();
  });

  it("falls back to the ISO string when no date formatter is supplied", () => {
    const badge = buildPurgeBadge(
      { auto_purge_exempt: false, purge_at: "2026-08-21T12:00:00Z" },
      { now },
    );
    expect(badge.title).toBe("Auto-deletes 2026-08-21T12:00:00Z");
  });
});
