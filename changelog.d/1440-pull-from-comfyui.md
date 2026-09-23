- Workflows can now pull every workflow your ComfyUI has saved: press
  **Pull from ComfyUI** on the Workflows screen. PixlStash only reads from
  ComfyUI, never writes back, and pulling again adds nothing twice. A workflow
  you delete in PixlStash is not brought back by the next pull.
- After a pull, the Workflows screen says what it found: how many workflows
  were new, how many your pictures were already made with, which ones won't run
  on that ComfyUI because a node pack is missing, and which name model files it
  doesn't have.
