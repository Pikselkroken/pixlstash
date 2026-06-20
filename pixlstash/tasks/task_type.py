from enum import Enum


class TaskType(str, Enum):
    """Identifies each background worker / task-runner lane."""

    FACE_EXTRACTION = "FaceExtractionTask"
    FACE_MODEL_REFRESH = "FaceModelRefreshTask"
    TAGGER = "TagTask"
    TAG_PREDICTION_BACKFILL = "TagPredictionBackfillTask"
    QUALITY = "QualityTask"
    LIKENESS = "LikenessTask"
    LIKENESS_PARAMETERS = "LikenessParametersTask"
    DESCRIPTION = "DescriptionTask"
    TEXT_EMBEDDING = "TextEmbeddingTask"
    IMAGE_EMBEDDING = "ImageEmbeddingTask"
    WATCH_FOLDERS = "WatchFolderImportTask"
    COMFYUI_EXTRACTION = "ComfyUIExtractionTask"
    SOURCE_FACE_LIKENESS = "SourceFaceLikenessTask"
    MISSING_FILE_PURGE = "MissingFilePurgeTask"
    REFERENCE_FOLDER_SCAN = "ReferenceFolderScanTask"
    SMART_SCORE = "SmartScoreTask"
    TEXT_SCORE = "TextScoreTask"
    GFS_SNAPSHOT = "EnsureGfsSnapshotTask"

    @staticmethod
    def all():
        return set(TaskType)
