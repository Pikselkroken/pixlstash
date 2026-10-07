- Connecting ComfyUI now puts its models folder on your model shelf and scans
  it, so the checkpoints and LoRAs ComfyUI uses show up without adding the
  folder by hand. A ComfyUI on another machine is left alone.
- Settings finds a ComfyUI running on this computer and connects it in one
  click. An address you type is checked before it is saved, so a wrong port
  says so straight away instead of failing at the first run.
- Link ComfyUI from Settings: PixlStash gives ComfyUI's PixlStash nodes a
  full-access key, so they can load and save pictures with no token to copy.
  This works for a ComfyUI on this computer, or on your local network when
  PixlStash's remote access is on with HTTPS.
- PixlStash now ships the ComfyUI-PixlStash nodes and installs them into a
  ComfyUI on this computer from the Connect dialog, replacing an older copy
  (it goes to the trash) and restarting ComfyUI through ComfyUI-Manager.
