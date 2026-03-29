from enum import Enum


class TaskType(str, Enum):
    """Identifies each background worker / task-runner lane."""

    FACE_EXTRACTION = "FaceExtractionTask"
    TAGGER = "TagTask"
    QUALITY = "QualityTask"
    FACE_QUALITY = "FaceQualityTask"
    LIKENESS = "LikenessTask"
    LIKENESS_PARAMETERS = "LikenessParametersTask"
    DESCRIPTION = "DescriptionTask"
    TEXT_EMBEDDING = "TextEmbeddingTask"
    IMAGE_EMBEDDING = "ImageEmbeddingTask"
    WATCH_FOLDERS = "WatchFolderImportTask"
    COMFYUI_EXTRACTION = "ComfyUIExtractionTask"
    TAG_PREDICTION = "TagPredictionTask"

    @staticmethod
    def all():
        return set(item for item in TaskType)
