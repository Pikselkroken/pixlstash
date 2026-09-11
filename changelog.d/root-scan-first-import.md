- Fixed: importing a folder of pictures as a new library sometimes skipped the
  questions about what its folders are. On a small library the background scan
  indexed every picture before the screen came up, so the app saw a library
  that was no longer empty and never made the offer. The scan now waits until
  you have answered it, or chosen "organise later".
- Fixed: a click beside the import wizard closed it, mid-answer, and the empty
  library it left behind offered "Choose a folder…", which refused the folder
  the library already was. The wizard now ignores clicks beside it, and an
  empty library whose folder holds pictures offers to import them instead.
