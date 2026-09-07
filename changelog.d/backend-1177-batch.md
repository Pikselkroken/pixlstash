- Fixed: on Windows, a picture whose name was one of the reserved device names
  - `NUL`, `CON`, `COM1` and the rest - went missing from an export. The file
  was written to the device instead of to your folder, so the export finished
  without it and said nothing. Those pictures now export under a name Windows
  can hold.
- When PixlStash is set to use the GPU and cannot find one, it refuses to start
  rather than quietly switching to the processor - and now tells you which file
  to edit, and what to put in it, to start anyway.
