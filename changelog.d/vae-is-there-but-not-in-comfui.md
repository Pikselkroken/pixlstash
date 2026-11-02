- Fixed: a model downloaded from inside PixlStash now goes into your model
  folder's own subfolder for its kind, however your launcher spells it
  (Stability Matrix's `VAE`, `TextEncoders`, `DiffusionModels`), so PixlStash
  knows what it is and runs load it off the shelf.
- Fixed: MiniMax H3's video VAE is recognised as a VAE wherever it is, so a
  workflow that needs it loads it off the shelf instead of reporting it
  missing.
- Fixed: a ComfyUI linked on this computer no longer loses PixlStash when
  another copy of PixlStash has run. The desktop app gives ComfyUI its
  configured port when remote access is on, and otherwise re-sends its address
  once every session as soon as ComfyUI answers.
