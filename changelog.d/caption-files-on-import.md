- Caption files beside your pictures are read when a library is imported in
  place. The import lists each naming pattern it found (`photo.txt`,
  `photo_tags.txt`, `photo.caption`...) with a sample, and you confirm which
  hold tags, which hold descriptions and which to leave alone before anything
  is written. Pictures with a tags file are not tagged from scratch.
- Fixed: a small library was sometimes indexed before the first-run import
  offer could appear, so the folder-mapping questions were never asked. The
  library now waits for your answer, and an empty library over a folder that
  already holds pictures offers "Import them" as the way back in.
