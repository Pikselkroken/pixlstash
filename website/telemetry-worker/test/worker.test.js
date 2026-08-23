/**
 * Tests for the telemetry ingestion Worker.
 *
 * The endpoint takes unauthenticated writes from the public internet, so the
 * rejection cases carry as much weight as the happy path: an unrecognised key,
 * an oversized body, a bad UUID, and a rate-limited caller must all fail
 * closed, and the response must never echo anything back.
 *
 * Run with: node --test
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import worker, {
  MAX_NEW_INSTALLS_PER_DAY,
  MAX_TOTAL_INSTALLS,
} from "../src/index.js";
import { validatePing, MAX_BODY_BYTES } from "../src/validate.js";
import {
  rollActivity,
  daysBetween,
  decodeActivity,
  encodeActivity,
  weekStart,
  ACTIVITY_BITS,
} from "../src/activity.js";
import {
  accumulateRow,
  buildAggregate,
  checkinsInActiveWindow,
  createAccumulator,
  deserializeAccumulator,
  finalizeAggregate,
  serializeAccumulator,
  hasResurrected,
  activeInLifeWeek,
  ACTIVE_WINDOW_DAYS,
  MIN_COHORT,
} from "../src/aggregate.js";
import { D1Stub, allowAll, denyAll, pingRequest } from "./d1-stub.js";

const UUID = "9f2c1b7e-4d5a-4c81-b3e6-8a7d2f0e5c14";
const UUID2 = "1b0d4e2a-77c3-4f19-9d6e-2c5a8b3f0e71";

const VALID = { install_id: UUID, is_new_install: true, install_type: "pip" };

function env(overrides = {}) {
  return { DB: new D1Stub(), RATE_LIMITER: allowAll, ...overrides };
}

// ---------------------------------------------------------------------------
// Validation: reject by default
// ---------------------------------------------------------------------------

describe("validatePing", () => {
  it("accepts a well-formed ping", () => {
    const result = validatePing({ ...VALID });
    assert.equal(result.ok, true);
    assert.equal(result.value.install_id, UUID);
  });

  it("rejects an unrecognised key rather than ignoring it", () => {
    const result = validatePing({ ...VALID, email: "someone@example.com" });
    assert.equal(result.ok, false);
    assert.match(result.reason, /unrecognised key/);
  });

  it("rejects a missing key", () => {
    const { install_type, ...partial } = VALID;
    assert.equal(validatePing(partial).ok, false);
  });

  for (const bad of [
    "not-a-uuid",
    "9F2C1B7E-4D5A-4C81-B3E6-8A7D2F0E5C14", // uppercase is not canonical
    "9f2c1b7e-4d5a-1c81-b3e6-8a7d2f0e5c14", // version 1, not 4
    "9f2c1b7e-4d5a-4c81-c3e6-8a7d2f0e5c14", // bad variant nibble
    "",
    12345,
  ]) {
    it(`rejects install_id ${JSON.stringify(bad)}`, () => {
      assert.equal(validatePing({ ...VALID, install_id: bad }).ok, false);
    });
  }

  it("rejects a non-boolean is_new_install", () => {
    assert.equal(validatePing({ ...VALID, is_new_install: "true" }).ok, false);
  });

  it("rejects an install_type outside the four buckets", () => {
    assert.equal(validatePing({ ...VALID, install_type: "snap" }).ok, false);
  });

  it("rejects arrays and null", () => {
    assert.equal(validatePing([VALID]).ok, false);
    assert.equal(validatePing(null).ok, false);
  });
});

// ---------------------------------------------------------------------------
// The activity bitmap
// ---------------------------------------------------------------------------

describe("rollActivity", () => {
  it("records today without rolling when no days have passed", () => {
    assert.equal(rollActivity(0b1n, 0), 0b1n);
  });

  it("shifts by the elapsed days and sets today", () => {
    assert.equal(rollActivity(0b1n, 1), 0b11n);
    assert.equal(rollActivity(0b11n, 2), 0b1101n);
  });

  it("drops everything once the whole window has aged out", () => {
    assert.equal(rollActivity((1n << 40n) | 1n, ACTIVITY_BITS), 1n);
    assert.equal(rollActivity(0xffffn, ACTIVITY_BITS + 10), 1n);
  });

  it("stays inside the deliberately bounded 63-day window", () => {
    let bits = 1n;
    for (let i = 0; i < 200; i++) bits = rollActivity(bits, 1);
    assert.ok(bits <= (1n << BigInt(ACTIVITY_BITS)) - 1n);
    assert.ok(bits > 0n, "must stay positive");
  });

  it("records without rolling backwards on a clock-skewed ping", () => {
    // A ping dated before last_seen must not rewrite history.
    assert.equal(rollActivity(0b110n, -5), 0b111n);
  });

  it("round-trips every bit through the D1-safe text encoding", () => {
    const bits = (1n << 62n) | (1n << 54n) | 0b101n;
    const encoded = encodeActivity(bits);
    assert.match(encoded, /^h[0-9a-f]{16}$/);
    assert.equal(decodeActivity(encoded), bits);
  });
});

describe("date helpers", () => {
  it("counts whole days between dates", () => {
    assert.equal(daysBetween("2026-08-01", "2026-08-08"), 7);
    assert.equal(daysBetween("2026-08-08", "2026-08-01"), -7);
    assert.equal(daysBetween("2026-02-27", "2026-03-01"), 2); // 2026 is not a leap year
  });

  it("buckets dates into ISO weeks starting Monday", () => {
    assert.equal(weekStart("2026-08-02"), "2026-07-27"); // a Sunday
    assert.equal(weekStart("2026-07-27"), "2026-07-27"); // the Monday itself
    assert.equal(weekStart("2026-07-31"), "2026-07-27");
  });
});

describe("hasResurrected", () => {
  it("is false for a continuously active install", () => {
    assert.equal(hasResurrected((1n << 30n) - 1n), false);
  });

  it("is false for a gap that was never closed", () => {
    // Active long ago, silent since: churned, not resurrected.
    assert.equal(hasResurrected(1n << 40n), false);
  });

  it("is true for a long silence broken by a later ping", () => {
    // Active 40 days ago, nothing until a ping today: a 39-day gap, closed.
    assert.equal(hasResurrected((1n << 40n) | 1n), true);
  });

  it("is false when the gap is shorter than the threshold", () => {
    // Active 10 days ago and today: a 9-day gap, below the 14-day threshold.
    assert.equal(hasResurrected((1n << 10n) | 1n), false);
  });
});

describe("activeInLifeWeek", () => {
  const row = { first_seen: "2026-07-01", last_seen: "2026-07-20", activity: 1n };
  const today = "2026-07-21";

  it("returns null for a week that has not happened yet", () => {
    assert.equal(activeInLifeWeek(row, 12, today), null);
  });

  it("finds the week containing the last ping", () => {
    // last_seen is day 19 of life, which falls in life-week 2 (days 14-20).
    assert.equal(activeInLifeWeek(row, 2, today), true);
  });

  it("reports a week with no pings as false, not null", () => {
    assert.equal(activeInLifeWeek(row, 0, today), false);
  });

  it("counts an elapsed week after the final ping as inactive", () => {
    const churned = {
      first_seen: "2026-07-01",
      last_seen: "2026-07-01",
      activity: 1n,
    };
    assert.equal(activeInLifeWeek(churned, 1, "2026-07-20"), false);
  });

  it("waits until the whole life-week has elapsed", () => {
    assert.equal(activeInLifeWeek(row, 2, "2026-07-20"), null);
  });
});

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

function cohortRows(count, firstSeen, lastSeen, activity) {
  return Array.from({ length: count }, (_, i) => ({
    install_id: `id-${firstSeen}-${i}`,
    first_seen: firstSeen,
    last_seen: lastSeen,
    activity,
    has_resurrected: 0,
    is_new_install: 1,
    install_type: "pip",
  }));
}

describe("buildAggregate", () => {
  it("suppresses a cohort below the minimum size rather than publishing it", () => {
    const rows = cohortRows(MIN_COHORT - 1, "2026-07-01", "2026-07-20", 1n);
    const agg = buildAggregate(rows, "2026-07-20");
    assert.deepEqual(agg.cohort_retention, {});
    assert.equal(agg.suppressed_cohorts, 1);
  });

  it("publishes a cohort at or above the minimum size", () => {
    const rows = cohortRows(MIN_COHORT, "2026-07-01", "2026-07-20", 1n);
    const agg = buildAggregate(rows, "2026-07-20");
    assert.equal(Object.keys(agg.cohort_retention).length, 1);
    assert.equal(agg.suppressed_cohorts, 0);
  });

  it("includes installs that churned after their first ping in later weeks", () => {
    const rows = cohortRows(
      MIN_COHORT,
      "2026-07-01",
      "2026-07-01",
      encodeActivity(1n),
    );
    const agg = buildAggregate(rows, "2026-07-20");
    assert.deepEqual(agg.cohort_retention["2026-06-29"], [100, 0]);
  });

  it("does not publish a cell from only a subset of its cohort", () => {
    const answerable = cohortRows(
      MIN_COHORT,
      "2026-07-01",
      "2026-07-01",
      encodeActivity(1n),
    );
    const agedOut = cohortRows(
      1,
      "2026-07-01",
      "2026-10-01",
      encodeActivity(1n),
    );
    const agg = buildAggregate([...answerable, ...agedOut], "2026-10-01");
    assert.deepEqual(agg.cohort_retention, {});
  });

  it("excludes upgrade-wave installs from cohorts", () => {
    // Enough rows to clear the floor, but none of them are new installs.
    const rows = cohortRows(MIN_COHORT + 5, "2026-07-01", "2026-07-20", 1n).map((r) => ({
      ...r,
      is_new_install: 0,
    }));
    const agg = buildAggregate(rows, "2026-07-20");
    assert.deepEqual(agg.cohort_retention, {});
    assert.equal(agg.suppressed_cohorts, 0, "not a suppressed cohort, simply not cohorted");
  });

  it("counts only recently-seen installs as active", () => {
    const recent = cohortRows(3, "2026-01-01", "2026-07-20", 1n);
    const stale = cohortRows(4, "2026-01-01", "2026-02-01", 1n);
    const agg = buildAggregate([...recent, ...stale], "2026-07-20");
    assert.equal(agg.active_installs, 3);
  });

  it("reports resurrection_rate as null when nothing is eligible yet", () => {
    const rows = cohortRows(3, "2026-07-19", "2026-07-20", 1n);
    const agg = buildAggregate(rows, "2026-07-20");
    // Null, not 0: "we cannot say yet" and "nobody returned" are different.
    assert.equal(agg.resurrection_rate, null);
  });

  it("keeps a resurrection after the original gap ages out of the bitmap", () => {
    const rows = [
      {
        install_id: "returned-and-stayed",
        first_seen: "2026-01-01",
        last_seen: "2026-07-20",
        // Dense recent activity no longer contains the old gap. The sticky
        // field is the durable record that the return happened.
        activity: encodeActivity((1n << 63n) - 1n),
        has_resurrected: 1,
        is_new_install: 0,
        install_type: "pip",
      },
    ];

    assert.equal(buildAggregate(rows, "2026-07-20").resurrection_rate, 100);
  });

  it("counts ID-bearing check-ins per type, not distinct installs", () => {
    const rows = [
      // Pinged on three days inside the window.
      { ...cohortRows(1, "2026-06-01", "2026-07-20", encodeActivity(0b1011n))[0] },
      // One electron install, one ping.
      {
        ...cohortRows(1, "2026-07-20", "2026-07-20", encodeActivity(1n))[0],
        install_type: "electron",
      },
    ];
    const agg = buildAggregate(rows, "2026-07-20");
    assert.deepEqual(agg.active_installs_by_type, {
      docker: 0,
      pip: 1,
      electron: 1,
      other: 0,
    });
    assert.deepEqual(agg.id_bearing_checkins_by_type, {
      docker: 0,
      pip: 3,
      electron: 1,
      other: 0,
    });
  });

  it("keeps dev in its own check-in bucket", () => {
    const rows = cohortRows(1, "2026-07-19", "2026-07-20", encodeActivity(0b11n)).map(
      (r) => ({ ...r, install_type: "dev" }),
    );
    const agg = buildAggregate(rows, "2026-07-20");
    assert.equal(agg.id_bearing_checkins_by_type.dev, 2);
    assert.equal(agg.active_installs_by_type.dev, 1);
  });

  it("excludes check-ins from installs that fell out of the active window", () => {
    const stale = cohortRows(1, "2026-01-01", "2026-02-01", encodeActivity(0b111n));
    const agg = buildAggregate(stale, "2026-07-20");
    assert.deepEqual(agg.id_bearing_checkins_by_type, {
      docker: 0,
      pip: 0,
      electron: 0,
      other: 0,
    });
  });

  it("publishes no identifiers", () => {
    const rows = cohortRows(MIN_COHORT, "2026-07-01", "2026-07-20", 1n);
    const serialised = JSON.stringify(buildAggregate(rows, "2026-07-20"));
    assert.ok(!serialised.includes("id-"), "aggregate must not contain install ids");
  });
});

// ---------------------------------------------------------------------------
// The endpoint
// ---------------------------------------------------------------------------

describe("POST /v1/ping", () => {
  it("accepts a valid ping with 204 and an empty body", async () => {
    const e = env();
    const res = await worker.fetch(pingRequest(VALID), e);
    assert.equal(res.status, 204);
    assert.equal(await res.text(), "");
    assert.equal(e.DB.installs.size, 1);
  });

  it("records first_seen, the new-install flag, and a fresh bitmap", async () => {
    const e = env();
    await worker.fetch(pingRequest(VALID), e);
    const row = e.DB.installs.get(UUID);
    assert.equal(row.activity, encodeActivity(1n));
    assert.equal(row.is_new_install, 1);
    assert.equal(row.install_type, "pip");
    assert.equal(row.first_seen, row.last_seen);
  });

  it("is idempotent within a day", async () => {
    const e = env();
    await worker.fetch(pingRequest(VALID), e);
    await worker.fetch(pingRequest(VALID), e);
    assert.equal(e.DB.installs.size, 1);
    assert.equal(decodeActivity(e.DB.installs.get(UUID).activity), 1n);
  });

  it("never lets a later ping rewrite first_seen or the new-install flag", async () => {
    const e = env();
    await worker.fetch(pingRequest(VALID), e);
    const before = { ...e.DB.installs.get(UUID) };
    await worker.fetch(pingRequest({ ...VALID, is_new_install: false }), e);
    const after = e.DB.installs.get(UUID);
    assert.equal(after.first_seen, before.first_seen);
    assert.equal(after.is_new_install, before.is_new_install);
  });

  it("records resurrection as a sticky fact when a long silence closes", async () => {
    const e = env();
    await worker.fetch(pingRequest(VALID), e);
    const row = e.DB.installs.get(UUID);
    const fifteenDaysAgo = new Date(Date.now() - 15 * 86400000)
      .toISOString()
      .slice(0, 10);
    row.last_seen = fifteenDaysAgo;
    row.activity = encodeActivity(1n);

    await worker.fetch(pingRequest(VALID), e);
    assert.equal(row.has_resurrected, 1);

    // Even after the bitmap no longer contains the gap, another update must
    // never clear the durable result.
    row.activity = encodeActivity((1n << 63n) - 1n);
    await worker.fetch(pingRequest(VALID), e);
    assert.equal(row.has_resurrected, 1);
  });

  it("records a return after the old ping has aged out of the bitmap", async () => {
    const e = env();
    await worker.fetch(pingRequest(VALID), e);
    const row = e.DB.installs.get(UUID);
    row.last_seen = new Date(Date.now() - 70 * 86400000)
      .toISOString()
      .slice(0, 10);
    row.activity = encodeActivity(1n);

    await worker.fetch(pingRequest(VALID), e);

    assert.equal(decodeActivity(row.activity), 1n);
    assert.equal(row.has_resurrected, 1);
  });

  it("rejects an unrecognised key without storing anything", async () => {
    const e = env();
    const res = await worker.fetch(pingRequest({ ...VALID, extra: 1 }), e);
    assert.equal(res.status, 400);
    assert.equal(e.DB.installs.size, 0);
  });

  it("rejects a body over the size cap", async () => {
    const e = env();
    const padded = JSON.stringify({ ...VALID, install_type: "pip" }).padEnd(
      MAX_BODY_BYTES + 100,
      " ",
    );
    const res = await worker.fetch(pingRequest(padded), e);
    assert.equal(res.status, 413);
    assert.equal(e.DB.installs.size, 0);
  });

  it("caps the read even when content-length lies", async () => {
    const e = env();
    const padded = "x".repeat(MAX_BODY_BYTES + 500);
    const res = await worker.fetch(pingRequest(padded, { "content-length": "10" }), e);
    assert.equal(res.status, 413);
  });

  it("rejects malformed JSON", async () => {
    const e = env();
    const res = await worker.fetch(pingRequest("{not json"), e);
    assert.equal(res.status, 400);
    assert.equal(e.DB.installs.size, 0);
  });

  it("refuses a rate-limited caller without writing", async () => {
    const e = env({ RATE_LIMITER: denyAll });
    const res = await worker.fetch(pingRequest(VALID), e);
    assert.equal(res.status, 429);
    assert.equal(e.DB.installs.size, 0);
  });

  it("rejects a content-type that would skip the CORS preflight", async () => {
    const e = env();
    // text/plain is CORS-safelisted, so a cross-origin POST carrying it is a
    // "simple request" and is sent without a preflight. Any site could then
    // make its visitors ping us from their own IPs, which per-IP rate limiting
    // cannot bound.
    for (const type of ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""]) {
      const res = await worker.fetch(
        pingRequest(VALID, { "content-type": type }),
        e,
      );
      assert.equal(res.status, 415, `content-type ${type || "(absent)"} must be refused`);
    }
    assert.equal(e.DB.installs.size, 0);
  });

  it("accepts application/json with parameters", async () => {
    const e = env();
    const res = await worker.fetch(
      pingRequest(VALID, { "content-type": "application/json; charset=utf-8" }),
      e,
    );
    assert.equal(res.status, 204);
  });

  it("fails closed when the rate limiter binding is missing", async () => {
    const e = { DB: new D1Stub() };
    const res = await worker.fetch(pingRequest(VALID), e);
    // Never accept writes with the only volume control silently absent.
    assert.equal(res.status, 503);
    assert.equal(e.DB.installs.size, 0);
  });

  it("rejects GET", async () => {
    const e = env();
    const res = await worker.fetch(new Request("https://t.pixlstash.dev/v1/ping"), e);
    assert.equal(res.status, 405);
  });

  it("never reflects submitted input in the response", async () => {
    const e = env();
    const res = await worker.fetch(
      pingRequest({ ...VALID, "<script>": "reflected-marker" }),
      e,
    );
    const text = await res.text();
    assert.ok(!text.includes("reflected-marker"));
    assert.ok(!text.includes("<script>"));
  });

  it("404s an unknown path", async () => {
    const e = env();
    const res = await worker.fetch(new Request("https://t.pixlstash.dev/v1/other"), e);
    assert.equal(res.status, 404);
  });
});

describe("activity bitmap durability", () => {
  it("survives past 53 days without collapsing", async () => {
    const e = env();
    // float64 has a 53-bit mantissa. Narrowing the bitmap through Number()
    // silently erased the whole history once an install crossed that, which is
    // precisely the long-lived install the retention curve exists to measure.
    let bits = 1n;
    for (let day = 1; day <= 62; day++) {
      bits = rollActivity(bits, 1);
    }
    const popcount = bits.toString(2).split("1").length - 1;
    assert.equal(popcount, 63, "63 consecutive daily pings must set 63 bits");
    assert.ok(bits > BigInt(Number.MAX_SAFE_INTEGER), "must exceed 2^53");
    assert.ok(bits <= (1n << 63n) - 1n, "must stay inside the 63-bit window");

    // The Worker binding must be a D1-safe string while preserving all 63 bits.
    await worker.fetch(pingRequest(VALID), e);
    const row = e.DB.installs.get(UUID);
    row.activity = encodeActivity(bits);
    row.last_seen = new Date().toISOString().slice(0, 10);
    await worker.fetch(pingRequest(VALID), e);
    const stored = e.DB.installs.get(UUID).activity;
    assert.equal(typeof stored, "string", "D1 must never receive a BigInt binding");
    assert.equal(decodeActivity(stored), bits, "all 63 bits must survive storage");
  });
});

describe("growth caps", () => {
  function idFor(n) {
    const hex = n.toString(16).padStart(12, "0");
    return `9f2c1b7e-4d5a-4c81-b3e6-${hex}`;
  }

  it("refuses a new install once the total ceiling is reached", async () => {
    const e = env();
    e.DB.counters.set("total_installs", { value: MAX_TOTAL_INSTALLS, day: null });

    const res = await worker.fetch(pingRequest(VALID), e);

    // Reported as ordinary rate limiting: telling a prober which cap they hit
    // tells them how close they are to exhausting it.
    assert.equal(res.status, 429);
    assert.equal(e.DB.installs.size, 0);
  });

  it("refuses once the daily new-install cap is reached", async () => {
    const e = env();
    e.DB.counters.set("new_installs_today", {
      value: MAX_NEW_INSTALLS_PER_DAY,
      day: new Date().toISOString().slice(0, 10),
    });

    const res = await worker.fetch(pingRequest(VALID), e);
    assert.equal(res.status, 429);
    assert.equal(e.DB.installs.size, 0);
  });

  it("ignores a daily counter left over from a previous day", async () => {
    const e = env();
    e.DB.counters.set("new_installs_today", {
      value: MAX_NEW_INSTALLS_PER_DAY,
      day: "2020-01-01",
    });

    const res = await worker.fetch(pingRequest(VALID), e);
    assert.equal(res.status, 204);
    assert.equal(e.DB.installs.size, 1);
  });

  it("still accepts updates from existing installs when the cap is hit", async () => {
    const e = env();
    await worker.fetch(pingRequest(VALID), e);
    // Capping updates would hand an attacker a denial of service against real
    // installs: flood until the cap trips and every genuine install goes dark.
    e.DB.counters.set("total_installs", { value: MAX_TOTAL_INSTALLS, day: null });

    const res = await worker.fetch(pingRequest(VALID), e);
    assert.equal(res.status, 204);
    assert.equal(e.DB.installs.size, 1);
  });

  it("fails closed when the counters cannot be read", async () => {
    const e = env();
    const realPrepare = e.DB.prepare.bind(e.DB);
    e.DB.prepare = (sql) => {
      if (sql.includes("FROM counter")) {
        return {
          bind: () => ({
            first: async () => {
              throw new Error("counter table unreadable");
            },
          }),
        };
      }
      return realPrepare(sql);
    };

    const res = await worker.fetch(pingRequest(VALID), e);
    // Storage failure is distinct from an intact capacity refusal, but still
    // fails closed and never inserts an uncapped row.
    assert.equal(res.status, 503);
    assert.equal(e.DB.installs.size, 0);
  });

  it("returns a clean refusal when storage fails outright", async () => {
    const e = env();
    e.DB.prepare = () => {
      throw new Error("D1 unavailable");
    };

    const res = await worker.fetch(pingRequest(VALID), e);
    // Not an unhandled throw rendered as Cloudflare's default 500 page.
    assert.equal(res.status, 503);
  });

  it("frees headroom when the prune removes rows", async () => {
    const e = env({ AGGREGATES_TOKEN: "t" });
    // The counter used to be a LIFETIME tally the prune never decremented, so
    // reaching the ceiling once refused every genuine new install for ever.
    e.DB.installs.set("stale", {
      install_id: "stale",
      first_seen: "2024-01-01",
      last_seen: "2024-01-02",
      activity: 1n,
      is_new_install: 1,
      install_type: "pip",
    });
    e.DB.counters.set("total_installs", { value: MAX_TOTAL_INSTALLS, day: null });

    await worker.scheduled({}, e);
    await worker.scheduled({}, e);

    assert.equal(e.DB.counters.get("total_installs").value, 0, "prune must free headroom");
    const res = await worker.fetch(pingRequest(VALID), e);
    assert.equal(res.status, 204, "a genuine new install must be accepted again");
  });

  it("counts creations without double-counting a repeat ping", async () => {
    const e = env();
    await worker.fetch(pingRequest(VALID), e);
    await worker.fetch(pingRequest(VALID), e);

    assert.equal(e.DB.counters.get("total_installs").value, 1);
  });

  it("walks every row when the table spans multiple scan pages", async () => {
    const e = env({ AGGREGATES_TOKEN: "t" });
    // More rows than one page, to prove the keyset cursor advances rather than
    // re-reading or truncating.
    for (let i = 0; i < 25; i++) {
      e.DB.installs.set(idFor(i), {
        install_id: idFor(i),
        first_seen: "2026-07-01",
        last_seen: new Date().toISOString().slice(0, 10),
        activity: 1,
        is_new_install: 1,
        install_type: "pip",
      });
    }
    await worker.scheduled({}, e);
    await worker.scheduled({}, e);

    const snapshot = JSON.parse([...e.DB.snapshots.values()][0]);
    assert.equal(snapshot.active_installs, 25);
  });
});

describe("GET /v1/aggregates", () => {
  const TOKEN = "s3cret-token-value";

  async function seeded() {
    const e = env({ AGGREGATES_TOKEN: TOKEN });
    await worker.fetch(pingRequest(VALID), e);
    await worker.fetch(pingRequest({ ...VALID, install_id: UUID2 }), e);
    await worker.scheduled({}, e);
    return e;
  }

  it("serves snapshots to a correct bearer token", async () => {
    const e = await seeded();
    const res = await worker.fetch(
      new Request("https://t.pixlstash.dev/v1/aggregates", {
        headers: { authorization: `Bearer ${TOKEN}` },
      }),
      e,
    );
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.snapshots.length, 1);
    assert.equal(body.snapshots[0].active_installs, 2);
  });

  it("refuses a missing token", async () => {
    const e = await seeded();
    const res = await worker.fetch(
      new Request("https://t.pixlstash.dev/v1/aggregates"),
      e,
    );
    assert.equal(res.status, 401);
  });

  it("refuses a wrong token", async () => {
    const e = await seeded();
    const res = await worker.fetch(
      new Request("https://t.pixlstash.dev/v1/aggregates", {
        headers: { authorization: "Bearer wrong" },
      }),
      e,
    );
    assert.equal(res.status, 401);
  });

  it("refuses when no token is configured, rather than serving openly", async () => {
    const e = env();
    await worker.scheduled({}, e);
    const res = await worker.fetch(
      new Request("https://t.pixlstash.dev/v1/aggregates", {
        headers: { authorization: "Bearer anything" },
      }),
      e,
    );
    assert.equal(res.status, 503);
  });
});

describe("scheduled", () => {
  it("prunes rows past the retention window", async () => {
    const e = env({ AGGREGATES_TOKEN: "t" });
    e.DB.installs.set("old", {
      install_id: "old",
      first_seen: "2024-01-01",
      last_seen: "2024-01-02",
      activity: 1,
      is_new_install: 1,
      install_type: "pip",
    });
    await worker.fetch(pingRequest(VALID), e);
    await worker.scheduled({}, e);
    await worker.scheduled({}, e);
    assert.equal(e.DB.installs.has("old"), false);
    assert.equal(e.DB.installs.has(UUID), true);
  });

  it("writes one snapshot per day and overwrites on a re-run", async () => {
    const e = env({ AGGREGATES_TOKEN: "t" });
    await worker.fetch(pingRequest(VALID), e);
    await worker.scheduled({}, e);
    await worker.scheduled({}, e);
    assert.equal(e.DB.snapshots.size, 1);
  });
});

describe("checkinsInActiveWindow", () => {
  const row = (lastSeen, bits) => ({
    last_seen: lastSeen,
    activity: encodeActivity(bits),
  });

  it("shifts the window by the gap between last_seen and today", () => {
    // Bits 0 and 1 are the day of last_seen and the day before it. last_seen is
    // itself ACTIVE_WINDOW_DAYS - 1 days before today, so those two days sit
    // ACTIVE_WINDOW_DAYS - 1 and ACTIVE_WINDOW_DAYS days back: both inside.
    const lastSeen = "2026-06-23"; // 27 days before 2026-07-20
    assert.equal(daysBetween(lastSeen, "2026-07-20"), ACTIVE_WINDOW_DAYS - 1);
    assert.equal(checkinsInActiveWindow(row(lastSeen, 0b11n), "2026-07-20"), 2);
  });

  it("drops the bits that fall out of the far edge of the window", () => {
    // last_seen sits exactly on the window edge, so only bit 0 is inside it.
    const lastSeen = "2026-06-22"; // ACTIVE_WINDOW_DAYS days before
    assert.equal(daysBetween(lastSeen, "2026-07-20"), ACTIVE_WINDOW_DAYS);
    assert.equal(checkinsInActiveWindow(row(lastSeen, 0b111n), "2026-07-20"), 1);
  });

  it("counts nothing once the install is past the window", () => {
    assert.equal(checkinsInActiveWindow(row("2026-06-21", 0b111n), "2026-07-20"), 0);
  });

  it("does not widen the window for a last_seen in the future", () => {
    // Clock skew. A sparse bitmap cannot tell the two apart -- clamped or not,
    // these three bits are inside either window -- so this is only a control
    // that skew does not lose ordinary counts.
    const skewed = { last_seen: "2026-07-22", activity: encodeActivity(0b111n) };
    assert.equal(daysBetween(skewed.last_seen, "2026-07-20"), -2);
    assert.equal(
      checkinsInActiveWindow(skewed, "2026-07-20"),
      3,
      "bits inside the window still count",
    );
    // This is the assertion that proves the clamp. A bitmap dense enough to
    // reach past the window edge counts ACTIVE_WINDOW_DAYS + 1 days clamped and
    // two more than that unclamped, because a negative gap would widen the
    // window backwards into days before it opened.
    const wide = {
      last_seen: "2026-07-22",
      activity: encodeActivity((1n << 40n) - 1n),
    };
    assert.equal(checkinsInActiveWindow(wide, "2026-07-20"), ACTIVE_WINDOW_DAYS + 1);
  });
});

describe("the aggregation checkpoint", () => {
  const row = {
    install_id: "checkpointed",
    first_seen: "2026-06-01",
    last_seen: "2026-07-20",
    activity: encodeActivity(0b1011n),
    has_resurrected: 0,
    is_new_install: 0,
    install_type: "pip",
  };

  it("carries counted check-ins across a slice boundary", () => {
    const state = createAccumulator();
    accumulateRow(state, row, "2026-07-20");
    assert.equal(state.checkinsByType.pip, 3, "guard: the row was counted");

    const resumed = deserializeAccumulator(serializeAccumulator(state));
    // The scan is split across five-minute slices, so anything not serialised
    // here is silently dropped from every day that needs more than one slice.
    assert.deepEqual(resumed.checkinsByType, state.checkinsByType);

    accumulateRow(resumed, { ...row, install_id: "second" }, "2026-07-20");
    assert.equal(resumed.checkinsByType.pip, 6);
  });

  it("still counts a pre-field checkpoint that had scanned nothing", () => {
    // runScheduledSlice persists an initial accumulator before it reads a row,
    // so an old Worker that crashed in that gap leaves a pre-field checkpoint
    // with an empty cursor. Nothing was lost, so the day stays countable.
    const pristine = JSON.stringify({
      active: 0,
      byType: { docker: 0, pip: 0, electron: 0, other: 0 },
      newLast7d: 0,
      resurrectionEligible: 0,
      resurrected: 0,
      cohorts: [],
    });
    const state = deserializeAccumulator(pristine, true);
    assert.deepEqual(state.checkinsByType, createAccumulator().checkinsByType);

    accumulateRow(state, row, "2026-07-20");
    assert.equal(
      finalizeAggregate(state, "2026-07-20").id_bearing_checkins_by_type.pip,
      3,
      "a complete total, not null",
    );
  });

  it("publishes null, not a partial count, when resuming a legacy checkpoint", () => {
    // Written by a Worker deployed before checkinsByType existed: byType carries
    // its prefix, and those rows' check-ins can never be recovered.
    const legacy = JSON.stringify({
      active: 4,
      byType: { docker: 0, pip: 4, electron: 0, other: 0, dev: 1 },
      newLast7d: 0,
      resurrectionEligible: 0,
      resurrected: 0,
      cohorts: [],
    });
    const state = deserializeAccumulator(legacy);
    assert.equal(state.active, 4, "the rest of the checkpoint still resumes");
    assert.equal(state.checkinsByType, null);

    // Still null after folding in the remaining rows, and still null through a
    // further checkpoint: a partial count must never look like a real one.
    accumulateRow(state, row, "2026-07-20");
    assert.equal(state.byType.pip, 5, "byType keeps accumulating");
    const resumed = deserializeAccumulator(serializeAccumulator(state));
    assert.equal(resumed.checkinsByType, null);
    assert.equal(
      finalizeAggregate(resumed, "2026-07-20").id_bearing_checkins_by_type,
      null,
    );
  });
});
