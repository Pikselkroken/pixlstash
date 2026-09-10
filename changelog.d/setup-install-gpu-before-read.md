- Changed: first-run setup now finishes installing the GPU runtime before it
  reads your library, instead of reading it while the runtime downloads. The
  read used to run on the CPU whatever hardware you had, which on a large
  library took longer than waiting for the download would have.
