import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { afterEach, describe, it } from "node:test";
import { Miniflare } from "miniflare";

import {
  admitInstall,
  MAX_NEW_INSTALLS_PER_DAY,
  MAX_TOTAL_INSTALLS,
  runScheduledSlice,
} from "../src/index.js";

const DATE = "2026-08-03";
const instances = [];

async function database() {
  const mf = new Miniflare({
    modules: true,
    script: "export default { fetch() { return new Response('ok'); } }",
    compatibilityDate: DATE,
    d1Databases: { DB: crypto.randomUUID() },
  });
  instances.push(mf);
  const db = await mf.getD1Database("DB");
  // D1Database.exec() treats each newline as a separate statement, so it
  // cannot load this intentionally formatted, multi-line schema file. Wrangler
  // `d1 execute --file` has a real SQL file parser. This schema contains no
  // semicolons in literals, so stripping full-line comments and splitting on
  // statement terminators is equivalent here and keeps each CREATE intact.
  const schema = (
    await readFile(new URL("../schema.sql", import.meta.url), "utf8")
  ).replace(/^\s*--.*$/gm, "");
  const statements = schema
    .split(";")
    .map((sql) => sql.trim())
    .filter(Boolean);
  for (const statement of statements) {
    await db.prepare(statement).run();
  }
  return db;
}

function ping(install_id) {
  return { install_id, is_new_install: true, install_type: "pip" };
}

function installStatement(db, installId, firstSeen, lastSeen) {
  return db
    .prepare(
      `INSERT INTO install
        (install_id, first_seen, last_seen, activity, has_resurrected,
         is_new_install, install_type)
       VALUES (?, ?, ?, 'h0000000000000001', 0, 1, 'pip')`,
    )
    .bind(installId, firstSeen, lastSeen);
}

afterEach(async () => {
  await Promise.all(instances.splice(0).map((mf) => mf.dispose()));
});

describe("real D1 admission", () => {
  it("admits exactly one request for the final capacity slot", async () => {
    const db = await database();
    await db.batch([
      db
        .prepare("INSERT INTO counter (name, value) VALUES ('total_installs', ?)")
        .bind(MAX_TOTAL_INSTALLS - 1),
      db
        .prepare(
          "INSERT INTO counter (name, value, day) VALUES ('new_installs_today', ?, ?)",
        )
        .bind(MAX_NEW_INSTALLS_PER_DAY - 1, DATE),
    ]);

    const admitted = await Promise.all([
      admitInstall(db, ping("00000000-0000-4000-8000-000000000001"), DATE),
      admitInstall(db, ping("00000000-0000-4000-8000-000000000002"), DATE),
    ]);

    assert.equal(admitted.filter(Boolean).length, 1);
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM install").first()).n, 1);
    const counters = await db
      .prepare("SELECT name, value FROM counter ORDER BY name")
      .all();
    assert.deepEqual(
      Object.fromEntries(counters.results.map((row) => [row.name, row.value])),
      {
        new_installs_today: MAX_NEW_INSTALLS_PER_DAY,
        total_installs: MAX_TOTAL_INSTALLS,
      },
    );
  });

  it("charges a concurrent duplicate install exactly once", async () => {
    const db = await database();
    const duplicate = ping("00000000-0000-4000-8000-000000000003");
    const admitted = await Promise.all([
      admitInstall(db, duplicate, DATE),
      admitInstall(db, duplicate, DATE),
    ]);

    assert.equal(admitted.filter(Boolean).length, 1);
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM install").first()).n, 1);
    assert.equal(
      (await db.prepare("SELECT value FROM counter WHERE name='total_installs'").first())
        .value,
      1,
    );
    assert.equal(
      (
        await db
          .prepare("SELECT value FROM counter WHERE name='new_installs_today'")
          .first()
      ).value,
      1,
    );
  });
});

describe("real D1 scheduled checkpoints", () => {
  it("replays a failed scan checkpoint without losing or double-counting rows", async () => {
    const db = await database();
    await db.batch([
      installStatement(
        db,
        "00000000-0000-4000-8000-000000000010",
        "2026-08-01",
        "2026-08-03",
      ),
      installStatement(
        db,
        "00000000-0000-4000-8000-000000000011",
        "2026-08-01",
        "2026-08-03",
      ),
      installStatement(
        db,
        "00000000-0000-4000-8000-000000000012",
        "2026-08-01",
        "2026-08-03",
      ),
    ]);

    let failed = false;
    const failCheckpoint = {
      prepare(sql) {
        const statement = db.prepare(sql);
        if (!failed && /UPDATE aggregation_run SET cursor/.test(sql)) {
          return {
            bind() {
              return {
                async run() {
                  failed = true;
                  throw new Error("injected checkpoint failure");
                },
              };
            },
          };
        }
        return statement;
      },
      batch: db.batch.bind(db),
    };

    const testLimits = { scanPage: 2 };
    await assert.rejects(
      runScheduledSlice(failCheckpoint, DATE, testLimits),
      /injected/,
    );
    assert.equal(
      (await db.prepare("SELECT cursor FROM aggregation_run").first()).cursor,
      "",
    );
    assert.equal(
      (await db.prepare("SELECT COUNT(*) AS n FROM aggregate_snapshot").first()).n,
      0,
    );

    assert.equal((await runScheduledSlice(db, DATE, testLimits)).phase, "prune");
    const snapshot = JSON.parse(
      (await db.prepare("SELECT payload FROM aggregate_snapshot").first()).payload,
    );
    assert.equal(snapshot.active_installs, 3);
  });

  it("commits the snapshot before pruning and resumes after prune failure", async () => {
    const db = await database();
    await db.batch([
      installStatement(
        db,
        "00000000-0000-4000-8000-000000000004",
        "2024-01-01",
        "2024-01-02",
      ),
      db.prepare("INSERT INTO counter (name, value) VALUES ('total_installs', 1)"),
    ]);

    assert.equal((await runScheduledSlice(db, DATE)).phase, "prune");
    const failPrune = {
      prepare(sql) {
        if (/UPDATE counter SET value = MAX/.test(sql)) {
          return db.prepare("UPDATE injected_missing_table SET value = 0");
        }
        return db.prepare(sql);
      },
      batch: db.batch.bind(db),
    };
    await assert.rejects(runScheduledSlice(failPrune, DATE), /injected_missing_table/);

    assert.equal(
      (await db.prepare("SELECT COUNT(*) AS n FROM aggregate_snapshot").first()).n,
      1,
    );
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM install").first()).n, 1);
    assert.equal(
      (await db.prepare("SELECT value FROM counter WHERE name='total_installs'").first())
        .value,
      1,
    );

    assert.equal((await runScheduledSlice(db, DATE)).phase, "complete");
    assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM install").first()).n, 0);
    assert.equal(
      (await db.prepare("SELECT value FROM counter WHERE name='total_installs'").first())
        .value,
      0,
    );
  });
});
