- Adding a watch folder now refuses one that sits inside another of your
  libraries, inside a reference folder, or inside a folder you already watch.
  A watch folder set to delete after import used to accept any of those and
  then copy the pictures into the library you had open and delete the
  originals, leaving the library that owned them pointing at files that were
  gone.
- Moving a reference folder now refuses a destination inside another library or
  inside a watched folder, and says so before anything is moved.
