- The GPU Docker image runs face detection and the WD14 tagger on the GPU
  again. Since insightface 2.0 its CPU build of ONNX Runtime replaced the GPU
  one, so both ran on the CPU.
