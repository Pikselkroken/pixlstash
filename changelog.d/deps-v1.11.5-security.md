- Removed the python-jose package, which PixlStash installed but never used, so
  security scanners no longer flag its unpatched advisory. Updated Vue to
  3.5.43 for an advisory in its server-side renderer, which the app does not
  use.
