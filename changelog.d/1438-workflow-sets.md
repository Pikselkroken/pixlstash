- The model shelf now opens on **Workflow sets**: a grid of the model
  combinations your pictures were actually made with. A card is one set of files
  that ran together — the checkpoint, its VAE, its text encoders, its LoRAs — so
  you can see what to pair with what instead of guessing. Near-identical sets
  fold into a stack you can open; the `Fold` control on the toolbar says what
  each setting costs in cards, and *Don't fold* shows every set.
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
