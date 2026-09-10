- Importing a folder of pictures now reads the caption files beside them.
  The folder read lists every way a `.txt` or `.caption` file is named after
  its picture (`photo.txt`, `photo_tags.txt`, `photo.jpg.caption`, whatever
  the exporter used), reads a few of each to tell tag lists from prose, and
  the "This is what your folders
  become" step asks you to confirm each one as tags, a description, or
  something to ignore. Confirmed files become the picture's tags or
  description; a picture whose file gave no tags (no file, or an empty one)
  is tagged by PixlStash as before. Before,
  the in-place import ("Add a library" on a folder holding pictures, and the
  first-run offer) skipped the files and tagged everything from scratch.
