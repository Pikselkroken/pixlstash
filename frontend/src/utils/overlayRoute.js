// Where closing the lightbox goes.
//
// The lightbox is mounted inside `ImageGrid`, so a destination that replaces the
// grid - the workflow library and its "Made with it" tiles - can only open a
// picture by navigating to the library route first. Closing it there left the
// reader on All Pictures, a view they never asked for, with the shelf they came
// from two clicks away.
//
// Those links carry `?from=<path>` alongside `?overlay=<id>` and this hands it
// back, so the close returns where the picture was opened from. Everywhere else
// the query keeps whatever else it carried and only `?overlay=` is dropped,
// which is what the lightbox has always done.
//
// `from` comes out of the URL bar, so only a same-document absolute path is
// honoured; anything else (a protocol, a protocol-relative host, a repeated
// parameter arriving as an array) falls back to the plain close.
//
// `returnToOrigin` is false for the closes the grid makes on its own behalf - a
// reverse-image search, a delete, "use as input" - because those close the
// lightbox precisely to show their result in the grid behind it, and leaving for
// the shelf would put that result on a screen that has no grid. Only the
// reader's own close (the X, Escape) goes back. Either way `from` is dropped: it
// is one close's instruction, not a sticky property of the route.
export function overlayCloseTarget(query, returnToOrigin = true) {
  const { overlay: _overlay, from, ...rest } = query || {};
  if (
    returnToOrigin &&
    typeof from === "string" &&
    from.startsWith("/") &&
    !from.startsWith("//")
  ) {
    // Everything else in the query belonged to the route being LEFT, so it is
    // dropped - except the share token, which belongs to the session. Its rule
    // is `useAppNavigation.withShareToken`'s: "a share session's credential
    // lives in `?token=`, so a navigation that drops it leaves the visitor on a
    // URL that 401s on the next reload". This close does not go through that
    // helper (it is the router's own `replace`), so it carries it itself.
    return { path: from, query: rest.token ? { token: rest.token } : {} };
  }
  return { query: rest };
}
