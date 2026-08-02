// The single source of what telemetry sends.
//
// The consent dialog's preview and the eventual sender both read from here, so
// the preview is a regression test rather than documentation: change the
// payload without changing this and the preview changes with it. A hand-written
// sample drifts from reality and eventually somebody diffs them, which on a
// self-hosted audience is a trust incident rather than a bug report.
//
// Nothing in this module performs a request.

/** Host that answers the version check. Static, CDN-cached, no compute. */
export const VERSION_CHECK_HOST = "https://pixlstash.dev";

/** Host that answers the install-ID ping. Separate on purpose: see the Worker README. */
export const TELEMETRY_HOST = "https://t.pixlstash.dev";

/**
 * The version check, as a URL.
 *
 * The two path segments are the only varying parts, which is why the dialog
 * names them: a user shown a URL they cannot decode has been shown a string,
 * not told the truth.
 *
 * @param {string} version Running app version, e.g. "1.9.0".
 * @param {string} installType One of docker, pip, electron, other.
 * @returns {{method: string, url: string}}
 */
export function buildVersionCheckRequest(version, installType) {
  return {
    method: "GET",
    url: `${VERSION_CHECK_HOST}/latest-version/${version}/${installType}.json`,
  };
}

/**
 * The install-ID ping body.
 *
 * Exactly the three keys the ingestion Worker accepts; it rejects anything
 * else outright rather than storing it. Keep this in step with
 * ``website/telemetry-worker/src/validate.js``.
 *
 * @param {{installId: string, isNewInstall: boolean, installType: string}} args
 * @returns {{method: string, url: string, body: Object}}
 */
export function buildInstallPingRequest({
  installId,
  isNewInstall,
  installType,
}) {
  return {
    method: "POST",
    url: `${TELEMETRY_HOST}/v1/ping`,
    body: {
      install_id: installId,
      is_new_install: isNewInstall,
      install_type: installType,
    },
  };
}

/**
 * Everything a given consent choice sends, in order.
 *
 * @param {string} variant "none", "check" or "checkid".
 * @param {{version: string, installType: string, installId: string,
 *   isNewInstall: boolean}} context
 * @returns {Array<{method: string, url: string, body?: Object}>} Empty for "none".
 */
export function buildPayloadForChoice(variant, context) {
  if (variant === "none") return [];
  const requests = [
    buildVersionCheckRequest(context.version, context.installType),
  ];
  if (variant === "checkid") {
    requests.push(
      buildInstallPingRequest({
        installId: context.installId,
        isNewInstall: context.isNewInstall,
        installType: context.installType,
      }),
    );
  }
  return requests;
}

/**
 * Plain-language meaning of every varying value in a payload.
 *
 * Rendered under the preview. `pip` covers the Windows server installer too,
 * because only the Electron shell sets PIXLSTASH_INSTALL_TYPE and everything
 * else falls through to the pip default.
 *
 * @param {string} variant
 * @param {{version: string, installType: string}} context
 * @returns {Array<{term: string, meaning: string}>}
 */
export function buildPayloadLegend(variant, context) {
  if (variant === "none") return [];
  const legend = [
    {
      term: context.version,
      meaning: "the PixlStash version you are running right now",
    },
    {
      term: context.installType,
      meaning:
        'how you installed it. "pip" covers both a pip install and the Windows ' +
        'server installer. The other two are "docker" and "electron", the desktop app',
    },
  ];
  if (variant === "checkid") {
    legend.push(
      {
        term: "install_id",
        meaning:
          "the random number. Not derived from anything about you or your computer, " +
          "and you can replace it whenever you like",
      },
      {
        term: "is_new_install",
        meaning:
          "whether this was a fresh install or an upgrade, so people who have used " +
          "PixlStash for months are not counted as brand new",
      },
      { term: "install_type", meaning: "the same value as above" },
    );
  }
  return legend;
}
