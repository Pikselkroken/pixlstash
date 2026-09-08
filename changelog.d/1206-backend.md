- Export to folder now refuses a destination inside *any* of your libraries,
  not only the one you are looking at. Exporting into another library's folder
  used to be accepted, and everything written there came back as a fresh set of
  pictures the next time you opened it.
- Deleted pictures no longer use the GPU for tagging. The "awaiting tagging"
  number left them out while the tagger went on working through them, so the
  count could read zero while your card stayed busy.
- PixlStash now says so when the WD14 tagger falls back to the CPU. It asked
  onnxruntime what the build supports rather than what the tagger actually
  loaded, so a GPU that could not be used looked exactly like one that was.
- Background work no longer stalls re-checking files on a drive that has gone
  away. The list of files to skip was rebuilt several times a sweep, and every
  rebuild waited on the missing drive.
- `pixlstash-cli plugins install --with-deps` now lists the pip options a
  plugin's `requirements.txt` reaches through an `-r` include or a line
  continuation. The include line was shown, but not the `--index-url` behind
  it that decides where the packages come from.
