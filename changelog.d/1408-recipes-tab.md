- Workflows has a **Recipes** tab beside Workflow. It lists **every recipe
  your pictures were made with on that workflow**, filled in by itself: each
  distinct prompt-and-LoRAs combination, with how many of your pictures it
  accounts for and a picture of one of them, which opens the picture. *Run…*
  opens the Run popup with the prompt filled in and editable, and *Clone…*
  copies the recipe into **Saved**, where you can name and reorder it; the
  original stays in the list, marked Saved. Select several workflows and you
  get the union of what they have between them.
- Saved recipes sit above the rest, each with the picture it was saved from,
  its prompt, its LoRAs with their strengths, and how many of your pictures it
  accounts for. Run… on a card opens the Run popup already filled in with that
  recipe. Drag a card by its handle to reorder, or hold Alt and use the arrow
  keys; ⋯ renames, exports or deletes one. A recipe runs on any workflow in
  the stack it was saved from, so picking a member of a stack shows the
  stack's recipes.
- *Save as recipe* now opens a window listing what the recipe will keep —
  prompt, LoRAs, and each setting you changed with the workflow's own value
  beside it — and each line can be unticked. **The seed is off unless you tick
  it**, because a recipe that keeps a seed makes the same picture every run. A
  LoRA that is not on your model shelf is kept in the recipe and named, with a
  note that runs will ignore it: PixlStash cannot load a file it cannot
  identify.
- A picture's Recipe tab says when it already matches one of your saved
  recipes and names it, and *Save as recipe* reads *Saved* rather than
  offering to keep the same look twice.
- *Export…* on a saved recipe says exactly what the file tells whoever opens
  it — your prompt word for word, the LoRA file names, your settings, and a
  warning when it names a model this machine no longer holds — before anything
  is written. **Export workflow** is offered beside it for sharing the
  workflow without the look, and says what it left out.
