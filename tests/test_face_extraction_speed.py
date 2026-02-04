import os
import tempfile
import insightface

from time import time

from pixlvault.feature_extraction_worker import FeatureExtractionWorker
from pixlvault.db_models.picture import Picture
from pixlvault.pixl_logging import get_logger
from pixlvault.server import Server

logger = get_logger(__name__)


def test_face_extraction_speed_cpu():
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")

        src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
        image_files = [
            f
            for f in os.listdir(src_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]
        # Duplicate images to increase the number of pictures
        image_files = image_files * 2  # Adjust multiplier as needed for testing

        with Server(server_config_path=server_config_path) as server:
            pictures = []
            for image_file in image_files:
                pic = Picture(file_path=os.path.join(src_dir, image_file))
                pictures.append(pic)

            def notify(event_type) -> None:
                pass

            worker = FeatureExtractionWorker(server.vault.db, None, notify)
            worker._insightface_app = insightface.app.FaceAnalysis()
            worker._insightface_app.prepare(ctx_id=-1, det_thresh=0.25)

            start = time()
            features = worker._extract_features(pictures)
            end = time()
            logger.info(
                f"Face extraction took {end - start} seconds for {len(pictures)} images and created {len(features)} features. Or {(end - start) / len(pictures)} seconds per image on average."
            )


def test_face_extraction_speed_gpu():
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")

        src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
        original_image_files = [
            f
            for f in os.listdir(src_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]

        image_files = (
            original_image_files * 10
        )  # Adjust multiplier as needed for testing

        with Server(server_config_path=server_config_path) as server:
            pictures = []
            for image_file in image_files:
                pic = Picture(file_path=os.path.join(src_dir, image_file))
                pictures.append(pic)

            def notify(event_type) -> None:
                pass

            worker = FeatureExtractionWorker(server.vault.db, None, notify)
            # worker._insightface_app = insightface.model_zoo.get_model('buffalo_l.onnx')
            worker._insightface_app = insightface.app.FaceAnalysis(
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
            )
            worker._insightface_app.prepare(
                ctx_id=0, det_thresh=0.25, det_size=(480, 480)
            )

            start = time()
            features = worker._extract_features(pictures)
            end = time()
            logger.info(
                f"Face extraction took {end - start} seconds for {len(pictures)} images and created {len(features)} features. Or {(end - start) / len(pictures)} seconds per image on average."
            )
