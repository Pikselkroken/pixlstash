- Fixed: a ComfyUI run could wait for GPU memory when ComfyUI itself held it,
  until you pressed Run anyway. ComfyUI makes room in its own memory, so the
  run now starts.
