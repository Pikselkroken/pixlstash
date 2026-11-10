"""``python -m pixlstash.telemetry <server-config>``: record the install identity.

For writers of a fresh ``server-config.json`` that are not Python - the Docker
entrypoint and the desktop's first-run setup - to call *before* they write it.
Once the config exists the identity is classed as an upgrade (see
``install_id.ensure_install_identity``).
"""

import sys

from pixlstash.telemetry.install_id import ensure_install_identity

if len(sys.argv) != 2:
    sys.exit("usage: python -m pixlstash.telemetry <server-config path>")
# Exit 0 even when it could not be stored: telemetry never stops a first run.
ensure_install_identity(sys.argv[1])
