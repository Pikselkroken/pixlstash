from .task_type import TaskType
from .base_task_finder import BaseTaskFinder, TaskFinderRegistry
from .description_task import DescriptionTask
from .face_extraction_task import FaceExtractionTask
from .face_model_refresh_task import FaceModelRefreshTask
from .quality_task import QualityTask
from .image_embedding_task import ImageEmbeddingTask
from .missing_face_extraction_finder import MissingFaceExtractionFinder
from .missing_face_model_refresh_finder import MissingFaceModelRefreshFinder
from .missing_image_embedding_finder import MissingImageEmbeddingFinder
from .missing_likeness_finder import MissingLikenessFinder
from .missing_likeness_parameters_finder import MissingLikenessParametersFinder
from .missing_description_finder import MissingDescriptionFinder
from .missing_quality_finder import MissingQualityFinder
from .missing_text_embedding_finder import MissingTextEmbeddingFinder
from .missing_tag_finder import MissingTagFinder
from .missing_tag_prediction_finder import MissingTagPredictionFinder
from .tag_prediction_backfill_task import TagPredictionBackfillTask
from .missing_watch_folder_import_finder import MissingWatchFolderImportFinder
from .missing_comfyui_extraction_finder import MissingComfyUIExtractionFinder
from .comfyui_extraction_task import ComfyUIExtractionTask
from .picture_import_task import PictureImportTask
from .tag_task import TagTask
from .text_embedding_task import TextEmbeddingTask
from .likeness_parameters_task import LikenessParametersTask
from .likeness_task import LikenessTask
from .source_face_likeness_task import SourceFaceLikenessTask
from .missing_source_face_likeness_finder import (
    MissingSourceFaceLikenessCharacterFinder,
)
from .reference_folder_scan_task import ReferenceFolderScanTask
from .reference_folder_scan_finder import ReferenceFolderScanFinder
from .text_score_task import TextScoreTask
from .missing_text_score_finder import MissingTextScoreFinder
from .ensure_gfs_snapshot_finder import EnsureGfsSnapshotFinder
from .ensure_gfs_snapshot_task import EnsureGfsSnapshotTask
from .tag_health_auto_rebuild_task import TagHealthAutoRebuildTask
from .tag_health_auto_rebuild_finder import TagHealthAutoRebuildFinder
from .scrapheap_retention_purge_task import ScrapheapRetentionPurgeTask
from .scrapheap_retention_purge_finder import ScrapheapRetentionPurgeFinder
from .thumbnail_generation_task import ThumbnailGenerationTask
from .missing_thumbnail_finder import MissingThumbnailFinder
from .pixel_sha_task import PixelShaTask
from .missing_pixel_sha_finder import MissingPixelShaFinder
from .dedup_scan_task import DedupScanTask
from .dedup_scan_finder import DedupScanFinder

__all__ = [
    "TaskType",
    "BaseTaskFinder",
    "TaskFinderRegistry",
    "DescriptionTask",
    "FaceExtractionTask",
    "FaceModelRefreshTask",
    "ImageEmbeddingTask",
    "QualityTask",
    "MissingFaceExtractionFinder",
    "MissingFaceModelRefreshFinder",
    "MissingImageEmbeddingFinder",
    "MissingLikenessFinder",
    "MissingLikenessParametersFinder",
    "MissingDescriptionFinder",
    "MissingQualityFinder",
    "MissingTextEmbeddingFinder",
    "MissingTagFinder",
    "MissingTagPredictionFinder",
    "TagPredictionBackfillTask",
    "MissingWatchFolderImportFinder",
    "MissingComfyUIExtractionFinder",
    "ComfyUIExtractionTask",
    "PictureImportTask",
    "TagTask",
    "TextEmbeddingTask",
    "LikenessParametersTask",
    "LikenessTask",
    "SourceFaceLikenessTask",
    "MissingSourceFaceLikenessCharacterFinder",
    "ReferenceFolderScanTask",
    "ReferenceFolderScanFinder",
    "TextScoreTask",
    "MissingTextScoreFinder",
    "EnsureGfsSnapshotFinder",
    "EnsureGfsSnapshotTask",
    "TagHealthAutoRebuildTask",
    "TagHealthAutoRebuildFinder",
    "ScrapheapRetentionPurgeTask",
    "ScrapheapRetentionPurgeFinder",
    "ThumbnailGenerationTask",
    "MissingThumbnailFinder",
    "PixelShaTask",
    "MissingPixelShaFinder",
    "DedupScanTask",
    "DedupScanFinder",
]
