- Clone a workflow onto a different model: select a workflow, choose "Clone
  with new models…", pick the checkpoint, and PixlStash fills in the VAE and
  text encoders your other workflows have used with it, each saying where the
  suggestion came from. Change any of them, name the copy, and it lands as a
  new card beside the original. LoRAs trained for a different model are
  pointed out and kept.
  A checkpoint nothing you have run shares a model family with still gets
  a VAE and text encoders of the type that family loads, marked as untested.
