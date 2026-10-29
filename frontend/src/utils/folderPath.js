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
 * Where a registered folder is, said from the drive it sits under: the second
 * line of a root row in the Folders tree. The home directory is `~`; a path on
 * any other mounted drive starts where the mount point ends, since the drive
 * row above already names it. Split like {@link splitFolderPath} so the last
 * segment, the part that tells two installs apart, survives truncation.
 *
 * @param {string} path - the folder's absolute path.
 * @param {string} [mountPoint] - its drive's mount point, when measured.
 * @param {boolean} [leaf=true] - false drops the last segment, for a row whose
 *   name already is that segment.
 * @returns {{head: string, tail: string}} the origin is `head + tail`.
 */
export function folderOrigin(path, mountPoint = "", leaf = true) {
  const { home, head, tail } = splitFolderPath(
    String(path || "").replace(/(.)[\\/]+$/, "$1"),
  );
  let text = leaf ? head + tail : head;
  const mount = String(mountPoint || "").replace(/[\\/]+$/, "");
  if (home) text = `~${text}`;
  else if (mount && text.startsWith(mount) && /^[\\/]./.test(text.slice(mount.length)))
    text = text.slice(mount.length + 1);
  // Only a folder at the very root of the filesystem has no parent left.
  text = text || "/";
  const cut = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
  return cut > 0
    ? { head: text.slice(0, cut), tail: text.slice(cut) }
    : { head: "", tail: text };
}
