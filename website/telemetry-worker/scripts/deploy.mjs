/**
 * Deploy the Worker without ever writing the database UUID into wrangler.jsonc.
 *
 * The UUID is deliberately absent from source control, and Wrangler's automatic
 * provisioning is disabled so a missing one fails closed rather than creating a
 * non-EU database. This looks the UUID up by name, refuses anything outside the
 * EU jurisdiction, and deploys from a temporary gitignored copy of the config
 * that is removed afterwards. The copy sits beside wrangler.jsonc because
 * Wrangler resolves `main` relative to the config file.
 *
 * Run with: npm run deploy
 */

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const DATABASE_NAME = "pixlstash-telemetry";
export const REQUIRED_JURISDICTION = "eu";
export const DEPLOY_FLAGS = ["--no-x-provision", "--no-x-auto-create"];

const ROOT = new URL("../", import.meta.url);
const CONFIG = fileURLToPath(new URL("wrangler.jsonc", ROOT));
const DEPLOY_CONFIG = fileURLToPath(new URL("wrangler.deploy.jsonc", ROOT));
// The pinned Wrangler from package.json, run through Node rather than resolved
// on PATH: an arbitrary wrangler there may not honour the provisioning flags,
// and without a shell Windows would not find wrangler.cmd.
const WRANGLER = fileURLToPath(new URL("node_modules/wrangler/bin/wrangler.js", ROOT));

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * Return the UUID of the production database, or throw.
 *
 * @param {object} info Parsed `wrangler d1 info --json` output.
 * @returns {string}
 */
export function productionDatabaseId(info) {
  if (info?.name !== DATABASE_NAME) {
    throw new Error(`expected database ${DATABASE_NAME}, got ${info?.name}`);
  }
  // The jurisdiction is fixed at creation. A database of the same name outside
  // the EU is not the production one, and deploying against it would start
  // writing install ids there.
  if (info.jurisdiction !== REQUIRED_JURISDICTION) {
    throw new Error(
      `${DATABASE_NAME} has jurisdiction ${info.jurisdiction ?? "none"}, ` +
        `expected ${REQUIRED_JURISDICTION}; refusing to deploy`,
    );
  }
  if (typeof info.uuid !== "string" || !UUID.test(info.uuid)) {
    throw new Error(`${DATABASE_NAME} returned no valid uuid`);
  }
  return info.uuid;
}

/**
 * Add the database UUID to the D1 binding in the config source.
 *
 * Text substitution rather than parse-and-serialise, so the comments survive
 * and the deployed config differs from the tracked one by this field only.
 *
 * @param {string} source wrangler.jsonc contents.
 * @param {string} uuid
 * @returns {string}
 */
export function withDatabaseId(source, uuid) {
  const binding = `"database_name": "${DATABASE_NAME}"`;
  const count = source.split(binding).length - 1;
  if (count !== 1) {
    throw new Error(`expected one ${binding} in wrangler.jsonc, found ${count}`);
  }
  if (source.includes('"database_id"')) {
    throw new Error("wrangler.jsonc already carries a database_id; remove it first");
  }
  return source.replace(binding, `${binding}, "database_id": "${uuid}"`);
}

function wrangler(args, options = {}) {
  const result = spawnSync(process.execPath, [WRANGLER, ...args], {
    cwd: ROOT,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = result.stderr ? `: ${result.stderr.toString().trim()}` : "";
    throw new Error(`wrangler ${args[0]} ${args[1] ?? ""} failed${detail}`);
  }
  return result;
}

function main() {
  const info = JSON.parse(
    wrangler(["d1", "info", DATABASE_NAME, "--json"], {
      stdio: ["ignore", "pipe", "pipe"],
    }).stdout.toString(),
  );
  const config = withDatabaseId(readFileSync(CONFIG, "utf8"), productionDatabaseId(info));

  // Ctrl-C or a closed terminal reaches Wrangler too, since it shares the
  // process group. Without a handler Node would exit before the finally block
  // removes the copy. SIGKILL cannot be caught; the next run overwrites it.
  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(signal, () => {});
  writeFileSync(DEPLOY_CONFIG, config);
  try {
    wrangler(["deploy", "--config", DEPLOY_CONFIG, ...DEPLOY_FLAGS], {
      stdio: "inherit",
    });
  } finally {
    rmSync(DEPLOY_CONFIG, { force: true });
  }
  if (existsSync(DEPLOY_CONFIG)) {
    throw new Error(`could not remove ${DEPLOY_CONFIG}; delete it by hand`);
  }
}

// Real paths on both sides: import.meta.url is already resolved, so through a
// symlinked checkout a plain comparison would skip main() and exit 0 having
// deployed nothing.
if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))) {
  try {
    main();
  } catch (error) {
    console.error(`deploy failed: ${error.message}`);
    process.exit(1);
  }
}
