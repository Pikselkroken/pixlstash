- Workflows group more sensibly. Ones that differed only by leftover nodes,
  a text box, film grain, a loader variant (GGUF, multi-GPU, the PixlStash
  shelf loaders) or seed variance are now one workflow, and their names,
  notes, defaults and saved recipes move with them.
- Workflows built on base models of different families (Flux and Qwen, say)
  are never combined. Where an existing workflow splits this way, each part
  keeps its name, notes and defaults, and each saved recipe follows the model
  it was saved on.
- A detailer-only workflow is one workflow whether or not it upscales
  afterwards, and an upscale-only workflow is no longer folded in with others.
- A workflow whose base model PixlStash cannot place in a family stays on its
  own until the model shelf knows it: set the base model on the shelf (or let a
  scan identify it) and the workflow joins its family's, bringing its name,
  defaults and saved recipes. Z-Image fine-tunes named with `zib` or `zit` are
  recognised.
- Seed variance is a stage like upscale: on or off in a workflow's default
  recipe, and switchable per run.
