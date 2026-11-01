- Fixed: a link saved to a project's files must now be an http or https
  address. Other kinds of link could run script in the session of whoever
  clicked them, and old ones are no longer opened.
- Fixed: share-link tokens are no longer written to the server log.
- Fixed: the desktop app now checks that it is talking to the backend it
  started before signing in, refuses web permissions such as camera and
  location, and lets only the PixlStash window use its desktop features.
- Fixed: watched folders and the workflow inbox could stop noticing new
  files after a library snapshot, until PixlStash was restarted.
