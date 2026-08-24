from enum import Enum


class TaskType(str, Enum):
    """Identifies each background worker / task-runner lane."""

    FACE_EXTRACTION = "FaceExtractionTask"
    FACE_MODEL_REFRESH = "FaceModelRefreshTask"
    DETECTION = "DetectionTask"
    TAGGER = "TagTask"
    TAG_PREDICTION_BACKFILL = "TagPredictionBackfillTask"
    QUALITY = "QualityTask"
    LIKENESS = "LikenessTask"
    LIKENESS_PARAMETERS = "LikenessParametersTask"
    DESCRIPTION = "DescriptionTask"
    TEXT_EMBEDDING = "TextEmbeddingTask"
    IMAGE_EMBEDDING = "ImageEmbeddingTask"
    WATCH_FOLDERS = "WatchFolderImportTask"
    PICTURE_IMPORT = "PictureImportTask"
    COMFYUI_EXTRACTION = "ComfyUIExtractionTask"
    SOURCE_FACE_LIKENESS = "SourceFaceLikenessTask"
    MISSING_FILE_PURGE = "MissingFilePurgeTask"
    REFERENCE_FOLDER_SCAN = "ReferenceFolderScanTask"
    SMART_SCORE = "SmartScoreTask"
    TEXT_SCORE = "TextScoreTask"
    GFS_SNAPSHOT = "EnsureGfsSnapshotTask"
    TAG_HEALTH_AUTO_REBUILD = "TagHealthAutoRebuildTask"
    SCRAPHEAP_RETENTION_PURGE = "ScrapheapRetentionPurgeTask"
    PENDING_SCORE_INVALIDATION = "PendingScoreInvalidationTask"
    THUMBNAIL_GENERATION = "ThumbnailGenerationTask"
    PIXEL_SHA = "PixelShaTask"
    ORIENTATION = "OrientationTask"
    DEDUP_SCAN = "DedupScanTask"
    STACK_COHESION = "StackCohesionTask"
    SNAPSHOT_IDENTITY_SCRUB = "SnapshotIdentityScrubTask"
    CHECKPOINT_HASH = "CheckpointHashTask"
    MODEL_FOLDER_SCAN = "ModelFolderScanTask"
    LAYOUT_MOVE = "LayoutMoveTask"

    @staticmethod
    def all():
        return set(TaskType)
