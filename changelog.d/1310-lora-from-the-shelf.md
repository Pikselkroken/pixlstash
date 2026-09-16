- Swap a LoRA from your model shelf into a run, wherever you start one: the run
  panel, "Edit with ComfyUI", and "Generate variants" on a picture's own
  workflow. The one you pick goes into the workflow's LoRA loader under the name
  your ComfyUI has that file under, and where a workflow chains more than one
  loader you choose which. A workflow with no LoRA loader says so rather than
  starting a run without it.
- Re-run a picture whose LoRA you no longer have, by swapping in one you do:
  "Generate variants" now puts the new LoRA in before it checks the workflow,
  instead of refusing the run over the file it is about to replace.
