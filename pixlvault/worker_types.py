from enum import Enum


class WorkerType(str, Enum):
    FACE = "FeatureExtractionWorker"
    TAGGER = "TagWorker"
    QUALITY = "QualityWorker"
    FACE_QUALITY = "FaceQualityWorker"
    LIKENESS = "LikenessWorker"
    LIKENESS_PARAMETERS = "LikenessParameterWorker"
    DESCRIPTION = "DescriptionWorker"
    TEXT_EMBEDDING = "EmbeddingWorker"
    IMAGE_EMBEDDING = "ImageEmbeddingWorker"
    WATCH_FOLDERS = "WatchFolderWorker"

    @staticmethod
    def all():
        return set(item for item in WorkerType)
