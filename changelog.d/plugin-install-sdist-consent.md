- `plugins install --with-deps` no longer runs a dependency's build code
  before it has shown you anything. Working out what a plugin needs used to
  mean letting pip build any dependency published only as source, which runs
  that package's own setup script on your machine as you — so the list you
  were asked to agree to appeared after the code it was about had already run.
  PixlStash now works the dependencies out from pre-built packages only, and
  when one is published only as source it says which one and asks before
  running anything. Scripted installs that need it can agree in advance with
  `--allow-sdist`; a run with no terminal to ask at stops rather than
  guessing.
