- When the same model file sits on your disk twice, the model shelf can now keep
  one copy and remove the rest: select the row and choose **Keep one copy…**.
  Nothing is pre-selected, so you say which one stays, and the others go to your
  Trash.
- The model stays on the shelf with its name, base model, triggers and people
  intact, and PixlStash remembers that the copy you removed was the same model —
  so a picture's recipe still names it, and a workflow you run through PixlStash
  is put on the copy you kept instead of reporting a missing model. It says which
  file it loaded rather than swapping silently, and it checks with your ComfyUI
  first rather than assuming.
- Before the copies go, PixlStash tells you if your ComfyUI is one of the things
  reading the copy you are removing. A workflow you open in ComfyUI and queue
  there still names the file that went; that is the one case this cannot fix.
