- Fixed: the check before a run now looks at the models loaded through
  ComfyUI-MultiGPU loaders and the GGUF CLIP loader, so a missing UNET, VAE or
  text encoder in those workflows is reported before you wait for the run to
  fail. A workflow saved from the ComfyUI editor shows those models on its card.
