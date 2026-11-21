- PixlStash ships workflows for MiniMax H3 Image, MiniMax's own still-image
  model: text to image, image edit, inpaint, and character sheets from words
  or from a reference picture. The model library now tells it apart from the
  MiniMax H3 video model, so a sheet drawn with it offers only its own LoRAs.
  Its edit and inpaint workflows have Qwen3.5 expand your instruction first,
  as ComfyUI's own MiniMax H3 Image template does. Each comes in a second
  version, named "nvfp4 TE", that loads a smaller text encoder (about 16 GB
  instead of 27 GB) and runs natively on NVIDIA Blackwell cards.
- Every workflow PixlStash ships is now on the Workflows page from the start,
  and one you remove stays removed.
