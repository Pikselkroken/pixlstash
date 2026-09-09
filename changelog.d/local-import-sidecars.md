- Importing a folder of pictures now reads the caption files beside them.
  The folder read lists every naming convention it finds (`photo.txt`,
  `photo_tags.txt`, `photo.jpg.caption`, whatever the exporter used), reads a
  few of each to tell tag lists from prose, and the "Before anything is
  written" step asks you to confirm each one as tags, a description, or
  something to ignore. Confirmed files become the picture's tags or
  description, and the tagger only runs on pictures that have none. Before,
  the in-place import ("Add a library" on a folder holding pictures, and the
  first-run offer) skipped the files and tagged everything from scratch.
