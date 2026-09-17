- Use a LoRA from your shelf with a workflow that has no LoRA loader: PixlStash
  adds one for that run, straight after the model loader, and tells you where it
  goes and what it feeds before you start. It uses ComfyUI's own LoRA loader when
  your ComfyUI has the file, and otherwise the ComfyUI-PixlStash loader (except
  in "Generate variants", which keeps to ComfyUI's own).
  A workflow it cannot place one in, such as one loading two models, says why.
