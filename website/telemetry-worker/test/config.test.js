import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { describe, it } from "node:test";

import {
  DEPLOY_FLAGS,
  productionDatabaseId,
  withDatabaseId,
} from "../scripts/deploy.mjs";

async function text(path) {
  return readFile(new URL(path, import.meta.url), "utf8");
}

function parseJsonc(source) {
  return JSON.parse(source.replace(/^\s*\/\/.*$/gm, ""));
}

describe("release configuration", () => {
  it("has no tracked placeholder resource id and logs nothing about the request", async () => {
    const source = await text("../wrangler.jsonc");
    const config = parseJsonc(source);

    assert.equal(source.includes("REPLACE_WITH"), false);
    assert.equal("database_id" in config.d1_databases[0], false);
    assert.equal(config.limits.cpu_ms, 30_000);
    assert.deepEqual(config.triggers.crons, ["*/5 * * * *"]);
    assert.equal(config.observability.enabled, true);
    // Invocation logs and traces store request metadata (headers, client geo,
    // timestamps), which the README and PRIVACY.md promise is never stored.
    assert.equal(config.observability.logs.invocation_logs, false);
    assert.equal(config.observability.traces.enabled, false);
  });

  it("fails deployment closed and documents EU creation evidence", async () => {
    const pkg = JSON.parse(await text("../package.json"));
    const readme = await text("../README.md");

    assert.equal(pkg.scripts.deploy, "node scripts/deploy.mjs");
    assert.deepEqual(DEPLOY_FLAGS, ["--no-x-provision", "--no-x-auto-create"]);
    assert.match(readme, /--jurisdiction=eu/);
    assert.match(readme, /--update-config/);
    assert.match(readme, /release ticket/);
    assert.match(readme, /Workers Paid/);
  });
});

describe("deploy script", () => {
  const uuid = "0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0";
  const info = { name: "pixlstash-telemetry", jurisdiction: "eu", uuid };

  it("takes the uuid of the EU production database", () => {
    assert.equal(productionDatabaseId(info), uuid);
  });

  it("refuses a database outside the EU jurisdiction", () => {
    assert.throws(() => productionDatabaseId({ ...info, jurisdiction: undefined }), /jurisdiction/);
    assert.throws(() => productionDatabaseId({ ...info, jurisdiction: "fedramp" }), /jurisdiction/);
  });

  it("refuses a missing or malformed uuid", () => {
    assert.throws(() => productionDatabaseId({ ...info, uuid: "" }), /uuid/);
    assert.throws(() => productionDatabaseId({ ...info, uuid: undefined }), /uuid/);
  });

  it("adds the uuid to the tracked config's binding and changes nothing else", async () => {
    const source = await text("../wrangler.jsonc");
    const deployed = withDatabaseId(source, uuid);
    assert.equal(parseJsonc(deployed).d1_databases[0].database_id, uuid);
    assert.equal(deployed.replace(`, "database_id": "${uuid}"`, ""), source);
  });

  it("refuses a config that already carries a database_id", async () => {
    const source = await text("../wrangler.jsonc");
    assert.throws(() => withDatabaseId(withDatabaseId(source, uuid), uuid), /already/);
  });
});
