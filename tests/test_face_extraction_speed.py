import os
import tempfile
import insightface

from PIL import Image, ImageDraw
from time import time
from fastapi.testclient import TestClient

from pixlvault.face_extraction_worker import FaceExtractionWorker
from pixlvault.db_models.picture import Picture
from pixlvault.pixl_logging import get_logger
from pixlvault.server import Server
from pixlvault.worker_registry import WorkerType

logger = get_logger(__name__)


def test_face_extraction_speed_cpu():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server_config.json")

        src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
        image_files = [
            f
            for f in os.listdir(src_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]
        # Duplicate images to increase the number of pictures
        image_files = image_files * 2  # Adjust multiplier as needed for testing

        with Server(
            config_path=config_path,
            server_config_path=server_config_path,
        ) as server:
            pictures = []
            for image_file in image_files:
                pic = Picture(file_path=os.path.join(src_dir, image_file))
                pictures.append(pic)

            def notify(event_type) -> None:
                pass

            worker = FaceExtractionWorker(server.vault.db, None, notify)
            worker._insightface_app = insightface.app.FaceAnalysis()
            worker._insightface_app.prepare(ctx_id=-1, det_thresh=0.25)

            start = time()
            faces = worker._extract_faces(pictures)
            end = time()
            logger.info(
                f"Face extraction took {end - start} seconds for {len(pictures)} images and created {len(faces)} faces. Or {(end - start) / len(pictures)} seconds per image on average."
            )


def test_face_extraction_speed_gpu():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
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

        with Server(
            config_path=config_path,
            server_config_path=server_config_path,
        ) as server:
            pictures = []
            for image_file in image_files:
                pic = Picture(file_path=os.path.join(src_dir, image_file))
                pictures.append(pic)

            def notify(event_type) -> None:
                pass

            worker = FaceExtractionWorker(server.vault.db, None, notify)
            # worker._insightface_app = insightface.model_zoo.get_model('buffalo_l.onnx')
            worker._insightface_app = insightface.app.FaceAnalysis(
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
            )
            worker._insightface_app.prepare(
                ctx_id=0, det_thresh=0.25, det_size=(480, 480)
            )

            start = time()
            faces = worker._extract_faces(pictures)
            end = time()
            logger.info(
                f"Face extraction took {end - start} seconds for {len(pictures)} images and created {len(faces)} faces. Or {(end - start) / len(pictures)} seconds per image on average."
            )


def test_face_extraction_speed_server():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server_config.json")

        src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
        original_image_files = [
            f
            for f in os.listdir(src_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]
        # Duplicate images to increase the number of pictures

        def slightly_modify_and_save_temp(img, idx=0):
            draw = ImageDraw.Draw(img)
            # Draw a small dot in a unique position
            x, y = 5 + idx, 5 + idx
            draw.ellipse((x, y, x + 3, y + 3), fill=(255, 0, 0))
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
                img.save(temp_file, format="PNG")
                return temp_file.name

        tmp_files = []
        image_files = []
        for file in original_image_files:
            img = Image.open(os.path.join(src_dir, file)).convert("RGB")

            for idx in range(3):  # Adjust multiplier as needed for testing
                tmp_file = slightly_modify_and_save_temp(img.copy(), idx)
                image_files.append(tmp_file)
                tmp_files.append(tmp_file)

        with Server(
            config_path=config_path,
            server_config_path=server_config_path,
        ) as server:
            # Upload all images as new pictures
            client = TestClient(server.api)
            picture_ids = []
            face_futures = []
            id_to_filename = {}
            server.vault.initialise_worker_if_necessary(WorkerType.FACE)

            files = []
            for fname in image_files:
                with open(fname, "rb") as f:
                    files.append(
                        ("file", (os.path.basename(fname), f.read(), "image/png"))
                    )
            r = client.post("/pictures", files=files)
            assert r.status_code == 200
            resp = r.json()

            for result in resp["results"]:
                assert result["status"] == "success"
                id_to_filename[result["picture_id"]] = fname
                picture_ids.append(result["picture_id"])
                face_futures.append(
                    server.vault.get_worker_future(
                        WorkerType.FACE, Picture, picture_ids[-1], "faces"
                    )
                )

            start = time()
            server.vault.start_workers({WorkerType.FACE})

            for i, future in enumerate(face_futures):
                future.result(timeout=120)
            end = time()
            logger.info(
                f"Face extraction took {end - start} seconds for {len(picture_ids)} images. Or {(end - start) / len(picture_ids)} seconds per image on average."
            )

            server.vault.stop_workers({WorkerType.FACE})
