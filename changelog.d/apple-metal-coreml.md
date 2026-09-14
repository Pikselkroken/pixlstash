- PixlStash uses the GPU on Apple Silicon Macs. Tagging, captioning and
  embedding run on Metal instead of the CPU, with the WD14 tagger on CoreML;
  face detection and recognition stay on the CPU. Nothing in an existing library is re-indexed.
