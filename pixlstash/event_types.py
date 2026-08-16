from enum import auto, Enum


class EventType(Enum):
    CHANGED_PICTURES = auto()
    PICTURE_IMPORTED = auto()
    PLUGIN_PROGRESS = auto()
    CHANGED_TAGS = auto()
    CHANGED_CHARACTERS = auto()
    CHANGED_DESCRIPTIONS = auto()
    CHANGED_FACES = auto()
    QUALITY_UPDATED = auto()
    CLEARED_TAGS = auto()
    SNAPSHOT_CREATED = auto()
    SNAPSHOT_DELETED = auto()
    RESTORE_STARTED = auto()
    RESTORE_COMPLETED = auto()
    RESTORE_FAILED = auto()
    # The active library changed underneath every connected client. Their view
    # now describes a library the server is no longer serving, and picture ids
    # do not carry across, so the only honest response is a full reload.
    LIBRARY_SWITCHED = auto()
    # A GPU task ran out of VRAM and is being retried (or has given up). The
    # only event here that carries no picture ids: it describes the machine,
    # not the library, and the SPA renders it as a warning toast.
    VRAM_OOM = auto()
