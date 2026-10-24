- The Workflow tab's picker for a missing checkpoint now lists every checkpoint
  the workflow can load, sorted under *Likely to work*, *Might work*, *Not
  known* and *Unlikely to work* by its base model, with that base model on each
  row. The group's mark stays beside your pick, with a line saying what it
  means. A missing VAE or text encoder is sorted the same way, by what has run
  with the checkpoint.
