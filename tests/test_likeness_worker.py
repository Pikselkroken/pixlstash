import json
import tempfile
import os
import time

from pixlvault.pixl_logging import get_logger
from pixlvault.server import Server
from pixlvault.worker_registry import WorkerType
from pixlvault.db_models.picture import Picture
from pixlvault.db_models.picture_likeness import PictureLikeness


# Configure logging for the module
logger = get_logger(__name__)


def test_likeness_worker():
    with tempfile.TemporaryDirectory() as temp_dir:
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        config_path = os.path.join(temp_dir, "config.json")
        config = Server.create_config(
            default_device="cpu",
            image_roots=[image_root],
            selected_image_root=image_root,
        )
        with open(config_path, "w") as f:
            f.write(json.dumps(config, indent=2))
        server_config_path = os.path.join(temp_dir, "server-config.json")
        print("Launching server for likeness worker test...")
        with Server(config_path, server_config_path) as server:
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
            quality_futures = []
            for pic in pictures:
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

            # Get all unique pairs (a < b)
            pairs = []
            ids = sorted([pic.id for pic in pictures])
            for i, a in enumerate(ids):
                for b in ids[i + 1 :]:
                    pairs.append((a, b))
            # Get future objects for each pair
            futures = {}
            for a, b in pairs:
                future = server.vault.get_worker_future(
                    WorkerType.LIKENESS, PictureLikeness, (a, b), "pair"
                )
                futures[(a, b)] = future

            logger.info(f"Queued {len(futures)} likeness pairs for processing.")
            # Start the likeness worker
            server.vault.start_workers({WorkerType.LIKENESS})

            # Wait for all futures to complete
            timeout = time.time() + 60
            for key, future in futures.items():
                key, result = future.result(timeout=timeout - time.time())
                assert result >= 0.0, f"Likeness score for pair {key} is negative."
            server.vault.stop_workers({WorkerType.LIKENESS})
            # Check that all likeness results are present
            likeness_results = server.vault.db.run_task(
                lambda session: PictureLikeness.find(session)
            )
            result_pairs = set(
                (r.picture_id_a, r.picture_id_b) for r in likeness_results
            )
            assert len(likeness_results) == len(pairs), (
                "Not all likeness pairs were processed"
            )
            for a, b in pairs:
                assert (a, b) in result_pairs

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
