- A run at a larger size than a workflow has run before, such as a 6K
  character sheet after 5K ones, no longer waits for GPU memory that never
  comes free: it asks for no more room than the earlier run finished in, since
  ComfyUI moves models out to system memory to make space.
