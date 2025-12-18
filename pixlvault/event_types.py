from enum import auto, Enum


class EventType(Enum):
    CHANGED_PICTURES = auto()
    CHANGED_TAGS = auto()
    CHANGED_CHARACTERS = auto()
    CHANGED_DESCRIPTIONS = auto()
    CHANGED_FACES = auto()
    QUALITY_UPDATED = auto()
