- Fixed: a workflow that loads a diffusion model in fp8 (Load Diffusion Model
  with a weight type other than default) now runs when only your model shelf
  has the file. It used to call the model missing. It needs the updated
  ComfyUI-PixlStash node pack.
- When ComfyUI's PixlStash nodes are too old to load a model from your shelf,
  the workflow's Models section and the Run popup now offer **Update and
  restart ComfyUI**, which installs the nodes PixlStash ships and checks again
  once ComfyUI is back.
