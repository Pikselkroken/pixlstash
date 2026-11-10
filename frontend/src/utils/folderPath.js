// How a model folder's path is drawn: home as one word, the rest split so the
// last segment survives truncation.

// ponytail: home is recognised by its conventional shape, not asked of the
// server, so a home directory anywhere else is shown as a plain path. Send the
// server's own `~` in `GET /model-folders` if that ever matters.
// `Shared`, `Public` and `Default` sit under a Users folder without being anybody's.
const HOME_RE =
  /^(\/home\/[^/]+|\/Users\/(?!(?:Shared|Public|Default)\/)[^/]+|\/root|[A-Za-z]:[\\/]Users[\\/](?!(?:Shared|Public|Default)[\\/])[^\\/]+)(?=[\\/])/;

/**
 * Split a path for display.
 *
 * @param {string} path - an absolute host path.
 * @returns {{home: boolean, head: string, tail: string}} `home` when the path
 *   starts in a home directory (which is then dropped from `head`), `tail` the
 *   last segment with its leading separator, `head` everything between.
 */
export function splitFolderPath(path) {
  const full = String(path || "");
  const match = full.match(HOME_RE);
  const rest = match ? full.slice(match[0].length) : full;
  const cut = Math.max(rest.lastIndexOf("/"), rest.lastIndexOf("\\"));
  return {
    home: Boolean(match),
    head: cut > 0 ? rest.slice(0, cut) : "",
    tail: cut >= 0 ? rest.slice(cut) : rest,
  };
}

/**
 * Where a registered folder is, said from the drive row above it: `~` for
 * home, the drive's mount point dropped, split like {@link splitFolderPath}
 * so the last segment survives truncation.
 *
 * @param {string} path - the folder's absolute path.
 * @param {string} [mountPoint] - its drive's mount point, when known.
 * @param {{parent?: boolean}} [options] - `parent` to say where the folder's
 *   parent is, for a row whose label is already the folder's own name.
 * @returns {{head: string, tail: string}} `tail` the last segment, `head`
 *   everything before it, its trailing separator included.
 */
export function folderOrigin(path, mountPoint = "", { parent = false } = {}) {
  let full = String(path || "").replace(/(.)[\\/]+$/, "$1");
  if (parent) {
    const cut = Math.max(full.lastIndexOf("/"), full.lastIndexOf("\\"));
    // The parent of `/models` is the root itself, not nothing.
    if (cut >= 0) full = full.slice(0, cut) || full.slice(0, 1);
  }
  const home = `${full}/`.match(HOME_RE);
  const mount = String(mountPoint || "").replace(/[\\/]+$/, "");
  let shown = full;
  if (home) {
    shown = `~${full.slice(home[0].length)}`;
  } else if (
    mount &&
    /^[\\/]/.test(full.slice(mount.length)) &&
    full.startsWith(mount)
  ) {
    // The drive row already names the drive; a folder AT its mount point keeps
    // the whole path rather than saying nothing.
    shown = full.slice(mount.length + 1);
  }
  // The separator ends the head, so a one-segment head like `~` still has a
  // part to give way before the last segment does.
  const cut = Math.max(shown.lastIndexOf("/"), shown.lastIndexOf("\\")) + 1;
  if (cut === shown.length) return { head: "", tail: shown };
  return { head: shown.slice(0, cut), tail: shown.slice(cut) };
}
