import os
import tempfile
import insightface

from time import time

from pixlvault.tasks.feature_extraction_task import FeatureExtractionTask
from pixlvault.db_models.picture import Picture
from pixlvault.pixl_logging import get_logger
from pixlvault.utils.image_processing.image_utils import ImageUtils
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
            image_root = server.vault.image_root
            os.makedirs(image_root, exist_ok=True)

            def add_picture(session, picture: Picture):
                session.add(picture)
                session.commit()
                session.refresh(picture)
                return picture

            for image_file in image_files:
                pic = ImageUtils.create_picture_from_file(
                    image_root_path=image_root,
                    source_file_path=os.path.join(src_dir, image_file),
                )
                pic = server.vault.db.run_task(add_picture, pic)
                pictures.append(pic)

            def notify(event_type) -> None:
                pass

            task = FeatureExtractionTask(server.vault.db, None, pictures)
            task._insightface_app = insightface.app.FaceAnalysis()
            task._insightface_app.prepare(ctx_id=-1, det_thresh=0.25)

            start = time()
            features = task._extract_features(pictures)
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
            image_root = server.vault.image_root
            os.makedirs(image_root, exist_ok=True)

            def add_picture(session, picture: Picture):
                session.add(picture)
                session.commit()
                session.refresh(picture)
                return picture

            for image_file in image_files:
                pic = ImageUtils.create_picture_from_file(
                    image_root_path=image_root,
                    source_file_path=os.path.join(src_dir, image_file),
                )
                pic = server.vault.db.run_task(add_picture, pic)
                pictures.append(pic)

            def notify(event_type) -> None:
                pass

            task = FeatureExtractionTask(server.vault.db, None, pictures)
            # task._insightface_app = insightface.model_zoo.get_model('buffalo_l.onnx')
            task._insightface_app = insightface.app.FaceAnalysis(
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
            )
            task._insightface_app.prepare(
                ctx_id=0, det_thresh=0.25, det_size=(480, 480)
            )

            start = time()
            features = task._extract_features(pictures)
            end = time()
            logger.info(
                f"Face extraction took {end - start} seconds for {len(pictures)} images and created {len(features)} features. Or {(end - start) / len(pictures)} seconds per image on average."
            )
