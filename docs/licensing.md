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
- The desktop installer and the Docker images carry an unmodified build of
  FFmpeg, licensed under the GNU Lesser General Public License, version 3 or
  later (macOS: version 2.1 or later). It is an LGPL build: no `--enable-gpl`, no
  `--enable-nonfree`, no libx264 or libx265. H.264 is encoded with Cisco's
  OpenH264 (BSD-2-Clause) on Windows and Linux, and with Apple's VideoToolbox on
  macOS.
- PixlStash runs it only as a separate program, over the command line and pipes,
  to make playable copies of videos and read audio waveforms. It is never
  imported or linked into PixlStash.
- The binaries are mirrored from BtbN/FFmpeg-Builds (Windows, Linux; its `lgpl`
  variant) or built from FFmpeg source (macOS) by
  `.github/workflows/ffmpeg-mirror.yml` into a release of this repository named
  `ffmpeg-lgpl-<version>`. That release also holds the corresponding source: the
  FFmpeg source at the exact commit, the OpenH264 source and BtbN's build scripts
  (which pin every other library linked), with `SOURCES.txt` naming each commit.
  `packaging/ffmpeg/ffmpeg_pin.json` pins the release and each binary's SHA256;
  `packaging/ffmpeg/fetch_ffmpeg.py` is what the desktop build and the
  Dockerfiles use to fetch and verify it.
- The installer (`resources/ffmpeg/`) and the images (`/usr/share/doc/ffmpeg-lgpl/`)
  include `NOTICE.txt` and the licence texts (`COPYING.LGPLv3`, `COPYING.GPLv3`)
  from `packaging/ffmpeg/`.
- The backend uses the binary named by `PIXLSTASH_FFMPEG` (the desktop app sets
  it; the Docker images have it at `/usr/local/bin/ffmpeg`), otherwise `ffmpeg` on
  `PATH`. Set it to another ffmpeg to use that instead.
- The pip package and the Windows server installer carry no ffmpeg; they use one
  on `PATH`.

To avoid ambiguity, this file does not assign license by "subject matter".
Use the license files in each component path (for example `pixlstash/LICENSE`,
`frontend/LICENSE`, `scripts/LICENSE`, and `tests/LICENSE`) to
determine the applicable license for files in that path.

This document is informational and does not replace the component license texts.

