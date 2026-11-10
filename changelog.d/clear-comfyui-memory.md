- When a training run needs memory a local ComfyUI is holding, PixlStash now
  offers to clear ComfyUI's memory first, so ComfyUI stays open. Quitting it
  becomes the fallback. The run waits in Tasks with a short progress track
  while ComfyUI finishes its jobs and clears. If that frees too little, Tasks
  offers to quit ComfyUI or run anyway.
- ComfyUI has its own row in Tasks with a broom that clears its memory any
  time it is idle.
