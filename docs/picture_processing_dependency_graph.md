# Picture Processing Dependency Graph

This graph captures **all per-picture work** in the current backend and its dependencies.

## Current Pipeline DAG (as implemented)

```mermaid
flowchart TD
    A[Picture Ingested<br/>Upload / Watch Folder / Plugin / ComfyUI] --> B[Picture row persisted<br/>file_path, width/height, pixel_sha]

    %% Core per-picture workers
    B --> C[FeatureExtractionWorker<br/>Faces + Hands]
    B --> D[TagWorker<br/>Picture]
    B --> E[DescriptionWorker<br/>Generate caption/description]
    B --> F[ImageEmbeddingWorker<br/>image_embedding + aesthetic_score + perceptual_hash]
    B --> G[QualityWorker<br/>Full-image quality]
    C --> D[TagWorker<br/>Face / Hand Tags]
    C --> H[FaceQualityWorker<br/>Face quality]

    %% Text embedding path
    E --> I[EmbeddingWorker<br/>text_embedding]

    %% Likeness path
    G --> J[LikenessParameterWorker<br/>quality-derived params]
    F --> J
    B --> J
    J --> K[LikenessWorker<br/>pairwise likeness edges]
    F --> K
```

## Dependency Notes

- `FeatureExtractionWorker` runs when a picture is missing faces or hands.
- `FaceQualityWorker` requires detected faces with bounding boxes.
- `TagWorker` can tag pictures directly and can also backfill missing face/hand tags after detection.
- `DescriptionWorker` only needs a picture; `EmbeddingWorker` needs `description` first.
- `ImageEmbeddingWorker` is prerequisite for smart score and likeness.
- `QualityWorker` is prerequisite for quality-derived likeness parameters and can feed smart score fallback.
- `LikenessParameterWorker` requires picture metadata and quality/image-derived inputs; it enqueues pictures for `LikenessWorker`.
- `LikenessWorker` requires: `image_embedding`, `likeness_parameters`, and `perceptual_hash`.
- `SmartScoreScrapheapWorker` currently gates on: `imported_at is null`, non-empty tags, embedding, and `(aesthetic_score or quality)`.

## Event Triggers (high level)

```mermaid
flowchart LR
    CP[CHANGED_PICTURES] --> W1[FeatureExtractionWorker]
    CP --> W2[TagWorker]
    CP --> W3[QualityWorker]
    CP --> W4[DescriptionWorker]
    CP --> W5[ImageEmbeddingWorker]
    CP --> W6[LikenessParameterWorker]
    CP --> W7[SmartScoreScrapheapWorker]

    CF[CHANGED_FACES] --> W8[FaceQualityWorker]
    CF --> W2

    CT[CHANGED_TAGS] --> W7
    CD[CHANGED_DESCRIPTIONS] --> W9[EmbeddingWorker]
    QU[QUALITY_UPDATED] --> W10[LikenessWorker]
    QU --> W6
    QU --> W7
```