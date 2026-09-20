- Model names no longer carry their precision. A file called
  `z_image_turbo_bf16.safetensors` reads as `z image turbo` on the Models
  shelf, on a workflow card and in a picture's recipe, and the precision moves
  to a small chip beside the name — `BF16`, `FP8`, `Q4_K_M` — so two builds of
  one model are still told apart. Searching for `fp8` still finds them.
- GGUF models are now catalogued. Drop a `.gguf` file in a folder PixlStash
  scans and it appears on the Models shelf, named without its `Q4_K_M` and
  badged with it.
