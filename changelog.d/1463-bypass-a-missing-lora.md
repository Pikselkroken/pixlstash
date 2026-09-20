- A LoRA your ComfyUI does not have no longer stops a run. PixlStash skips that
  loader and runs the workflow without the LoRA, instead of refusing — so one
  missing LoRA on one picture no longer holds back a whole **Make more like
  these…** over a selection.
- It says so first. The Run popup names the LoRA it is leaving out, and where
  the file would have to go to get it back, before you press Run — the picture
  will look different without it.
- A missing checkpoint, VAE, text encoder or ControlNet still stops the run:
  the workflow cannot run without those. Neither is a LoRA skipped when its
  loader also holds LoRAs you do have, or when you asked for that LoRA yourself.
- The pictures a run like that makes are filed under their own workflow, not
  the one you started from: without the LoRA it is a different workflow, and
  PixlStash files pictures by what actually ran. Install the file and runs go
  back to the original.
