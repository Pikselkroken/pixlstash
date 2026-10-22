- ComfyUI runs wait for GPU memory instead of running out of it. A run that
  does not fit waits in the Tasks tab under "Waiting for the GPU", saying what
  it needs, what is free and who holds the rest, and starts by itself once
  there is room. You can start it anyway or cancel it.
- The Tasks tab shows a memory bar for each GPU: what PixlStash, ComfyUI and
  other apps hold, and what is free. PixlStash's own work is one folded
  "PixlStash processing" row.
- PixlStash learns how much memory each workflow needs from its runs. The
  workflow card shows it, and the Workflow tab shows the measured runs and a
  "Run alone on the GPU" switch. A workflow that runs out of memory runs alone
  from then on; you can turn that off in Settings › Compute.
- PixlStash steps aside for ComfyUI: it pauses its own GPU work and unloads its
  models when a run needs the room, and nothing it was doing is lost.
- Settings › Compute lets you pick which GPU PixlStash uses on a machine with
  more than one. Auto picks a spare GPU that ComfyUI is not using.
- Fixed: starting PixlStash next to a ComfyUI that had a model loaded could
  switch it to the CPU for the whole session, or refuse to start when the GPU
  was chosen explicitly.
- Fixed: the "GPU ran out of memory" notice now names the other program
  holding the card where the system can tell.
