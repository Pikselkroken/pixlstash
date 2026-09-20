- The model shelf now opens on **Workflow sets**: a card for each checkpoint
  your pictures were made with, and opening one shows every model that has run
  alongside it — its VAEs, its text encoders, its LoRAs — with how many of your
  pictures back each one. So you can see what to pair with what instead of
  guessing. Read a set as cards or as a comparison list, the same switch the
  Workflows screen's stacks use.
- Each model in a set says whether it is shared. `Also in 3 other sets` on a VAE
  three checkpoints have used; `Only in this set` on one that has served just the
  one. What a set does **not** claim is that its files all ran together at once —
  it is everything that checkpoint has ever loaded, and the set says so.
- A model belongs to every set it has run in, so the same VAE appears beside
  each checkpoint it has served. **Works with** shows everything one model has
  been seen alongside, ranked by how many recipes back each pairing: click a
  file's name inside an opened set, or pick **Works with** from any single
  model's right-click menu.
- Nothing is ruled out by any of this. A pairing you have never tried simply is
  not drawn, and the models nothing here was made with get a card of their own
  saying so — not a verdict that they do not work. The old `None`, `Base model`,
  `Folder` and `Feature` groupings are all still there under `Group`.
- **The shelf's verbs work on the sets screen too.** Click a card to select the
  checkpoint it is named after, or a model inside an opened set to select that
  one file; Ctrl-click and Shift-click build a selection the same way they do in
  the list. Right-click gives you the same menu — rename, set a base model,
  move, set a thumbnail, forget, delete — and the selection bar floats over the
  cards as it does over the rows. A card is only ever the one model it is named
  after, never the whole set, so a Delete cannot take a shared VAE with it —
  and where that model is one step of a stacked run, the card takes the run
  whole, exactly as a run behaves everywhere else on the shelf.
- Changing the default resets your grouping once, on the first open after
  updating. Everything else you had set — the sort, any column widths you had
  dragged, collapsed groups, the folder layout — is kept.
