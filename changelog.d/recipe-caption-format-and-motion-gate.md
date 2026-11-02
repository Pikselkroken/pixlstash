- Added: the Train dialog has a Captions choice (Tags, Full text description,
  None). It starts on what the recipe suggests, which for MiniMax H3 is the
  description, matching how the model is prompted.
- Changed: a motion recipe is disabled in the Train dialog when the set has no
  video clips, the still-picture recipe of the same model is picked instead,
  and the dialog says so. MiniMax H3's still-picture recipe is now called
  "MiniMax H3 · from stills".
- Fixed: a video model's training previews (MiniMax H3 motion) now show on
  the run page and in the Models rail; they were MP4 clips PixlStash skipped.
