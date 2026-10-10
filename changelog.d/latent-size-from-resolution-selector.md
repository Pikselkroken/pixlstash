- A workflow whose size comes from ComfyUI's Resolution Selector (or an integer
  primitive) now shows that size in its defaults and the Run popup's Size field,
  and a run can change it: the new width and height replace the selector's on
  the empty latent and on anything else it sizes the same way. An image-to-video
  workflow, which has no empty latent, is sized on the node that takes the
  `width` and `height`.
