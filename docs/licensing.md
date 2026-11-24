# LICENSING

This document explains the repository's split-license structure.
The canonical root `LICENSE` file contains the full GPL-3.0 text.

## Overview

This is an open source project using two separate licenses:

1. The GNU GPL v3.0 license for the Python backend in pixlstash/ and tests/
2. The MIT license for the frontend, CI workflows and utility scripts

## Licensing details

### PixlStash Backend

Basics:
- Located in the "pixlstash" directory
- Licensed under the GNU General Public License v3.0 (GPL-3.0).
- See pixlstash/LICENSE for the full license text.
- Contributions to the backend require agreement to the CLA in pixlstash/CLA.md.
- As an exception, the plugin base classes and templates are MIT-licensed, so
  third-party plugin authors can depend on those APIs without pulling in the GPL
  - `pixlstash/image_plugins/base.py`
  - `pixlstash/image_plugins/built-in/plugin_template.py`
  - `pixlstash/tagger_plugins/base.py`
  - `pixlstash/tagger_plugins/plugin_template.py`

### PixlStash Frontend
- Located in the "frontend" directory
- Licensed under the MIT License.
- See frontend/LICENSE for the full license text.
- No CLA is required for frontend contributions.

### Logos, Example Images and Utility Scripts
- Located in the "pictures" and "scripts" directories. The project website is its own repository, pixlstash-website, under the same terms.
- Licensed under the MIT License.
- However: PixlStash name and logo are property of the project’s copyright owner. You may not use the PixlStash name, logo, or branding in a way that suggests official endorsement or affiliation without explicit permission.

### Third-party program: ffmpeg
- PixlStash installs the Python package `imageio-ffmpeg` (BSD-2-Clause), whose
  wheels carry a prebuilt `ffmpeg` executable. That executable is a GPL build of
  FFmpeg (configured with `--enable-gpl`, including libx264 and libx265), so the
  copy distributed in the desktop installer and the Docker images is licensed
  under the GNU GPL.
- PixlStash runs it only as a separate program, over the command line and pipes,
  to make playable copies of videos and read audio waveforms. It is never
  imported or linked into PixlStash.
- The binaries are prebuilt upstream, not by PixlStash: Linux and Windows on Arm
  from BtbN/FFmpeg-Builds (https://github.com/BtbN/FFmpeg-Builds), Windows x64
  from gyan.dev (https://www.gyan.dev/ffmpeg/builds/), macOS from evermeet.cx
  and osxexperts.net, as listed by imageio-ffmpeg's `tools/download_ffmpeg.py`
  (https://github.com/imageio/imageio-ffmpeg). The version string `ffmpeg
  -version` prints names the FFmpeg release each was built from; FFmpeg's
  source for every release is at https://ffmpeg.org/download.html, and the
  BtbN repository is itself the build scripts for its builds.
- Set `IMAGEIO_FFMPEG_EXE` to the path of another ffmpeg to use that instead.

To avoid ambiguity, this file does not assign license by "subject matter".
Use the license files in each component path (for example `pixlstash/LICENSE`,
`frontend/LICENSE`, `scripts/LICENSE`, and `tests/LICENSE`) to
determine the applicable license for files in that path.

This document is informational and does not replace the component license texts.

