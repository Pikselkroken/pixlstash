- PixlStash now uses the GPU on Apple Silicon Macs. Tagging and search ran on
  the processor even though the Mac build already shipped everything needed to
  use the graphics chip, so a Mac was many times slower than it had to be: the
  built-in tagger takes 0.7 seconds a batch where it used to take 45, and the
  WD14 tagger takes a fifth of the time. Your existing tags and search results
  are unaffected — the same pictures come back in the same order, so nothing is
  re-indexed.
- Fixed: work that runs out of graphics memory on a Mac now falls back to the
  processor and carries on, the way it already did on other machines, instead
  of failing outright. Unloading a model releases its memory again too; it used
  to accumulate until PixlStash was restarted.
