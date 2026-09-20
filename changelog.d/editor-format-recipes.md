- Pictures saved with ComfyUI's editor workflow, rather than the graph the
  server ran, now show their recipe in the lightbox and can be run again.
  PixlStash rebuilds the workflow against your ComfyUI's own node list; where it
  cannot rebuild one exactly it says which node it could not read, and shows the
  prompt, models and seed anyway rather than pretending the picture was never
  made in ComfyUI.
- The lightbox's Recipe tab is no longer shown for a picture that has no recipe
  at all. It used to be there on every photo, with nothing behind it.
