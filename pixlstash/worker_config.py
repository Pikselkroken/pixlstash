"""Central configuration for background-worker concurrency.

Tune these constants to match your hardware:

- *_MAX_INFLIGHT: how many tasks of that type may be queued/running at once.
  Includes tasks that are preloading data and not yet executing on a worker
  thread.  Should be ≥ NUM_WORKERS to keep workers busy without excessive
  memory pressure.

- NUM_WORKERS: number of worker threads in the TaskRunner.  Each thread
  executes one task at a time.  Set to the number of CPU cores you are happy
  to dedicate to background processing.

- *_BATCH_SIZE and *_FETCH_LIMIT are in their respective task/finder files.
"""

# ── TaskRunner ────────────────────────────────────────────────────────────────
NUM_WORKERS: int = 4

# ── Per-task-type inflight caps ───────────────────────────────────────────────
# Quality: one task computes while up to two others preload ahead.  Three
# in-flight provides enough look-ahead that even slow-preload batches (large
# JPEGs) keep preload_wait ≈ 0 by the time compute reaches them.  Preload
# uses PIL draft() for JPEG subsampling so disk load is manageable.
QUALITY_MAX_INFLIGHT: int = 3

# Text score: MSER-based, single-threaded per image, low-priority.
TEXT_SCORE_MAX_INFLIGHT: int = 2

# Smart score: model inference, GPU if available.
SMART_SCORE_MAX_INFLIGHT: int = 2

# Tagger: WD14 / custom model inference.
# 3 inflight ensures the preloader always has 2 full GPU cycles (~0.54s) to
# complete before its task reaches the semaphore, eliminating preload stalls.
TAGGER_MAX_INFLIGHT: int = 3

# Image embeddings: CLIP ViT-B-32.  Tasks preload images from disk while the
# previous task runs inference, so 2 inflight (1 running + 1 preloading) keeps
# the GPU continuously fed.
IMAGE_EMBEDDING_MAX_INFLIGHT: int = 3

# Image tasks (Tags, image embeddings) also cut a batch by its decoded size:
# its summed recorded width x height stays under this budget (#2750). A task's
# preload memory grows with picture size, so 64 x 24 MP photos cost far more
# than 64 generated images. 64 x 12 MP: batches of typical photos and of
# generated images are cut by count alone; 24 MP photos run 32 to a batch,
# 48 MP ones 16. A picture with no recorded dimensions counts as a large photo.
IMAGE_TASK_PIXEL_BUDGET: int = 64 * 12_000_000
UNKNOWN_PICTURE_PIXELS: int = 24_000_000

# Likeness parameters: fast enough with a single inflight task after batching fix.
LIKENESS_PARAMETERS_MAX_INFLIGHT: int = 2
