- Fixed: importing a very large zip (many GB) no longer uploads it up to three
  times, checks every picture in it once per upload, or freezes the app while
  the server unpacks it.
- Fixed: an import that takes longer than ten minutes no longer stops with
  "Import timed out" while the server is still working through it.
- The Arriving band over the grid has a caret that folds an import down to
  its top line, so the progress stays in view without the tiles.
