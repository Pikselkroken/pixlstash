- Running a workflow is one popup now, wherever you start from. Press Run… on a
  picture's Recipe tab or on a workflow in Workflows, and the same window opens
  with the picture beside the form and every value filled in and editable —
  prompt, LoRAs, size, steps, CFG, seed and checkpoint, with the rest under
  "All N parameters". A field you change shows a ↺ chip carrying the value it
  started at, so you can always put it back.
- Right-click a selection, or open the selection menu, for *Make more like
  these…* and *Run a workflow on these…*. Make more reruns each picture's own
  recipe with a new seed; when the pictures use several recipes it says so and
  lets you set the count and the seed once for all of them.
- A run that cannot start stays on screen with the reason and what to do about
  it, rather than disappearing — a missing model is named along with the folder
  it belongs in, and it stops the whole batch so you do not have to repeat the
  gesture after installing it.
- Starting a run is now something only the library's owner can do. *Generate
  variants…* was reachable from a share link; nothing that starts a run is any
  more. A share link can still read a picture's recipe.
- The old *Generate variants…* dialog and the run panel in the right-hand rail
  are gone; the Run popup replaces both. The toolbar's Generate button stays
  where it was and opens the popup instead, so generating with nothing selected
  is still one click from the grid.
- Gone for now: the per-picture caption an image-to-image run from the run
  panel took.
- The Run popup lists the LoRAs the workflow already loads, matched to your
  model shelf, so you can swap one or change its strength. A LoRA the shelf has
  never seen is still shown, named, and left exactly as the workflow has it.
- Gone for now: putting a LoRA into a workflow that has **no** LoRA loader.
  *Generate variants…* could add a loader for you; the new popup tells you the
  workflow has none and offers to run without the LoRA. Workflows that already
  have a loader are unaffected.
