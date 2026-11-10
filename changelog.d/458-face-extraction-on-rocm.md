- On an AMD GPU (ROCm, experimental), face extraction and the WD14 tagger now
  run on the GPU when the AMD build of ONNX Runtime (`onnxruntime-migraphx`) is
  installed. Without it they stay on the CPU, and the startup log says how to
  get it.
