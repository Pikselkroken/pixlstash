import os
import sys
import tempfile
import numpy as np

from datetime import datetime
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models.picture_set import PictureSet, PictureSetMember
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.server import Server

API_PREFIX = "/api/v1"


def _make_png_bytes(color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (24, 24), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_picture_plugins_list_and_run_colour_filter():
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            login_resp = client.post(
                f"{API_PREFIX}/login",
                json={"username": "testuser", "password": "testpassword"},
            )
            assert login_resp.status_code == 200

            img_a = _make_png_bytes((255, 40, 40))
            img_b = _make_png_bytes((40, 255, 40))

            def add_pictures(session: Session):
                first = ImageUtils.create_picture_from_bytes(
                    image_root_path=server.vault.image_root,
                    image_bytes=img_a,
                )
                second = ImageUtils.create_picture_from_bytes(
                    image_root_path=server.vault.image_root,
                    image_bytes=img_b,
                )
                now = datetime.utcnow()
                first.imported_at = now
                second.imported_at = now
                session.add(first)
                session.add(second)
                session.commit()
                session.refresh(first)
                session.refresh(second)
                picture_set = PictureSet(name="Plugin Test Set", description="test")
                session.add(picture_set)
                session.commit()
                session.refresh(picture_set)
                session.add(
                    PictureSetMember(set_id=picture_set.id, picture_id=first.id)
                )
                session.add(
                    PictureSetMember(set_id=picture_set.id, picture_id=second.id)
                )
                session.commit()
                return [first.id, second.id, picture_set.id]

            inserted = server.vault.db.run_task(add_pictures)
            inserted_ids = inserted[:2]
            created_set_id = inserted[2]
            assert len(inserted_ids) == 2

            pictures_resp = client.get(f"{API_PREFIX}/pictures?fields=grid")
            assert pictures_resp.status_code == 200
            pictures = pictures_resp.json()
            assert pictures and len(pictures) >= 2
            selected_ids = sorted(inserted_ids)

            plugins_resp = client.get(f"{API_PREFIX}/pictures/plugins")
            assert plugins_resp.status_code == 200
            plugins_payload = plugins_resp.json()
            plugins = plugins_payload.get("plugins") or []
            names = {plugin.get("name") for plugin in plugins}
            assert "colour_filter" in names
            assert "scaling" in names
            assert "brightness_contrast" in names
            assert "blur_sharpen" in names
            colour_schema = next(
                (plugin for plugin in plugins if plugin.get("name") == "colour_filter"),
                None,
            )
            assert colour_schema is not None
            assert colour_schema.get("supports_images") is True
            assert colour_schema.get("supports_videos") is True
            brightness_contrast_schema = next(
                (
                    plugin
                    for plugin in plugins
                    if plugin.get("name") == "brightness_contrast"
                ),
                None,
            )
            assert brightness_contrast_schema is not None
            assert brightness_contrast_schema.get("supports_images") is True
            assert brightness_contrast_schema.get("supports_videos") is True
            blur_sharpen_schema = next(
                (plugin for plugin in plugins if plugin.get("name") == "blur_sharpen"),
                None,
            )
            assert blur_sharpen_schema is not None
            assert blur_sharpen_schema.get("supports_images") is True
            assert blur_sharpen_schema.get("supports_videos") is True

            run_resp = client.post(
                f"{API_PREFIX}/pictures/plugins/colour_filter",
                json={
                    "picture_ids": selected_ids,
                    "parameters": {"mode": "sepia"},
                },
            )
            assert run_resp.status_code == 200, run_resp.text
            run_payload = run_resp.json()
            assert run_payload.get("status") == "success"
            created_ids = run_payload.get("created_picture_ids") or []
            assert len(created_ids) == 2

            def fetch_set_members(session: Session, set_id: int):
                members = session.exec(
                    select(PictureSetMember).where(PictureSetMember.set_id == set_id)
                ).all()
                return {int(member.picture_id) for member in members}

            set_member_ids = server.vault.db.run_task(fetch_set_members, created_set_id)
            for created_id in created_ids:
                assert int(created_id) in set_member_ids

            # Fetch pictures individually to avoid stack_leaders_only filtering
            # (fields=grid hides non-leader stack members, which includes the
            # original source pictures after the plugin pushes them to position 1).
            for source_id, created_id in zip(selected_ids, created_ids):
                source_resp = client.get(f"{API_PREFIX}/pictures/{source_id}/metadata")
                assert source_resp.status_code == 200, source_resp.text
                source = source_resp.json()
                created_resp = client.get(
                    f"{API_PREFIX}/pictures/{created_id}/metadata"
                )
                assert created_resp.status_code == 200, created_resp.text
                created = created_resp.json()
                assert source.get("stack_id") is not None
                assert created.get("stack_id") == source.get("stack_id")
                assert int(created.get("stack_position")) == 0

            scale_resp = client.post(
                f"{API_PREFIX}/pictures/plugins/scaling",
                json={
                    "picture_ids": selected_ids,
                    "parameters": {
                        "algorithm": "lanczos",
                        "scale_factor": "2.0",
                    },
                },
            )
            assert scale_resp.status_code == 200, scale_resp.text
            scale_payload = scale_resp.json()
            assert scale_payload.get("status") == "success"
            scaled_ids = scale_payload.get("created_picture_ids") or []
            assert len(scaled_ids) == 2

            for source_id, scaled_id in zip(selected_ids, scaled_ids):
                source_resp = client.get(f"{API_PREFIX}/pictures/{source_id}/metadata")
                assert source_resp.status_code == 200, source_resp.text
                source = source_resp.json()
                scaled_resp = client.get(f"{API_PREFIX}/pictures/{scaled_id}/metadata")
                assert scaled_resp.status_code == 200, scaled_resp.text
                scaled = scaled_resp.json()
                assert int(scaled.get("width")) == int(source.get("width")) * 2
                assert int(scaled.get("height")) == int(source.get("height")) * 2

            brightness_resp = client.post(
                f"{API_PREFIX}/pictures/plugins/brightness_contrast",
                json={
                    "picture_ids": selected_ids,
                    "parameters": {
                        "brightness": 1.1,
                        "contrast": 1.2,
                    },
                },
            )
            assert brightness_resp.status_code == 200, brightness_resp.text
            brightness_payload = brightness_resp.json()
            assert brightness_payload.get("status") == "success"
            brightness_ids = brightness_payload.get("created_picture_ids") or []
            assert len(brightness_ids) == 2

            blur_resp = client.post(
                f"{API_PREFIX}/pictures/plugins/blur_sharpen",
                json={
                    "picture_ids": selected_ids,
                    "parameters": {
                        "mode": "blur",
                        "strength": 1.0,
                    },
                },
            )
            assert blur_resp.status_code == 200, blur_resp.text
            blur_payload = blur_resp.json()
            assert blur_payload.get("status") == "success"
            blur_output_ids = blur_payload.get("output_picture_ids") or []
            assert len(blur_output_ids) == 2


def test_create_picture_from_bytes_preserves_video_extension_format(monkeypatch):
    class _FakeVideoCapture:
        def __init__(self, _path):
            self._read = False

        def read(self):
            if self._read:
                return False, None
            self._read = True
            frame = np.zeros((32, 48, 3), dtype=np.uint8)
            return True, frame

        def release(self):
            return None

    monkeypatch.setattr(
        "pixlstash.utils.image_processing.video_utils.cv2.VideoCapture",
        _FakeVideoCapture,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        picture = ImageUtils.create_picture_from_bytes(
            image_root_path=temp_dir,
            image_bytes=b"not-an-image",
            picture_uuid="example.webm",
        )

        assert picture.format == "WEBM"
        assert picture.file_path is not None
        assert picture.file_path.endswith(".webm")


def _write_test_video(path: str, width: int, height: int, frames: int) -> None:
    """Write a small solid-colour video, or skip the test if no encoder is available."""
    import cv2
    import pytest

    writer = cv2.VideoWriter(
        path, cv2.VideoWriter_fourcc(*"mp4v"), 12.0, (width, height)
    )
    if not writer.isOpened():
        pytest.skip("no OpenCV video encoder available in this environment")
    for index in range(frames):
        frame = np.full((height, width, 3), (index * 20) % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def _run_video_plugin(plugin, source_path: str, params: dict):
    """Run *plugin* over *source_path* and return ``(width, height, frames, progress)``."""
    import cv2

    progress: list = []
    data, ext = plugin.run_video(source_path, params, progress_callback=progress.append)
    out_path = source_path + ".out" + ext
    with open(out_path, "wb") as handle:
        handle.write(data)
    try:
        cap = cv2.VideoCapture(out_path)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        decoded = 0
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            decoded += 1
        cap.release()
    finally:
        os.remove(out_path)
    return width, height, decoded, progress


def test_video_plugins_reencode_every_frame_and_size_output_from_the_transform():
    """The shared ``ImagePlugin.transform_video`` pipeline must preserve frame
    count and take its output dimensions from the transformed frame.

    Rotation is the case that matters: a 90° turn swaps width and height, and a
    writer opened at the *source* size would silently drop every frame. Covers
    all four video-capable built-ins through one pipeline.
    """
    from pixlstash.image_plugins.registry import get_image_plugin_manager

    manager = get_image_plugin_manager()

    width, height, frames = 64, 48, 6
    cases = [
        ("blur_sharpen", {"mode": "blur", "strength": 1.5}, (width, height)),
        ("brightness_contrast", {"brightness": 1.2, "contrast": 1.1}, (width, height)),
        ("colour_filter", {"mode": "black_and_white"}, (width, height)),
        # 90° rotation swaps the output dimensions.
        ("rotate", {"direction": "90_right"}, (height, width)),
        ("rotate", {"direction": "180"}, (width, height)),
    ]

    with tempfile.TemporaryDirectory() as temp_dir:
        source = os.path.join(temp_dir, "source.mp4")
        _write_test_video(source, width, height, frames)

        for name, params, expected_size in cases:
            plugin = manager.get_plugin(name)
            assert plugin is not None, f"built-in plugin {name} not registered"
            assert plugin.supports_videos is True

            got_w, got_h, decoded, progress = _run_video_plugin(plugin, source, params)

            assert (got_w, got_h) == expected_size, (
                f"{name} {params}: output {got_w}x{got_h}, expected {expected_size}"
            )
            assert decoded == frames, (
                f"{name} {params}: {decoded} frames, want {frames}"
            )
            assert len(progress) == frames, (
                f"{name} {params}: {len(progress)} progress events, want {frames}"
            )


# ----------------------------------------------------------------------
# Embedded metadata carried from the source file onto the plugin output
# ----------------------------------------------------------------------


def _rotate_output_bytes(source_path: str, source_format: str) -> bytes:
    """Run the built-in rotate plugin and save it exactly as the service does."""
    from pixlstash.image_plugins.registry import get_image_plugin_manager
    from pixlstash.image_plugins.service import _save_output_images

    plugin = get_image_plugin_manager().get_plugin("rotate")
    assert plugin is not None
    frame = ImageUtils.load_image_or_video(source_path)
    assert frame is not None
    pil_image = Image.fromarray(frame).convert("RGB")
    outputs = plugin.run([pil_image], parameters={"direction": "90_right"})
    output_bytes, _ext = _save_output_images(outputs[0], source_format, source_path)
    return output_bytes


def test_plugin_output_keeps_comfyui_png_text_chunks():
    """A rotate run must not destroy the ComfyUI provenance chunks.

    ``metadata["png"]["workflow"]`` / ``["prompt"]`` are unrecoverable once the
    derived file is written without them.
    """
    from PIL import PngImagePlugin

    with tempfile.TemporaryDirectory() as temp_dir:
        source = os.path.join(temp_dir, "comfy.png")
        info = PngImagePlugin.PngInfo()
        info.add_text("parameters", "a prompt, steps: 20")
        info.add_text("workflow", '{"1": {"class_type": "KSampler", "inputs": {}}}')
        Image.new("RGB", (24, 32), color=(10, 20, 30)).save(source, pnginfo=info)

        with Image.open(BytesIO(_rotate_output_bytes(source, "PNG"))) as out:
            assert out.text.get("parameters") == "a prompt, steps: 20"
            assert (
                out.text.get("workflow")
                == '{"1": {"class_type": "KSampler", "inputs": {}}}'
            )


def test_plugin_output_keeps_jpeg_exif_description_fields():
    with tempfile.TemporaryDirectory() as temp_dir:
        source = os.path.join(temp_dir, "camera.jpg")
        img = Image.new("RGB", (24, 32), color=(90, 90, 90))
        exif = img.getexif()
        exif[0x0110] = "PixlCam 9000"  # Model
        exif.get_ifd(0x8769)[0x9003] = "2026:08:15 09:41:00"  # DateTimeOriginal
        img.save(source, exif=exif)

        with Image.open(BytesIO(_rotate_output_bytes(source, "JPEG"))) as out:
            out_exif = out.getexif()
            assert out_exif.get(0x0110) == "PixlCam 9000"
            assert out_exif.get_ifd(0x8769).get(0x9003) == "2026:08:15 09:41:00"


def test_plugin_output_drops_exif_orientation_so_it_is_not_rotated_twice():
    """``load_image_or_video`` already applied the source's orientation.

    Re-stamping tag 0x0112 onto the output would turn it a second time on
    display, and the displayed size would then disagree with the stored size.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        source = os.path.join(temp_dir, "sideways.jpg")
        # 40x20 stored, orientation 6 => displayed upright as 20x40.
        img = Image.new("RGB", (40, 20), color=(200, 30, 30))
        exif = img.getexif()
        exif[0x0112] = 6
        exif[0x0110] = "PixlCam 9000"
        img.save(source, exif=exif)

        output_bytes = _rotate_output_bytes(source, "JPEG")
        with Image.open(BytesIO(output_bytes)) as out:
            stored_size = out.size
            assert out.getexif().get(0x0112, 1) == 1, (
                "output carried the source's orientation and will be double-rotated"
            )
            # Descriptive fields still survive; only orientation is stripped.
            assert out.getexif().get(0x0110) == "PixlCam 9000"
        with Image.open(BytesIO(output_bytes)) as out:
            from PIL import ImageOps

            assert ImageOps.exif_transpose(out).size == stored_size

        # 40x20 stored + orientation 6 loads as 20x40; rotating 90° right => 40x20.
        assert stored_size == (40, 20)


def test_plugin_output_invents_no_metadata_when_the_source_has_none():
    with tempfile.TemporaryDirectory() as temp_dir:
        for name, fmt in (("plain.png", "PNG"), ("plain.jpg", "JPEG")):
            source = os.path.join(temp_dir, name)
            Image.new("RGB", (24, 32), color=(5, 5, 5)).save(source)
            with Image.open(BytesIO(_rotate_output_bytes(source, fmt))) as out:
                assert not getattr(out, "text", None)
                assert not out.getexif()


def test_encoded_plugin_output_is_returned_untouched():
    """Video sources and pre-encoded bytes must not be re-muxed for metadata."""
    from pixlstash.image_plugins.service import _save_output_images

    with tempfile.TemporaryDirectory() as temp_dir:
        source = os.path.join(temp_dir, "clip.mp4")
        _write_test_video(source, 32, 24, 3)
        with open(source, "rb") as handle:
            encoded = handle.read()

        assert _save_output_images(encoded, "MP4", source) == (encoded, ".mp4")
        assert _save_output_images((encoded, "mp4"), "MP4", source) == (
            encoded,
            ".mp4",
        )
        assert _save_output_images(b"jpegbytes", "JPEG", source) == (
            b"jpegbytes",
            ".jpg",
        )


# ---------------------------------------------------------------------------
# Registry loading rules (issue #968). No Server, and no dependency on the
# shipped built-ins: both directories are temporary, so these cost a few
# milliseconds and cannot be broken by a change to a real built-in.
# ---------------------------------------------------------------------------

_CONCRETE_PLUGIN = """
from pixlstash.image_plugins.base import ImagePlugin


class {cls}(ImagePlugin):
    name = "{name}"
    display_name = "{name}"

    def parameter_schema(self):
        return []

    def run(self, images, parameters=None, progress_callback=None,
            error_callback=None, captions=None):
        return list(images)
"""


def _write(folder: str, filename: str, source: str) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(source)
    return path


def _manager(temp_dir: str):
    """A manager over two temporary directories, both possibly empty."""
    from pixlstash.image_plugins.registry import ImagePluginManager

    return ImagePluginManager(
        built_in_dir=os.path.join(temp_dir, "built-in"),
        user_dir=os.path.join(temp_dir, "user"),
    )


def test_registry_prefers_the_concrete_class_over_an_abstract_intermediate():
    with tempfile.TemporaryDirectory() as temp_dir:
        _write(
            os.path.join(temp_dir, "user"),
            "layered.py",
            '''
from pixlstash.image_plugins.base import ImagePlugin


class Intermediate(ImagePlugin):
    """Defined first, and abstract: `run` is left to subclasses."""

    name = "intermediate"

    def parameter_schema(self):
        return []


class Real(Intermediate):
    name = "layered"
    display_name = "Layered"

    def run(self, images, parameters=None, progress_callback=None,
            error_callback=None, captions=None):
        return list(images)
''',
        )
        manager = _manager(temp_dir)
        manager.reload()

        assert type(manager.get_plugin("layered")).__name__ == "Real"
        assert manager.get_plugin("intermediate") is None
        assert manager.list_errors() == []


def test_a_file_whose_only_plugin_class_is_abstract_names_the_missing_method():
    """The abstract skip must not cost the diagnostic for a forgotten method."""
    with tempfile.TemporaryDirectory() as temp_dir:
        path = _write(
            os.path.join(temp_dir, "user"),
            "forgetful.py",
            """
from pixlstash.image_plugins.base import ImagePlugin


class Forgetful(ImagePlugin):
    name = "forgetful"

    def parameter_schema(self):
        return []
""",
        )
        manager = _manager(temp_dir)
        manager.reload()

        assert manager.get_plugin("forgetful") is None
        errors = manager.list_errors()
        assert len(errors) == 1
        assert errors[0]["file"] == path
        assert "Forgetful" in errors[0]["message"]
        assert "run" in errors[0]["message"]


def test_a_malformed_models_header_is_refused_at_load_not_at_request_time():
    """`list_plugins()` runs unguarded, so one bad header must not reach it.

    Declaring the single dict the docs show as an *entry*, unwrapped, is the
    natural mistake. Before the registry probed the schema at load, this
    registered fine and then raised inside the comprehension in
    `list_plugins()`, taking `GET /pictures/plugins` down for every plugin.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        path = _write(
            os.path.join(temp_dir, "user"),
            "unwrapped.py",
            """
from pixlstash.image_plugins.base import ImagePlugin


class Unwrapped(ImagePlugin):
    name = "unwrapped"
    models = {"name": "example/model", "license": "MIT"}

    def parameter_schema(self):
        return []

    def run(self, images, parameters=None, **kwargs):
        return images
""",
        )
        _write(
            os.path.join(temp_dir, "user"),
            "wellformed.py",
            """
from pixlstash.image_plugins.base import ImagePlugin


class WellFormed(ImagePlugin):
    name = "wellformed"
    models = [{"name": "example/model", "license": "MIT"}]

    def parameter_schema(self):
        return []

    def run(self, images, parameters=None, **kwargs):
        return images
""",
        )
        manager = _manager(temp_dir)
        manager.reload()

        assert manager.get_plugin("unwrapped") is None
        errors = manager.list_errors()
        assert len(errors) == 1
        assert errors[0]["file"] == path
        # The message has to name the contract that was broken. The bare
        # `dict(...)` ValueError this used to raise said "dictionary update
        # sequence element #0 has length 1; 2 is required", which points at
        # nothing the plugin author wrote.
        assert "Unwrapped.models" in errors[0]["message"]
        assert "list" in errors[0]["message"]
        # The neighbour is still listed, which is the whole point: one bad
        # header costs its own plugin, not the endpoint.
        assert [schema["name"] for schema in manager.list_plugins()] == ["wellformed"]


def test_registry_ignores_a_plugin_class_the_file_only_imported():
    """A user file importing another plugin for reference must not ship it."""
    with tempfile.TemporaryDirectory() as temp_dir:
        helper_dir = os.path.join(temp_dir, "helpers")
        # Named `filmgrain` after the built-in below, so shipping the imported
        # class would replace that built-in - the compounding failure #968
        # describes, since a user plugin also wins a name collision.
        _write(
            helper_dir,
            "borrowed.py",
            _CONCRETE_PLUGIN.format(cls="Borrowed", name="filmgrain"),
        )
        _write(
            os.path.join(temp_dir, "built-in"),
            "filmgrain.py",
            _CONCRETE_PLUGIN.format(cls="FilmGrain", name="filmgrain"),
        )
        _write(
            os.path.join(temp_dir, "user"),
            "mine.py",
            "from borrowed import Borrowed  # noqa: F401\n"
            + _CONCRETE_PLUGIN.format(cls="Mine", name="mine"),
        )

        sys.path.insert(0, helper_dir)
        try:
            manager = _manager(temp_dir)
            manager.reload()
        finally:
            # The plugin module holds its own reference to the imported class,
            # so neither of these can un-import it out from under the manager.
            sys.path.remove(helper_dir)
            sys.modules.pop("borrowed", None)

        assert type(manager.get_plugin("mine")).__name__ == "Mine"
        assert type(manager.get_plugin("filmgrain")).__name__ == "FilmGrain"
        assert manager.list_errors() == []


def test_a_user_plugin_shadowing_a_built_in_is_reported_against_the_user_file():
    with tempfile.TemporaryDirectory() as temp_dir:
        _write(
            os.path.join(temp_dir, "built-in"),
            "filmgrain.py",
            _CONCRETE_PLUGIN.format(cls="FilmGrain", name="filmgrain"),
        )
        user_path = _write(
            os.path.join(temp_dir, "user"),
            "my_grain.py",
            _CONCRETE_PLUGIN.format(cls="MyGrain", name="filmgrain"),
        )
        manager = _manager(temp_dir)
        manager.reload()

        # User still wins, deliberately - but it is now visible, and it is the
        # user file that is named rather than the built-in it displaced.
        assert type(manager.get_plugin("filmgrain")).__name__ == "MyGrain"
        errors = manager.list_errors()
        assert len(errors) == 1
        assert errors[0]["file"] == user_path
        assert "filmgrain" in errors[0]["message"]
        assert "built-in" in errors[0]["message"]


def test_two_user_plugins_with_one_name_keep_the_first_and_are_not_errors():
    """Only a shadowed *built-in* is recorded; a user-vs-user clash still logs."""
    with tempfile.TemporaryDirectory() as temp_dir:
        user_dir = os.path.join(temp_dir, "user")
        _write(user_dir, "a_first.py", _CONCRETE_PLUGIN.format(cls="A", name="twin"))
        _write(user_dir, "b_second.py", _CONCRETE_PLUGIN.format(cls="B", name="twin"))
        manager = _manager(temp_dir)
        manager.reload()

        # Sorted order: a_first.py claims the name, b_second.py is the duplicate.
        assert type(manager.get_plugin("twin")).__name__ == "A"
        assert manager.list_errors() == []
