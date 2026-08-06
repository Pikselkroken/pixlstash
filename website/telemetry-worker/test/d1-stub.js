/**
 * A minimal in-memory stand-in for the D1 statements this Worker actually runs.
 *
 * Deliberately not a SQL engine. It recognises the handful of statements in
 * src/index.js by shape and applies them to a Map, which keeps the tests
 * dependency-free and runnable under plain `node --test`. If a new statement is
 * added to the Worker and not taught here, the stub throws rather than
 * silently returning an empty result, so a test can never pass by accident
 * against a query the stub does not understand.
 */

export class D1Stub {
  constructor() {
    this.installs = new Map();
    this.snapshots = new Map();
    this.counters = new Map();
    this.runs = new Map();
    this.lastChanges = 0;
  }

  prepare(sql) {
    return new StubStatement(this, sql.replace(/\s+/g, " ").trim());
  }

  /** D1 runs a batch as a sequential transaction; sequential is enough here. */
  async batch(statements) {
    const results = [];
    for (const statement of statements) results.push(await statement.run());
    return results;
  }
}

class StubStatement {
  constructor(db, sql) {
    this.db = db;
    this.sql = sql;
    this.args = [];
  }

  bind(...args) {
    if (args.some((value) => typeof value === "bigint")) {
      throw new TypeError("D1Stub: BigInt bindings are not supported by D1");
    }
    this.args = args;
    return this;
  }

  async first() {
    if (this.sql.startsWith("SELECT COUNT(*) AS n FROM install")) {
      return { n: this.db.installs.size };
    }
    if (
      this.sql.startsWith(
        "SELECT first_seen, last_seen, activity, has_resurrected FROM install",
      )
    ) {
      return this.db.installs.get(this.args[0]) ?? null;
    }
    if (this.sql.startsWith("SELECT value, day FROM counter")) {
      return this.db.counters.get(this.args[0]) ?? null;
    }
    if (this.sql.startsWith("SELECT snapshot_date, phase, cutoff, cursor, accumulator")) {
      return [...this.db.runs.values()].sort((a, b) =>
        a.snapshot_date < b.snapshot_date ? -1 : 1,
      )[0] ?? null;
    }
    throw new Error(`D1Stub: unrecognised first() statement: ${this.sql}`);
  }

  async all() {
    if (this.sql.startsWith("SELECT install_id, first_seen")) {
      // Mirrors the keyset paging the Worker uses, so a paging bug shows up
      // here rather than only against real D1.
      const [cursor, limit] = this.args;
      const page = [...this.db.installs.values()]
        .sort((a, b) => (a.install_id < b.install_id ? -1 : 1))
        .filter((row) => row.install_id > (cursor ?? ""))
        .slice(0, limit ?? 10000);
      return { results: page };
    }
    if (this.sql.startsWith("SELECT snapshot_date, payload FROM aggregate_snapshot")) {
      const rows = [...this.db.snapshots.entries()]
        .sort((a, b) => (a[0] < b[0] ? 1 : -1))
        .slice(0, this.args[0] ?? 90)
        .map(([snapshot_date, payload]) => ({ snapshot_date, payload }));
      return { results: rows };
    }
    throw new Error(`D1Stub: unrecognised all() statement: ${this.sql}`);
  }

  async run() {
    if (this.sql.startsWith("INSERT INTO install")) {
      const [
        install_id,
        first_seen,
        last_seen,
        activity,
        has_resurrected,
        is_new_install,
        install_type,
      ] = this.args;
      const total = this.db.counters.get("total_installs")?.value ?? 0;
      const daily = this.db.counters.get("new_installs_today")?.value ?? 0;
      const totalCap = this.args[7] ?? Number.MAX_SAFE_INTEGER;
      const dailyCap = this.args[8] ?? Number.MAX_SAFE_INTEGER;
      const inserted =
        !this.db.installs.has(install_id) && total < totalCap && daily < dailyCap;
      if (inserted) {
        this.db.installs.set(install_id, {
          install_id,
          first_seen,
          last_seen,
          activity,
          has_resurrected,
          is_new_install,
          install_type,
        });
      }
      this.db.lastChanges = inserted ? 1 : 0;
      return {
        results: inserted ? [{ install_id }] : [],
        meta: { changes: this.db.lastChanges },
      };
    }
    if (this.sql.startsWith("UPDATE install")) {
      const [last_seen, activity, has_resurrected, install_type, install_id] =
        this.args;
      const row = this.db.installs.get(install_id);
      if (row) {
        Object.assign(row, {
          last_seen,
          activity,
          has_resurrected,
          install_type,
        });
      }
      this.db.lastChanges = row ? 1 : 0;
      return { meta: { changes: this.db.lastChanges } };
    }
    if (this.sql.startsWith("DELETE FROM install WHERE install_id IN")) {
      const [cutoff, limit] = this.args;
      let changes = 0;
      for (const [id, row] of [...this.db.installs.entries()]
        .sort((a, b) => (a[0] < b[0] ? -1 : 1))
        .filter(([, row]) => row.last_seen < cutoff)
        .slice(0, limit)) {
        this.db.installs.delete(id);
        changes += 1;
      }
      this.db.lastChanges = changes;
      return { meta: { changes } };
    }
    if (this.sql.startsWith("INSERT INTO aggregate_snapshot")) {
      const inserted = !this.db.snapshots.has(this.args[0]);
      if (inserted) this.db.snapshots.set(this.args[0], this.args[1]);
      this.db.lastChanges = inserted ? 1 : 0;
      return { meta: { changes: this.db.lastChanges } };
    }
    if (this.sql.startsWith("INSERT INTO counter") && this.sql.includes("excluded.value")) {
      this.db.counters.set("total_installs", {
        value: this.db.installs.size,
        day: null,
      });
      this.db.lastChanges = 1;
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("INSERT INTO counter")) {
      const isDaily = this.sql.includes("new_installs_today");
      const name = isDaily ? "new_installs_today" : "total_installs";
      const day = isDaily ? this.args[0] : null;
      const existing = this.db.counters.get(name);
      if (!existing) {
        this.db.counters.set(name, { value: 0, day });
      } else if (isDaily && existing.day !== day) {
        this.db.counters.set(name, { value: 0, day });
      }
      this.db.lastChanges = 1;
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE counter SET value = value + 1")) {
      const name = this.sql.includes("total_installs")
        ? "total_installs"
        : "new_installs_today";
      const changed = this.db.lastChanges === 1;
      if (changed) this.db.counters.get(name).value += 1;
      this.db.lastChanges = changed ? 1 : 0;
      return { meta: { changes: this.db.lastChanges } };
    }
    if (this.sql.startsWith("UPDATE counter SET value = MAX")) {
      const changed = this.db.lastChanges;
      const counter = this.db.counters.get("total_installs");
      if (counter) counter.value = Math.max(0, counter.value - changed);
      this.db.lastChanges = counter ? 1 : 0;
      return { meta: { changes: this.db.lastChanges } };
    }
    if (this.sql.startsWith("INSERT OR IGNORE INTO aggregation_run")) {
      const [snapshot_date, cutoff, accumulator] = this.args;
      const inserted =
        this.db.runs.size === 0 && !this.db.snapshots.has(snapshot_date);
      if (inserted) {
        this.db.runs.set(snapshot_date, {
          snapshot_date,
          phase: "scan",
          cutoff,
          cursor: "",
          accumulator,
        });
      }
      this.db.lastChanges = inserted ? 1 : 0;
      return { meta: { changes: this.db.lastChanges } };
    }
    if (this.sql.startsWith("UPDATE aggregation_run SET cursor")) {
      const [cursor, accumulator, snapshot_date, oldCursor] = this.args;
      const run = this.db.runs.get(snapshot_date);
      const changed = run?.phase === "scan" && run.cursor === oldCursor;
      if (changed) Object.assign(run, { cursor, accumulator });
      this.db.lastChanges = changed ? 1 : 0;
      return { meta: { changes: this.db.lastChanges } };
    }
    if (this.sql.startsWith("UPDATE aggregation_run SET phase")) {
      const [snapshot_date, cursor] = this.args;
      const run = this.db.runs.get(snapshot_date);
      const changed = run?.phase === "scan" && run.cursor === cursor;
      if (changed) run.phase = "prune";
      this.db.lastChanges = changed ? 1 : 0;
      return { meta: { changes: this.db.lastChanges } };
    }
    if (this.sql.startsWith("DELETE FROM aggregation_run")) {
      const changed = this.db.runs.delete(this.args[0]);
      this.db.lastChanges = changed ? 1 : 0;
      return { meta: { changes: this.db.lastChanges } };
    }
    throw new Error(`D1Stub: unrecognised run() statement: ${this.sql}`);
  }
}

/** A rate limiter that always allows, for tests that are not about limiting. */
export const allowAll = { limit: async () => ({ success: true }) };

/** A rate limiter that always refuses. */
export const denyAll = { limit: async () => ({ success: false }) };

/**
 * Build a ping Request.
 *
 * @param {object|string} body Object to serialise, or a raw string.
 * @param {object} [headers] Extra headers.
 */
export function pingRequest(body, headers = {}) {
  const raw = typeof body === "string" ? body : JSON.stringify(body);
  return new Request("https://t.pixlstash.dev/v1/ping", {
    method: "POST",
    body: raw,
    headers: { "content-type": "application/json", ...headers },
  });
}
