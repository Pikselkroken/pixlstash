import tempfile
import os
import time

from pixlstash.pixl_logging import get_logger
from pixlstash.server import Server
from pixlstash.tasks.task_type import TaskType
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_likeness import PictureLikeness, PictureLikenessQueue
from pixlstash.utils.image_processing.image_utils import ImageUtils
from sqlalchemy import func
from sqlmodel import select


# Configure logging for the module
logger = get_logger(__name__)


def test_likeness_worker():
    with tempfile.TemporaryDirectory() as temp_dir:
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server_config_path = os.path.join(temp_dir, "server-config.json")
        print("Launching server for likeness worker test...")
        with Server(server_config_path) as server:
            print("Server started for likeness worker test.")
            server.vault.import_default_data(add_tagger_test_images=True)
            # Get all pictures
            pictures = server.vault.db.run_task(
                lambda session: Picture.find(
                    session, select_fields=Picture.metadata_fields()
                )
            )
            assert pictures and len(pictures) >= 2, (
                "No pictures found in the database. Test requires at least two pictures."
            )

            quality_pictures = [pic for pic in pictures if pic.file_path]
            assert quality_pictures, (
                "Expected at least one picture with file_path for quality calculation."
            )
            quality_paths = [
                ImageUtils.resolve_picture_path(
                    server.vault.db.image_root, pic.file_path
                )
                for pic in quality_pictures
            ]
            assert any(os.path.exists(path) for path in quality_paths), (
                "Expected at least one quality-processable picture file to exist on disk."
            )

            # Register futures for every downstream worker BEFORE waiting on
            # quality so we don't miss completions for fast-running pipelines.
            # get_worker_future resolves on success AND on failure (the worker
            # writes a sentinel row and reports the pid as "changed"), so we
            # always get a deterministic completion signal — no polling races.
            embedding_pictures = [pic for pic in pictures if pic.file_path]
            params_pictures = [
                pic for pic in pictures if pic.file_path and pic.width and pic.height
            ]
            quality_futures = {
                pic.id: server.vault.get_worker_future(
                    TaskType.QUALITY, Picture, pic.id, "quality"
                )
                for pic in quality_pictures
            }
            embedding_futures = {
                pic.id: server.vault.get_worker_future(
                    TaskType.IMAGE_EMBEDDING, Picture, pic.id, "image_embedding"
                )
                for pic in embedding_pictures
            }
            params_futures = {
                pic.id: server.vault.get_worker_future(
                    TaskType.LIKENESS_PARAMETERS,
                    Picture,
                    pic.id,
                    "likeness_parameters",
                )
                for pic in params_pictures
            }
            logger.info(
                "Queued futures: quality=%d image_embedding=%d likeness_parameters=%d",
                len(quality_futures),
                len(embedding_futures),
                len(params_futures),
            )

            def _wait_all(label, futures, per_stage_timeout):
                deadline = time.time() + per_stage_timeout
                for pid, future in futures.items():
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        raise AssertionError(
                            f"Timed out waiting for {label} on picture id={pid} "
                            f"(stage timeout={per_stage_timeout}s)."
                        )
                    try:
                        future.result(timeout=remaining)
                    except Exception as exc:
                        raise AssertionError(
                            f"Worker future for {label} on picture id={pid} "
                            f"failed: {exc!r}"
                        ) from exc

            _wait_all("QUALITY", quality_futures, 120)
            logger.info("All quality computations completed.")
            _wait_all("IMAGE_EMBEDDING", embedding_futures, 120)
            logger.info("All image embedding computations completed.")
            _wait_all("LIKENESS_PARAMETERS", params_futures, 120)
            logger.info("All likeness parameter computations completed.")

            # Diagnostic: log which pictures (if any) are still missing one of
            # the three likeness candidate fields.  These are pictures where
            # the embedding task wrote a failure sentinel (e.g. decode error)
            # or where width/height were never set; they are legitimately
            # excluded from the likeness candidate pool by
            # LikenessTask.count_total_candidates.
            def fetch_missing_prereqs(session):
                rows = session.exec(
                    select(
                        Picture.id,
                        Picture.image_embedding,
                        Picture.likeness_parameters,
                        Picture.perceptual_hash,
                    ).where(
                        (Picture.image_embedding.is_(None))
                        | (func.length(Picture.image_embedding) == 0)
                        | (Picture.likeness_parameters.is_(None))
                        | (Picture.perceptual_hash.is_(None))
                    )
                ).all()
                return [
                    {
                        "id": int(r[0]),
                        "image_embedding_missing": r[1] is None or len(r[1]) == 0,
                        "likeness_parameters_missing": r[2] is None,
                        "perceptual_hash_missing": r[3] is None,
                    }
                    for r in rows
                ]

            missing = server.vault.db.run_task(fetch_missing_prereqs)
            if missing:
                logger.warning(
                    "%d picture(s) excluded from likeness candidate pool "
                    "(embedding/parameter pipeline reported them as done but "
                    "left fields unset): %s",
                    len(missing),
                    missing,
                )
            # Get all unique pairs (a < b)
            pairs = []
            ids = sorted([pic.id for pic in pictures])
            for i, a in enumerate(ids):
                for b in ids[i + 1 :]:
                    pairs.append((a, b))
            logger.info(f"Queued {len(pairs)} likeness pairs for processing.")
            # Start the likeness worker

            def fetch_queue_remaining(session):
                return session.exec(
                    select(func.count()).select_from(PictureLikenessQueue)
                ).one()

            timeout = time.time() + 120
            remaining = server.vault.db.run_task(fetch_queue_remaining)
            while remaining and time.time() < timeout:
                time.sleep(0.5)
                remaining = server.vault.db.run_task(fetch_queue_remaining)
            assert not remaining, (
                f"Timed out waiting for likeness queue to drain. Remaining={remaining}"
            )
            # Check that all likeness results are present
            likeness_results = server.vault.db.run_task(
                lambda session: PictureLikeness.find(session)
            )
            result_pairs = set(
                (r.picture_id_a, r.picture_id_b) for r in likeness_results
            )
            if not likeness_results:
                logger.warning(
                    "No likeness results were produced; gating may have filtered all pairs."
                )
            for a, b in result_pairs:
                assert (a, b) in pairs, f"Unexpected likeness pair produced: ({a}, {b})"

            # Print table of likeness scores with descriptions
            pic_map = {pic.id: pic for pic in pictures}
            logger.info("\nLikeness Table:")
            logger.info(f"{'Desc A':<30} | {'Desc B':<30} | {'Likeness':<10}")
            logger.info("-" * 110)
            for r in likeness_results:
                pic_a = pic_map.get(r.picture_id_a)
                pic_b = pic_map.get(r.picture_id_b)
                desc_a = (pic_a.description or "") if pic_a else "?"
                desc_b = (pic_b.description or "") if pic_b else "?"
                logger.info(f"{desc_a:<30} | {desc_b:<30} | {r.likeness:<10.4f}")
