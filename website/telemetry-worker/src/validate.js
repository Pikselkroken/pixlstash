/**
 * Strict validation for the telemetry ping body.
 *
 * The rule is reject-by-default: an unrecognised key, an unexpected type, or an
 * out-of-set value fails the whole request. Nothing is coerced, nothing is
 * dropped-and-accepted, and nothing unrecognised is ever stored. This endpoint
 * takes unauthenticated writes from the public internet, so the parser is the
 * security boundary.
 */

/** Maximum accepted request body, in bytes. A valid ping is under 150. */
export const MAX_BODY_BYTES = 512;

/**
 * Install type for a machine that runs PixlStash to develop it, not to use it.
 *
 * Declared locally with `PIXLSTASH_INSTALL_TYPE=dev`. It is accepted and stored
 * like any other type so the signal stays visible in
 * `active_installs_by_type.dev`, and excluded from every published number:
 * active installs, new installs, resurrection and cohort retention. See
 * `accumulateRow` in aggregate.js.
 */
export const DEV_INSTALL_TYPE = "dev";

/**
 * The only install types we record. Four coarse real buckets plus `dev`, which
 * is a self-signal rather than a user. No free text in either case.
 */
export const INSTALL_TYPES = Object.freeze([
  "docker",
  "pip",
  "electron",
  "other",
  DEV_INSTALL_TYPE,
]);

/** The exact key set. Not a minimum: extra keys are a rejection, not noise. */
const REQUIRED_KEYS = Object.freeze(["install_id", "is_new_install", "install_type"]);

// Canonical UUIDv4 only: version nibble 4, variant nibble 8/9/a/b. Accepting any
// UUID-shaped string would let a poisoner mint sequential ids trivially, and the
// client only ever generates v4.
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/**
 * Validate a decoded ping body.
 *
 * @param {unknown} body Decoded JSON.
 * @returns {{ok: true, value: {install_id: string, is_new_install: boolean,
 *   install_type: string}} | {ok: false, reason: string}}
 */
export function validatePing(body) {
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    return { ok: false, reason: "body must be a JSON object" };
  }

  const keys = Object.keys(body);
  for (const key of keys) {
    if (!REQUIRED_KEYS.includes(key)) {
      return { ok: false, reason: "unrecognised key" };
    }
  }
  for (const key of REQUIRED_KEYS) {
    if (!Object.hasOwn(body, key)) {
      return { ok: false, reason: "missing key" };
    }
  }

  const { install_id: installId, is_new_install: isNew, install_type: type } = body;

  if (typeof installId !== "string" || !UUID_V4.test(installId)) {
    return { ok: false, reason: "install_id must be a canonical lowercase UUIDv4" };
  }
  if (typeof isNew !== "boolean") {
    return { ok: false, reason: "is_new_install must be a boolean" };
  }
  if (typeof type !== "string" || !INSTALL_TYPES.includes(type)) {
    return { ok: false, reason: "install_type is not a known bucket" };
  }

  return { ok: true, value: { install_id: installId, is_new_install: isNew, install_type: type } };
}
