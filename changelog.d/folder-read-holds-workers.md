- Fixed: reading a folder tree on the mapping screen no longer fights the
  background workers for the GPU. Each face-detection batch of the read used to
  alternate with a queued tagging or embedding task, swapping models every time;
  the read now pauses the planner for a minute at a time, renewed on every batch,
  so a stalled read still hands the workers back on its own.
