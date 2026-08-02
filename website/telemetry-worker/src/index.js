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
 * activity bitmap, a new-install flag, one of four install-type buckets).
 * What is not stored: IP addresses, user agents, request timestamps, versions,
 * or anything else from the request.
 */

import {
  accumulateRow,
  createAccumulator,
  finalizeAggregate,
} from "./aggregate.js";
import { daysBetween, rollActivity } from "./activity.js";
import { MAX_BODY_BYTES, validatePing } from "./validate.js";

/** Rows whose last_seen is older than this are deleted by the daily cron. */
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

/** Rows read per query when the aggregation pass walks the table. */
const AGGREGATE_SCAN_PAGE = 10_000;

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
 * Read a counter row, treating a stale day as zero.
 *
 * @returns {Promise<{value: number, day: string|null}>}
 */
async function readCounter(db, name) {
  const row = await db
    .prepare("SELECT value, day FROM counter WHERE name = ?")
    .bind(name)
    .first();
  return { value: Number(row?.value ?? 0), day: row?.day ?? null };
}

/**
 * True if another install may be created right now.
 *
 * Fails CLOSED on a counter read error: refusing a ping loses one datapoint,
 * whereas assuming headroom is how the cap gets bypassed by making the counter
 * table unreadable.
 */
async function canCreateInstall(db, date) {
  try {
    const total = await readCounter(db, "total_installs");
    if (total.value >= MAX_TOTAL_INSTALLS) {
      console.error(
        `install ceiling reached (${total.value}); refusing new installs`,
      );
      return false;
    }
    const today = await readCounter(db, "new_installs_today");
    // A counter from an earlier day is stale, so today's count is zero.
    const createdToday = today.day === date ? today.value : 0;
    if (createdToday >= MAX_NEW_INSTALLS_PER_DAY) {
      console.error(
        `daily new-install cap reached (${createdToday}); refusing until tomorrow`,
      );
      return false;
    }
    return true;
  } catch (error) {
    console.error(`counter read failed (${error}); refusing new install`);
    return false;
  }
}

/**
 * Recompute `total_installs` from the actual row count.
 *
 * Without this the counter is a LIFETIME tally: the prune deletes rows and
 * never decrements it, so reaching the ceiling once refuses every genuine new
 * install for ever, with no way back and a 429 that is deliberately
 * indistinguishable from rate limiting. Reconciling daily, right after the
 * prune, makes the ceiling mean "rows currently stored" and makes pruning
 * restore headroom.
 */
async function reconcileTotalInstalls(db) {
  const row = await db.prepare("SELECT COUNT(*) AS n FROM install").first();
  const actual = Number(row?.n ?? 0);
  await db
    .prepare(
      `INSERT INTO counter (name, value, day) VALUES ('total_installs', ?, NULL)
       ON CONFLICT(name) DO UPDATE SET value = excluded.value`,
    )
    .bind(actual)
    .run();
  return actual;
}

/** Bump both growth counters after a successful insert. */
async function bumpInstallCounters(db, date) {
  await db.batch([
    db
      .prepare(
        `INSERT INTO counter (name, value, day) VALUES ('total_installs', 1, NULL)
         ON CONFLICT(name) DO UPDATE SET value = counter.value + 1`,
      ),
    db
      .prepare(
        // Resets to 1 when the stored day is not today, which is what makes the
        // daily cap a daily cap rather than a lifetime one.
        `INSERT INTO counter (name, value, day) VALUES ('new_installs_today', 1, ?1)
         ON CONFLICT(name) DO UPDATE SET
           value = CASE WHEN counter.day = ?1 THEN counter.value + 1 ELSE 1 END,
           day = ?1`,
      )
      .bind(date),
  ]);
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
  const existing = await db
    .prepare("SELECT first_seen, last_seen, activity FROM install WHERE install_id = ?")
    .bind(ping.install_id)
    .first();

  if (!existing) {
    // Growth caps apply to INSERT only, never to UPDATE. Capping updates would
    // hand an attacker a denial-of-service against real installs: flood until
    // the cap trips, and every genuine install stops being counted.
    const allowed = await canCreateInstall(db, date);
    if (!allowed) return "capped";

    await db
      .prepare(
        `INSERT INTO install
           (install_id, first_seen, last_seen, activity, is_new_install, install_type)
         VALUES (?, ?, ?, 1, ?, ?)
         ON CONFLICT(install_id) DO NOTHING`,
      )
      .bind(ping.install_id, date, date, ping.is_new_install ? 1 : 0, ping.install_type)
      .run();
    await bumpInstallCounters(db, date);
    return "created";
  }

  const elapsed = daysBetween(existing.last_seen, date);
  const activity = rollActivity(existing.activity, elapsed);
  // first_seen and is_new_install are write-once: a later ping must not be able
  // to rewrite an install's cohort or move it into the new-install population.
  await db
    .prepare(
      `UPDATE install
          SET last_seen = ?, activity = ?, install_type = ?
        WHERE install_id = ?`,
    )
    .bind(
      elapsed > 0 ? date : existing.last_seen,
      // Bound as a BigInt, NOT Number(). float64 has 53 bits of mantissa, so
      // narrowing here silently discarded the low bits once an install passed
      // ~53 days of history: the whole activity record collapsed, and a fully
      // active install exceeded INT64_MAX. Those are exactly the long-lived
      // installs the retention curve is about.
      activity,
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

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/v1/ping") return handlePing(request, env);
    if (pathname === "/v1/aggregates") return handleAggregates(request, env);
    return fail(404, "not found");
  },

  /**
   * Daily cron: prune aged-out rows, then snapshot the day's aggregate.
   *
   * Order matters. Pruning first means the snapshot reflects what is actually
   * retained, so a published number can always be re-derived from the store it
   * was taken from.
   */
  async scheduled(event, env) {
    const date = today();
    // The prune is destructive and the snapshot is not recomputable ("compute
    // daily, never backfill"), so a failure after the delete would lose the day
    // permanently. Wrapping keeps the failure visible without leaving the
    // Worker in a half-run state that looks like success.
    try {

    const cutoff = new Date(Date.parse(`${date}T00:00:00Z`) - RETENTION_DAYS * 86400000)
      .toISOString()
      .slice(0, 10);
    const pruned = await env.DB.prepare("DELETE FROM install WHERE last_seen < ?")
      .bind(cutoff)
      .run();
    console.log(`pruned ${pruned.meta?.changes ?? 0} rows older than ${cutoff}`);

    // Reconcile BEFORE aggregating, so the ceiling reflects what the prune just
    // left behind rather than everything ever created.
    const stored = await reconcileTotalInstalls(env.DB);
    console.log(`reconciled total_installs to ${stored}`);

    // Keyset-paged and folded incrementally: no page is retained after it has
    // been accumulated, so the daily job's memory is independent of table size.
    const state = createAccumulator();
    let cursor = "";
    for (;;) {
      const { results } = await env.DB.prepare(
        `SELECT install_id, first_seen, last_seen, activity, is_new_install, install_type
           FROM install WHERE install_id > ? ORDER BY install_id LIMIT ?`,
      )
        .bind(cursor, AGGREGATE_SCAN_PAGE)
        .all();
      const page = results ?? [];
      for (const row of page) accumulateRow(state, row, date);
      if (page.length < AGGREGATE_SCAN_PAGE) break;
      cursor = page[page.length - 1].install_id;
    }

    const aggregate = finalizeAggregate(state, date);
    await env.DB.prepare(
      `INSERT INTO aggregate_snapshot (snapshot_date, payload) VALUES (?, ?)
       ON CONFLICT(snapshot_date) DO UPDATE SET payload = excluded.payload`,
    )
      .bind(date, JSON.stringify(aggregate))
      .run();
      console.log(
        `snapshot ${date}: ${aggregate.active_installs} active, ` +
          `${Object.keys(aggregate.cohort_retention).length} published cohorts, ` +
          `${aggregate.suppressed_cohorts} suppressed`,
      );
    } catch (error) {
      console.error(`scheduled run failed after pruning (${error})`);
      throw error;
    }
  },
};
