- Fixed: background work that uses the GPU (face detection, embeddings,
  tagging) could stop for good after a few hours of ComfyUI holding the
  graphics card, until PixlStash was restarted. It keeps running now.
