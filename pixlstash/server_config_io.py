"""Writing ``server-config.json`` back without the running process's overrides.

The config dict a ``Server`` holds is both what the owner wrote and what this
process decided at boot: ``PIXLSTASH_DEFAULT_DEVICE`` (the desktop shell naming
the device its active runtime supports) replaces ``default_device`` in memory.
Writing that dict straight back made the runtime's answer permanent - one boot
under the CPU-only runtime left ``"cpu"`` in the file, and every later launch
without the variable (a developer's own env, a Docker deploy) read it as the
owner's choice and ran CPU inference on a CUDA machine.

So the decided value lives under a ``_``-prefixed key that this writer never
persists, and ``default_device`` goes back to disk as the owner had it.
Writers that rewrite an existing config file should use :func:`persist_server_config`.
"""

from pixlstash.utils.atomic_write import write_json_atomic

#: The ``default_device`` the file held before an environment override replaced
#: it in memory. Present only while an override is active.
DEVICE_ON_DISK_KEY = "_default_device_on_disk"


def persist_server_config(path: str, config: dict) -> None:
    """Write *config* to *path*, minus what only this process decided.

    Restores ``default_device`` to the on-disk value when an override is
    active and drops every ``_``-prefixed key.
    """
    on_disk = dict(config)
    if DEVICE_ON_DISK_KEY in on_disk:
        on_disk["default_device"] = on_disk[DEVICE_ON_DISK_KEY]
    for key in [k for k in on_disk if k.startswith("_")]:
        del on_disk[key]
    write_json_atomic(path, on_disk)
