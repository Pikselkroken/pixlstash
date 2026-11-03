- Fixed: a workflow that loads a diffusion model in fp8 (Load Diffusion Model
  with a weight type other than default) now runs when only your model shelf
  has the file. It used to call the model missing. It needs the updated
  ComfyUI-PixlStash node pack; with an older one the workflow says to restart
  ComfyUI or update the pack.
