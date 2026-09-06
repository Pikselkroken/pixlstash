- `libraries backup` now counts picture files in the library folder that have
  no PixlStash record and asks whether to include them. Enter keeps them, so a
  restore puts back exactly what was there; `--skip-orphans` leaves them out
  without asking, and `--yes` includes them.
