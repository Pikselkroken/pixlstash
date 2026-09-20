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
  file inside an opened set, or pick it from a single model's right-click menu
  in the row list.
- Nothing is ruled out by any of this. A pairing you have never tried simply is
  not drawn, and the models nothing here was made with get a card of their own
  saying so — not a verdict that they do not work. The old `None`, `Base model`,
  `Folder` and `Feature` groupings are all still there under `Group`, and that
  is where you go to rename, move or delete a model: a card stands for several
  files at once, so the shelf's verbs stay with the list of single ones.
- Changing the default resets your grouping once, on the first open after
  updating. Everything else you had set — the sort, any column widths you had
  dragged, collapsed groups, the folder layout — is kept.
