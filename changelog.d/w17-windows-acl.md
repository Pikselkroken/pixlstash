- On Windows, PixlStash now checks who else can write to a library's folder
  before opening it. A library in a folder that other accounts on the computer
  can change (for example a folder created at the top of a second drive) is no
  longer opened; the error names the folder and gives the `icacls` command
  that removes the other accounts' access. Libraries and settings under your
  user profile are unaffected. Libraries on drives that keep no permissions
  (FAT32/exFAT USB sticks) still open, with a warning in the log.
