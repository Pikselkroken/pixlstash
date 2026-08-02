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
  }

  prepare(sql) {
    return new StubStatement(this, sql.replace(/\s+/g, " ").trim());
  }
}

class StubStatement {
  constructor(db, sql) {
    this.db = db;
    this.sql = sql;
    this.args = [];
  }

  bind(...args) {
    this.args = args;
    return this;
  }

  async first() {
    if (this.sql.startsWith("SELECT first_seen, last_seen, activity FROM install")) {
      return this.db.installs.get(this.args[0]) ?? null;
    }
    throw new Error(`D1Stub: unrecognised first() statement: ${this.sql}`);
  }

  async all() {
    if (this.sql.startsWith("SELECT install_id, first_seen")) {
      return { results: [...this.db.installs.values()] };
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
      const [install_id, first_seen, last_seen, is_new_install, install_type] = this.args;
      if (!this.db.installs.has(install_id)) {
        this.db.installs.set(install_id, {
          install_id,
          first_seen,
          last_seen,
          activity: 1,
          is_new_install,
          install_type,
        });
      }
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE install")) {
      const [last_seen, activity, install_type, install_id] = this.args;
      const row = this.db.installs.get(install_id);
      if (row) Object.assign(row, { last_seen, activity, install_type });
      return { meta: { changes: row ? 1 : 0 } };
    }
    if (this.sql.startsWith("DELETE FROM install WHERE last_seen <")) {
      const cutoff = this.args[0];
      let changes = 0;
      for (const [id, row] of this.db.installs) {
        if (row.last_seen < cutoff) {
          this.db.installs.delete(id);
          changes += 1;
        }
      }
      return { meta: { changes } };
    }
    if (this.sql.startsWith("INSERT INTO aggregate_snapshot")) {
      this.db.snapshots.set(this.args[0], this.args[1]);
      return { meta: { changes: 1 } };
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
