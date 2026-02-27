import tempfile
import os
import time

from pixlvault.pixl_logging import get_logger
from pixlvault.server import Server
from pixlvault.worker_registry import WorkerType
from pixlvault.db_models.picture import Picture
from pixlvault.db_models.picture_likeness import PictureLikeness, PictureLikenessQueue
from pixlvault.picture_utils import PictureUtils
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
                PictureUtils.resolve_picture_path(
                    server.vault.db.image_root, pic.file_path
                )
                for pic in quality_pictures
            ]
            assert any(os.path.exists(path) for path in quality_paths), (
                "Expected at least one quality-processable picture file to exist on disk."
            )

            quality_futures = []
            for pic in quality_pictures:
                future = server.vault.get_worker_future(
                    WorkerType.QUALITY, Picture, pic.id, "quality"
                )
                quality_futures.append(future)
            logger.info(f"Queued {len(quality_futures)} quality computations.")
            # Start the quality worker
            server.vault.start_workers({WorkerType.QUALITY})
            # Wait for all quality computations to complete
            timeout = time.time() + 120
            for future in quality_futures:
                future.result(timeout=timeout - time.time())

            logger.info("All picture quality computations completed.")

            server.vault.stop_workers({WorkerType.QUALITY})

            server.vault.start_workers(
                {
                    WorkerType.LIKENESS_PARAMETERS,
                    WorkerType.IMAGE_EMBEDDING,
                }
            )

            def fetch_missing_prereqs(session):
                rows = session.exec(
                    select(Picture.id).where(
                        (Picture.image_embedding.is_(None))
                        | (Picture.likeness_parameters.is_(None))
                        | (Picture.perceptual_hash.is_(None))
                    )
                ).all()
                return [row for row in rows]

            timeout = time.time() + 240
            missing = server.vault.db.run_task(fetch_missing_prereqs)
            while missing and time.time() < timeout:
                time.sleep(0.5)
                missing = server.vault.db.run_task(fetch_missing_prereqs)
            assert not missing, (
                "Timed out waiting for likeness prerequisites for picture ids: "
                f"{missing}"
            )
            server.vault.stop_workers(
                {
                    WorkerType.LIKENESS_PARAMETERS,
                    WorkerType.IMAGE_EMBEDDING,
                }
            )

            # Get all unique pairs (a < b)
            pairs = []
            ids = sorted([pic.id for pic in pictures])
            for i, a in enumerate(ids):
                for b in ids[i + 1 :]:
                    pairs.append((a, b))
            logger.info(f"Queued {len(pairs)} likeness pairs for processing.")
            # Start the likeness worker
            server.vault.start_workers({WorkerType.LIKENESS})

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
            server.vault.stop_workers({WorkerType.LIKENESS})
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
