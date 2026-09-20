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
- Five things the run panel could do that the Run popup cannot do yet. Nothing
  about the workflows themselves has changed and their setup in Workflows is
  untouched; what is missing is a way to start these particular runs.
- Gone for now: running a workflow **on** your pictures, with them loaded into
  it as inputs. *Use as input for…* has been renamed *Run another workflow…*,
  because that is what it now does — it opens the Run popup on another
  workflow, starting from the picture's recipe, and does not feed the picture
  into the graph.
- Gone for now: choosing a picture for a workflow's picture input at run time,
  and workflows set up with a fixed input picture.
- Gone for now: image-to-image and upscale runs over a selection, and the
  caption those took.
- Gone for now: "stack new pictures with the ones they came from" for ComfyUI
  runs — new pictures arrive in the grid unstacked. The same setting for plugin
  *Filters* runs is unaffected.
- Gone for now: putting a LoRA into a workflow that has **no** LoRA loader.
  *Generate variants…* could add a loader for you; the new popup tells you the
  workflow has none and offers to run without the LoRA. Workflows that already
  have a loader are unaffected.
