- Fixed: a VAE, text encoder or diffusion model downloaded from inside
  PixlStash now goes into your model folder's own subfolder for its kind,
  however your launcher spells it (Stability Matrix's `VAE`, `TextEncoders`,
  `DiffusionModels`), so PixlStash files it correctly and runs can load it off
  the shelf.
- Fixed: MiniMax H3's video VAE is recognised as a VAE wherever it is, so a
  workflow that needs it loads it off the shelf instead of reporting it
  missing.
- Fixed: a ComfyUI linked on this computer kept calling a PixlStash that had
  stopped after another copy of PixlStash ran. With remote access on, the
  desktop app now gives ComfyUI its configured port. Otherwise it sends ComfyUI
  its address again at the start of every session.
