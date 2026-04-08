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
- located in the "pixlstash" directory
- Licensed under the GNU General Public License v3.0 (GPL-3.0).
- See pixlstash/LICENSE for the full license text.
- Contributions to the backend require agreement to the CLA in pixlstash/CLA.md.
- As an exception, the image plugin base class and template are MIT-licensed
  - `pixlstash/image_plugins/base.py`
  - `pixlstash/image_plugins/built-in/plugin_template.py`

### PixlStash Frontend
- Located in the "frontend" directory
- Licensed under the MIT License.
- See frontend/LICENSE for the full license text.
- No CLA is required for frontend contributions.

### The Project website, logos, example images and utility scripts
- Located in the "website", "pictures" and "scripts" directories.
- Licensed under the MIT License.
- However: PixlStash name and logo are property of the project’s copyright owner. You may not use the PixlStash name, logo, or branding in a way that suggests official endorsement or affiliation without explicit permission.

To avoid ambiguity, this file does not assign license by "subject matter".
Use the license files in each component path (for example `pixlstash/LICENSE`,
`frontend/LICENSE`, `scripts/LICENSE`, `tests/LICENSE`, and `website/LICENSE`) to
determine the applicable license for files in that path.

This document is informational and does not replace the component license texts.

