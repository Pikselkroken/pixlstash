# Third-party notices

The PixlStash desktop installer and Docker images ship third-party programs
beside PixlStash's own code. Python packages carry their licence texts in their
own `*.dist-info/licenses/` folders inside the bundled environment. This file
covers what those folders do not.

## FFmpeg

PixlStash makes playable copies of videos a browser cannot play, and reads
their audio, by running the `ffmpeg` program that the
[imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) package ships. It
runs as a separate process and is never linked into PixlStash.

- imageio-ffmpeg's own Python code is BSD-2-Clause.
- The `ffmpeg` binary inside it is built by
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) with
  `--enable-gpl --enable-version3`, and includes libx264 and libx265. That
  build is licensed under the **GNU General Public License, version 3**
  (the root `LICENSE` holds the text).
- FFmpeg is a trademark of Fabrice Bellard. See <https://ffmpeg.org/legal.html>.

**Source.** The binary reports its exact source revision and build
configuration with `ffmpeg -version`. The corresponding source is FFmpeg at that
revision (<https://git.ffmpeg.org/ffmpeg.git>, mirrored at
<https://github.com/FFmpeg/FFmpeg>) together with the build scripts and
library sources of BtbN/FFmpeg-Builds for the same date. On request, for three
years from the date you received a copy of PixlStash, the PixlStash maintainer
will provide that complete corresponding source for the ffmpeg binary shipped
with it, on a medium customarily used for software interchange, for no more
than the cost of physically performing the distribution. Ask through the
contact address in `SECURITY.md`.

To use a different ffmpeg, set the `IMAGEIO_FFMPEG_EXE` environment variable
to its path.

## libheif and libde265

HEIC/HEIF pictures are decoded by [pi-heif](https://github.com/bigcat88/pillow_heif),
whose wheels bundle libheif and libde265 under the GNU LGPL v3. Their licence
texts ship in `pi_heif-*.dist-info/licenses/`.
