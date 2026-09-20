- When the same model file is on your disk twice, the model shelf can now keep
  one copy and remove the rest: select the row and choose **Keep one copy…**.
  Nothing is pre-selected, so you say which one stays, and the others go to your
  trash.
- The model stays on the shelf with its name, base model, triggers and people
  intact, and PixlStash remembers that the copy you removed was the same model.
  So a picture's recipe still names it, and a workflow you run from PixlStash
  loads the copy you kept instead of stopping on a missing model. It checks with
  your ComfyUI that the copy you kept is one it can load, and it tells you which
  file it used.
- Before anything is removed, PixlStash says whether your ComfyUI is reading the
  copy you are about to lose. A workflow you open in ComfyUI and run there still
  names the old file; that is the one case this cannot fix.
