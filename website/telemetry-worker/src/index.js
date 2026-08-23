/**
 * PixlStash telemetry ingestion Worker.
 *
 * This is the first endpoint in the product's history that accepts
 * unauthenticated writes from the public internet, so it is written as an
 * attack surface rather than as plumbing: reject-by-default parsing, a hard
 * body cap, per-IP rate limiting, and a response that never reflects input.
 *
 * It attaches to the Cloudflare zone in front of pixlstash.dev. The website
 * itself stays a static GitHub Pages origin and is never involved: this route
 * is answered at the edge and the request never reaches Pages.
 *
 * What is stored: one row per install (id, first/last seen date, a 63-bit
 * activity bitmap, a sticky resurrection flag, a new-install flag, one of four
 * install-type buckets).
 * What is not stored: IP addresses, user agents, request timestamps, versions,
 * or anything else from the request.
 */

import {
  accumulateRow,
  createAccumulator,
  deserializeAccumulator,
  finalizeAggregate,
  hasResurrected,
  RESURRECTION_GAP_DAYS,
  serializeAccumulator,
} from "./aggregate.js";
import { daysBetween, encodeActivity, rollActivity } from "./activity.js";
import { MAX_BODY_BYTES, validatePing } from "./validate.js";

/** Rows whose last_seen is older than this are deleted by scheduled slices. */
const RETENTION_DAYS = 400;

/** Aggregate snapshots served by one GET, newest first. */
const AGGREGATE_PAGE = 90;

/**
 * Ceilings on stored installs and on new installs per UTC day.
 *
 * These are NOT a precision limit. The counter column is SQLite INTEGER, a
 * signed 64-bit value, so it could hold 9.2e18. They are a deliberate abuse
 * bound.
 *
 * The earlier value of 250,000 was justified as "what bounds the aggregation
 * pass's memory", which was both wrong and self-defeating: the daily job now
 * folds rows into fixed-size counters (see aggregate.js) so its memory scales
 * with the number of cohorts, not the number of rows. With that gone, the real
 * constraint is D1 storage, and the ceiling can be an order of magnitude
 * higher while still refusing an obvious flood.
 *
 * The daily cap is the control that actually matters: it bounds how fast an
 * attacker can consume the ceiling, and unlike the ceiling it resets.
 */
export const MAX_TOTAL_INSTALLS = 5_000_000;
export const MAX_NEW_INSTALLS_PER_DAY = 5_000;

/** Bounded work per five-minute scheduled invocation. */
export const AGGREGATE_SCAN_PAGE = 2_000;
export const AGGREGATE_PAGES_PER_SLICE = 20;
export const PRUNE_PAGE = 10_000;
export const PRUNE_PAGES_PER_SLICE = 5;

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  // Nothing here is cacheable or embeddable, and none of it should be sniffed.
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

/** Fixed-shape error. The detail strings are our own constants, never input. */
function fail(status, detail) {
  return new Response(JSON.stringify({ error: detail }), {
    status,
    headers: JSON_HEADERS,
  });
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Constant-time string comparison, so a token cannot be recovered by timing.
 *
 * @param {string} a
 * @param {string} b
 * @returns {boolean}
 */
function timingSafeEqual(a, b) {
  const left = new TextEncoder().encode(a);
  const right = new TextEncoder().encode(b);
  // Compare lengths without an early return, then every byte regardless.
  let diff = left.length ^ right.length;
  const max = Math.max(left.length, right.length);
  for (let i = 0; i < max; i++) {
    diff |= (left[i] ?? 0) ^ (right[i] ?? 0);
  }
  return diff === 0;
}

/**
 * Read at most MAX_BODY_BYTES from the request.
 *
 * Content-Length is checked first as a cheap rejection, but it is
 * attacker-controlled, so the actual read is capped independently rather than
 * trusted.
 *
 * @param {Request} request
 * @returns {Promise<string|null>} The body, or null if it exceeded the cap.
 */
async function readCappedBody(request) {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return null;

  if (!request.body) return "";

  // Read incrementally and abort the moment the cap is passed, rather than
  // materialising the whole body first. Content-Length is attacker-controlled
  // and absent entirely on a chunked request, so buffering first would let one
  // crafted request allocate up to Cloudflare's 100 MB body limit inside a
  // 128 MB isolate.
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_BODY_BYTES) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } catch {
    return null;
  }

  const joined = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(joined);
}

/**
 * Atomically admit one unique install and update both counters.
 *
 * D1 documents batch() as a sequential SQL transaction that rolls the whole
 * sequence back on failure. `changes()` carries the conditional INSERT's row
 * count through the two counter updates. Concurrent requests therefore cannot
 * both consume the last slot, and a duplicate install consumes no slot.
 */
export async function admitInstall(db, ping, date) {
  const statements = [
    db
      .prepare(
        `INSERT INTO counter (name, value, day)
         VALUES ('total_installs', 0, NULL) ON CONFLICT(name) DO NOTHING`,
      ),
    db
      .prepare(
        `INSERT INTO counter (name, value, day)
         VALUES ('new_installs_today', 0, ?1)
         ON CONFLICT(name) DO UPDATE SET
           value = CASE WHEN counter.day = ?1 THEN counter.value ELSE 0 END,
           day = ?1`,
      )
      .bind(date),
    db
      .prepare(
        `INSERT INTO install
           (install_id, first_seen, last_seen, activity, has_resurrected,
            is_new_install, install_type)
         SELECT ?, ?, ?, ?, ?, ?, ?
          WHERE (SELECT value FROM counter WHERE name = 'total_installs') < ?
            AND (SELECT value FROM counter WHERE name = 'new_installs_today') < ?
         ON CONFLICT(install_id) DO NOTHING
         RETURNING install_id`,
      )
      .bind(
        ping.install_id,
        date,
        date,
        encodeActivity(1n),
        0,
        ping.is_new_install ? 1 : 0,
        ping.install_type,
        MAX_TOTAL_INSTALLS,
        MAX_NEW_INSTALLS_PER_DAY,
      ),
    db.prepare(
      `UPDATE counter SET value = value + 1
        WHERE name = 'total_installs' AND changes() = 1`,
    ),
    db.prepare(
      `UPDATE counter SET value = value + 1
        WHERE name = 'new_installs_today' AND changes() = 1`,
    ),
  ];
  const results = await db.batch(statements);
  return Number(results[2]?.meta?.changes ?? results[2]?.results?.length ?? 0) === 1;
}

/**
 * Record one ping.
 *
 * Read-modify-write rather than a SQL upsert with inline bit arithmetic: it
 * reuses the unit-tested rollActivity helper and stays readable. Two concurrent
 * pings for the same id could interleave and lose one bit; at this volume that
 * is not worth a transaction, and the cost of the race is one missing day on
 * one install.
 *
 * @param {D1Database} db
 * @param {{install_id: string, is_new_install: boolean, install_type: string}} ping
 * @param {string} date
 * @returns {Promise<"created"|"updated"|"capped">}
 */
async function recordPing(db, ping, date) {
  let existing = await db
    .prepare(
      `SELECT first_seen, last_seen, activity, has_resurrected
         FROM install WHERE install_id = ?`,
    )
    .bind(ping.install_id)
    .first();

  if (!existing) {
    // Growth caps apply to INSERT only, never to UPDATE. Capping updates would
    // hand an attacker a denial-of-service against real installs: flood until
    // the cap trips, and every genuine install stops being counted.
    if (await admitInstall(db, ping, date)) return "created";
    // A concurrent request for the same id may have won the unique insert.
    // Treat that as an update; only absence here means capacity refused us.
    existing = await db
      .prepare(
        `SELECT first_seen, last_seen, activity, has_resurrected
           FROM install WHERE install_id = ?`,
      )
      .bind(ping.install_id)
      .first();
    if (!existing) return "capped";
  }

  const elapsed = daysBetween(existing.last_seen, date);
  const activity = rollActivity(existing.activity, elapsed);
  // Once observed, resurrection is a durable fact about this installation.
  // Recomputing it solely from the 63-day bitmap makes a genuine return turn
  // back into "never returned" when the historical gap ages out.
  const resurrected =
    Boolean(existing.has_resurrected) ||
    // `elapsed - 1` is the number of silent days between the two pings. Check
    // it directly because rollActivity deliberately discards the old bit when
    // the return falls beyond the 63-day bitmap window.
    elapsed - 1 >= RESURRECTION_GAP_DAYS ||
    // Backfill the sticky field from any older gap still visible in rows that
    // predate this column.
    hasResurrected(activity);
  // first_seen and is_new_install are write-once: a later ping must not be able
  // to rewrite an install's cohort or move it into the new-install population.
  await db
    .prepare(
      `UPDATE install
          SET last_seen = ?, activity = ?, has_resurrected = ?, install_type = ?
        WHERE install_id = ?`,
    )
    .bind(
      elapsed > 0 ? date : existing.last_seen,
      // D1 cannot bind BigInt and exposes INTEGER values as Number, whose
      // 53-bit mantissa cannot carry this 63-bit bitmap. The fixed-width text
      // encoding preserves every bit through the Workers API.
      encodeActivity(activity),
      resurrected ? 1 : 0,
      ping.install_type,
      ping.install_id,
    )
    .run();
  return "updated";
}

async function handlePing(request, env) {
  if (request.method !== "POST") return fail(405, "method not allowed");

  // Require application/json, which is NOT a CORS-safelisted content type.
  //
  // Without this, a POST carrying text/plain is a "simple request": no
  // preflight, so any website could make every one of its visitors silently
  // POST a fabricated ping from their own IP. Per-IP rate limiting is no
  // defence against that, because each visitor is a different IP.
  //
  // Requiring application/json forces a preflight for any cross-origin caller.
  // This Worker returns no Access-Control-Allow-Origin, so the preflight fails
  // and the browser never sends the request. Our own sender is server-side and
  // sets this header already.
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    return fail(415, "content-type must be application/json");
  }

  // Fails CLOSED when the binding is missing. A misconfigured deploy that
  // silently dropped rate limiting would leave the one control that bounds
  // write volume switched off, on the only unauthenticated write endpoint we
  // operate, with nothing in the response to reveal it.
  if (!env.RATE_LIMITER) {
    console.error("RATE_LIMITER binding missing; refusing to accept pings.");
    return fail(503, "temporarily unavailable");
  }
  // The IP is used here and discarded. It is never written to D1, never
  // logged, and never leaves this function.
  const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
  const { success } = await env.RATE_LIMITER.limit({ key: ip });
  if (!success) return fail(429, "rate limited");

  const raw = await readCappedBody(request);
  if (raw === null) return fail(413, "payload too large");

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return fail(400, "body is not valid JSON");
  }

  const result = validatePing(parsed);
  if (!result.ok) return fail(400, result.reason);

  let outcome;
  try {
    outcome = await recordPing(env.DB, result.value, today());
  } catch (error) {
    // A storage failure must produce a clean refusal, not an unhandled throw
    // that Cloudflare renders as a default 500 page. The client never retries,
    // so the cost is one lost datapoint.
    console.error(`recordPing failed (${error})`);
    return fail(503, "temporarily unavailable");
  }
  if (outcome === "capped") {
    // Deliberately indistinguishable from ordinary rate limiting: telling a
    // prober which cap they hit tells them how close they are to exhausting it.
    return fail(429, "rate limited");
  }

  // 204 with no body: nothing is echoed, and there is nothing for a prober to
  // read back out.
  return new Response(null, { status: 204, headers: JSON_HEADERS });
}

async function handleAggregates(request, env) {
  if (request.method !== "GET") return fail(405, "method not allowed");

  const expected = env.AGGREGATES_TOKEN;
  if (!expected) return fail(503, "aggregates are not configured");

  const header = request.headers.get("authorization") ?? "";
  const presented = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!timingSafeEqual(presented, expected)) return fail(401, "unauthorized");

  const { results } = await env.DB.prepare(
    "SELECT snapshot_date, payload FROM aggregate_snapshot ORDER BY snapshot_date DESC LIMIT ?",
  )
    .bind(AGGREGATE_PAGE)
    .all();

  const snapshots = (results ?? []).map((row) => JSON.parse(row.payload));
  return new Response(JSON.stringify({ snapshots }), {
    status: 200,
    headers: JSON_HEADERS,
  });
}

function cutoffFor(date) {
  return new Date(Date.parse(`${date}T00:00:00Z`) - RETENTION_DAYS * 86400000)
    .toISOString()
    .slice(0, 10);
}

/** Run one bounded, restartable aggregation or prune slice. */
export async function runScheduledSlice(db, date = today(), limits = {}) {
  const scanPage = limits.scanPage ?? AGGREGATE_SCAN_PAGE;
  const scanPagesPerSlice = limits.scanPagesPerSlice ?? AGGREGATE_PAGES_PER_SLICE;
  const prunePage = limits.prunePage ?? PRUNE_PAGE;
  const prunePagesPerSlice = limits.prunePagesPerSlice ?? PRUNE_PAGES_PER_SLICE;
  const initial = serializeAccumulator(createAccumulator());
  await db
    .prepare(
      `INSERT OR IGNORE INTO aggregation_run
         (snapshot_date, phase, cutoff, cursor, accumulator)
       SELECT ?, 'scan', ?, '', ?
        WHERE NOT EXISTS (SELECT 1 FROM aggregation_run)
          AND NOT EXISTS (
            SELECT 1 FROM aggregate_snapshot WHERE snapshot_date = ?
          )`,
    )
    .bind(date, cutoffFor(date), initial, date)
    .run();

  const run = await db
    .prepare(
      `SELECT snapshot_date, phase, cutoff, cursor, accumulator
         FROM aggregation_run ORDER BY snapshot_date LIMIT 1`,
    )
    .first();
  if (!run) return { phase: "idle", date };

  if (run.phase === "scan") {
    // An empty cursor means no row has been folded in yet: the accumulator is
    // still the one persisted before the scan began, in the same transaction.
    const state = deserializeAccumulator(run.accumulator, run.cursor === "");
    let cursor = run.cursor;
    let scanned = 0;
    for (let pageNumber = 0; pageNumber < scanPagesPerSlice; pageNumber++) {
      const { results } = await db
        .prepare(
          `SELECT install_id, first_seen, last_seen, activity, has_resurrected,
                  is_new_install, install_type
             FROM install WHERE install_id > ? ORDER BY install_id LIMIT ?`,
        )
        .bind(cursor, scanPage)
        .all();
      const page = results ?? [];
      scanned += page.length;
      for (const row of page) accumulateRow(state, row, run.snapshot_date);

      if (page.length < scanPage) {
        const aggregate = finalizeAggregate(state, run.snapshot_date);
        const results = await db.batch([
          db
            .prepare(
              `INSERT INTO aggregate_snapshot (snapshot_date, payload)
               VALUES (?, ?) ON CONFLICT(snapshot_date) DO NOTHING`,
            )
            .bind(run.snapshot_date, JSON.stringify(aggregate)),
          db
            .prepare(
              `UPDATE aggregation_run SET phase = 'prune'
                WHERE snapshot_date = ? AND phase = 'scan' AND cursor = ?`,
            )
            .bind(run.snapshot_date, run.cursor),
        ]);
        if (Number(results[1]?.meta?.changes ?? 0) !== 1) {
          throw new Error("aggregation checkpoint was advanced concurrently");
        }
        console.log(
          JSON.stringify({
            message: "telemetry snapshot committed",
            snapshot_date: run.snapshot_date,
            active_installs: aggregate.active_installs,
            published_cohorts: Object.keys(aggregate.cohort_retention).length,
            suppressed_cohorts: aggregate.suppressed_cohorts,
          }),
        );
        return { phase: "prune", date: run.snapshot_date, complete: true };
      }

      cursor = page[page.length - 1].install_id;
      // If this write fails, the transaction has not advanced the cursor. The
      // next trigger re-reads this page into the prior accumulator, so no row is
      // skipped or counted twice in durable state.
      const checkpoint = await db
        .prepare(
          `UPDATE aggregation_run SET cursor = ?, accumulator = ?
            WHERE snapshot_date = ? AND phase = 'scan' AND cursor = ?`,
        )
        .bind(cursor, serializeAccumulator(state), run.snapshot_date, run.cursor)
        .run();
      if (Number(checkpoint.meta?.changes ?? 0) !== 1) {
        throw new Error("aggregation checkpoint was advanced concurrently");
      }
      run.cursor = cursor;
    }
    console.log(
      JSON.stringify({
        message: "telemetry scan checkpointed",
        snapshot_date: run.snapshot_date,
        rows_scanned_this_slice: scanned,
      }),
    );
    return { phase: "scan", date: run.snapshot_date, cursor };
  }

  let pruned = 0;
  for (let pageNumber = 0; pageNumber < prunePagesPerSlice; pageNumber++) {
    const results = await db.batch([
      db
        .prepare(
          `DELETE FROM install WHERE install_id IN (
             SELECT install_id FROM install WHERE last_seen < ?
              ORDER BY install_id LIMIT ?
           )`,
        )
        .bind(run.cutoff, prunePage),
      db.prepare(
        `UPDATE counter SET value = MAX(0, value - changes())
          WHERE name = 'total_installs'`,
      ),
    ]);
    const removed = Number(results[0]?.meta?.changes ?? 0);
    pruned += removed;
    if (removed < prunePage) {
      await db.batch([
        db.prepare(
          `INSERT INTO counter (name, value, day)
           SELECT 'total_installs', COUNT(*), NULL FROM install WHERE true
           ON CONFLICT(name) DO UPDATE SET value = excluded.value`,
        ),
        db
          .prepare("DELETE FROM aggregation_run WHERE snapshot_date = ?")
          .bind(run.snapshot_date),
      ]);
      console.log(
        JSON.stringify({
          message: "telemetry prune complete",
          snapshot_date: run.snapshot_date,
          cutoff: run.cutoff,
        }),
      );
      return { phase: "complete", date: run.snapshot_date };
    }
  }
  console.log(
    JSON.stringify({
      message: "telemetry prune checkpointed",
      snapshot_date: run.snapshot_date,
      rows_pruned_this_slice: pruned,
    }),
  );
  return { phase: "prune", date: run.snapshot_date };
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/v1/ping") return handlePing(request, env);
    if (pathname === "/v1/aggregates") return handleAggregates(request, env);
    return fail(404, "not found");
  },

  async scheduled(_event, env) {
    try {
      await runScheduledSlice(env.DB);
    } catch (error) {
      console.error(
        JSON.stringify({
          message: "telemetry scheduled slice failed",
          error: error instanceof Error ? error.message : String(error),
        }),
      );
      throw error;
    }
  },
};
