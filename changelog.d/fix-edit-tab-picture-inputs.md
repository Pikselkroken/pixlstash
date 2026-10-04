- The picture overlay's Edit tab now lists workflows that need more than the
  open picture, such as a reference image; choosing one opens the Run popup to
  pick it. A Load Image node nothing is connected to no longer blocks a run.
- Fixed "ComfyUI prompt request failed" when running a workflow taken from a
  picture whose Load Image file ComfyUI could not read.
- Workflow names count reference pictures, not reference nodes: a Flux 2 edit
  with one reference picture said "+ 4 References" and now says "+ 1 Reference".
