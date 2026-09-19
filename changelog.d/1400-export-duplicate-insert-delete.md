- Export a workflow as a ComfyUI file you can give to somebody else. PixlStash
  takes out everything that belongs to a run rather than to the workflow: the
  prompt, the seed, the LoRA slots that are part of the look, the node titles
  you wrote, the folder the pictures were saved into, the name of any picture
  that went in, and the name of any model this machine no longer has —
  including a model whose name you asked PixlStash to forget but which one of
  your pictures still records. It tells you what it left out.
- Export a saved recipe as a file too. That one shares everything, because a
  recipe is the prompt and the LoRAs, so PixlStash lists exactly what the file
  says before you save it and offers the workflow export as the safer choice.
- Duplicate a workflow into your workflow folder, so you can change it in
  ComfyUI without touching the original — and so a workflow PixlStash only
  knows from your pictures becomes a file you can open at all.
- Add a LoRA loader to a workflow that has none: PixlStash writes a copy with
  one wired in after the model, ready for you to pick a LoRA.
- Delete a workflow you imported. It goes to your system trash, and the
  workflow's pictures, ratings and notes stay. A workflow PixlStash found in
  your pictures has no file to delete and is hidden instead.
