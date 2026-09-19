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
// `from` comes out of the URL bar, so it is checked twice before it is used.
// Its SHAPE must be a same-document absolute path - a protocol, a
// protocol-relative host or a repeated parameter arriving as an array is not
// one - and its TARGET must be a destination the router actually has, which
// `isDestination` answers for the caller that owns a router.
//
// The second check is not redundant. The router's catch-all
// (`{ path: "/:pathMatch(.*)*", redirect: "/" }`) matches everything, so
// `?from=/nonsense` is shaped correctly, resolves to nothing, and would close
// the lightbox onto the home view - All Pictures, the very place this exists to
// stop the reader landing on - rather than falling back to the plain close.
// `matched.length` cannot tell the two apart (the catch-all is a match, and
// reports one); a real destination is one with a name, and the only unnamed
// routes are the two redirects.
//
// A caller that passes no `isDestination` gets the shape check alone.
//
// `returnToOrigin` is false for the closes the grid makes on its own behalf - a
// reverse-image search, a delete, "use as input" - because those close the
// lightbox precisely to show their result in the grid behind it, and leaving for
// the shelf would put that result on a screen that has no grid. Only the
// reader's own close (the X, Escape) goes back. Either way `from` is dropped: it
// is one close's instruction, not a sticky property of the route.
export function overlayCloseTarget(
  query,
  returnToOrigin = true,
  isDestination,
) {
  const { overlay: _overlay, from, ...rest } = query || {};
  if (
    returnToOrigin &&
    typeof from === "string" &&
    from.startsWith("/") &&
    !from.startsWith("//") &&
    (!isDestination || isDestination(from))
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
