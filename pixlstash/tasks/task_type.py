from enum import Enum


class TaskType(str, Enum):
    """Identifies each background worker / task-runner lane."""

    FACE_EXTRACTION = "FaceExtractionTask"
    TAGGER = "TagTask"
    QUALITY = "QualityTask"
    LIKENESS = "LikenessTask"
    LIKENESS_PARAMETERS = "LikenessParametersTask"
    DESCRIPTION = "DescriptionTask"
    TEXT_EMBEDDING = "TextEmbeddingTask"
    IMAGE_EMBEDDING = "ImageEmbeddingTask"
    WATCH_FOLDERS = "WatchFolderImportTask"
    COMFYUI_EXTRACTION = "ComfyUIExtractionTask"
    TAG_PREDICTION = "TagPredictionTask"
    SOURCE_FACE_LIKENESS = "SourceFaceLikenessTask"
    MISSING_FILE_PURGE = "MissingFilePurgeTask"
    REFERENCE_FOLDER_SCAN = "ReferenceFolderScanTask"
    SMART_SCORE = "SmartScoreTask"
    TEXT_SCORE = "TextScoreTask"

    @staticmethod
    def all():
        # TAG_PREDICTION is retired as a standalone task type: predictions are
        # now written inline by TagTask.  Exclude it so it never appears in the
        # workers progress response or the frontend task manager.
        return {item for item in TaskType if item is not TaskType.TAG_PREDICTION}
