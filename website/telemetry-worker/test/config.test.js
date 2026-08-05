import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { describe, it } from "node:test";

async function text(path) {
  return readFile(new URL(path, import.meta.url), "utf8");
}

function parseJsonc(source) {
  return JSON.parse(source.replace(/^\s*\/\/.*$/gm, ""));
}

describe("release configuration", () => {
  it("has no tracked placeholder resource id and enables bounded observability", async () => {
    const source = await text("../wrangler.jsonc");
    const config = parseJsonc(source);

    assert.equal(source.includes("REPLACE_WITH"), false);
    assert.equal("database_id" in config.d1_databases[0], false);
    assert.equal(config.limits.cpu_ms, 30_000);
    assert.deepEqual(config.triggers.crons, ["*/5 * * * *"]);
    assert.equal(config.observability.enabled, true);
    assert.equal(config.observability.logs.invocation_logs, true);
    assert.ok(config.observability.traces.head_sampling_rate > 0);
  });

  it("fails deployment closed and documents EU creation evidence", async () => {
    const pkg = JSON.parse(await text("../package.json"));
    const readme = await text("../README.md");

    assert.match(pkg.scripts.deploy, /--no-x-provision/);
    assert.match(pkg.scripts.deploy, /--no-x-auto-create/);
    assert.match(readme, /--jurisdiction=eu/);
    assert.match(readme, /--update-config/);
    assert.match(readme, /release ticket/);
    assert.match(readme, /Workers Paid/);
  });
});
