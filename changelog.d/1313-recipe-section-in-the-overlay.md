- The picture viewer's sidebar is now two tabs, **Info** and **Recipe**. Info is
  everything it showed before. Recipe is everything about how the picture was
  made, for anything that came out of ComfyUI: which workflow, with a link that
  opens it in Workflows; the prompt; the models it used, with the strength each
  LoRA was loaded at and a tick beside any file the recipe named exactly; the
  sampler settings, the seed and the negative prompt; and the pictures the run
  actually loaded as inputs. Click a model to open it on the model shelf, or
  generate variants without leaving the viewer.
- The workflow JSON, with its Copy and Download buttons, has moved out of
  Metadata and into the Recipe tab, beside everything else about how the picture
  was made.
- Fixed: the prompt, models and seed PixlStash reads from a ComfyUI picture are
  now taken from the graph ComfyUI actually ran, not from the editor's copy of
  it. Where the two disagreed — most often when the prompt is built by a
  wildcard or style node — the picture could show a completely unrelated prompt,
  and sometimes no models or seed at all. This corrects pictures as they are
  imported or rescanned; pictures already in your library keep what was stored
  for them until they are read again.
- Everything PixlStash knows about how a picture was made now comes from one
  place, so the viewer's Recipe tab and the Generate variants dialog can no
  longer disagree about the same picture. Pictures from Stable Diffusion web UI
  (A1111 and its forks) get the Recipe tab too, which they did not before.
