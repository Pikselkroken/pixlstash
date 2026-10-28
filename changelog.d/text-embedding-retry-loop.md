- Fixed: when the text-embedding model could not load, PixlStash retried the
  same pictures several times a second for as long as it ran. A batch that
  fails is now skipped until the next restart.
