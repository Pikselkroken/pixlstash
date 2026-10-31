"""Share-ready copies (#2417): generation data stripped by default, pixels kept."""

import datetime
import json
import os
import tempfile
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageCms, PngImagePlugin

from pixlstash.db_models.picture import Picture
from pixlstash.server import Server
from pixlstash.utils.image_processing.generation_data import strip_generation_data
from pixlstash.utils.service.share_copies import (
    METADATA_KEPT,
    METADATA_REMOVED,
    METADATA_UNCHANGED,
    make_share_copies,
    share_copies_root,
    share_links_root,
    sweep_share_copies,
)
from pixlstash.utils.service.export_utils import ExportUtils
from tests.utils import API_PREFIX, upload_pictures_and_wait

COMFY_PROMPT = json.dumps({"3": {"class_type": "KSampler", "inputs": {"seed": 1}}})
COMFY_WORKFLOW = json.dumps({"nodes": [{"id": 3, "type": "KSampler"}]})
A1111_PARAMETERS = "a cat, best quality\nSteps: 20, Sampler: Euler a, Seed: 1"
XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description>'
    b"<dc:description>a cat</dc:description></rdf:Description></rdf:RDF>"
    b"</x:xmpmeta>"
)

# Text that must not survive into a stripped copy, whatever block it is in.
GENERATION_MARKERS = (b"KSampler", b"best quality", b"Steps: 20", b"a cat")


def _pattern(mode="RGB", size=(48, 32)):
    img = Image.new(mode, size)
    img.putdata(
        [
            tuple((x * 5 + y * 3 + c * 40) % 256 for c in range(len(mode)))
            for y in range(size[1])
            for x in range(size[0])
        ]
    )
    return img


def _srgb_icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _exif(orientation=None, comment=None) -> Image.Exif:
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    if comment:
        exif.get_ifd(0x8769)[0x9286] = b"ASCII\x00\x00\x00" + comment.encode()
    return exif


def _comfy_png(orientation=None) -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", COMFY_PROMPT)
    info.add_text("workflow", COMFY_WORKFLOW, zip=True)
    info.add_itxt("XML:com.adobe.xmp", XMP.decode())
    buf = BytesIO()
    kwargs = {"pnginfo": info, "icc_profile": _srgb_icc()}
    if orientation:
        kwargs["exif"] = _exif(orientation, A1111_PARAMETERS).tobytes()
    _pattern("RGBA").save(buf, format="PNG", **kwargs)
    return buf.getvalue()


def _a1111_png() -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", A1111_PARAMETERS)
    buf = BytesIO()
    _pattern().save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def _a1111_jpeg(orientation=None) -> bytes:
    buf = BytesIO()
    _pattern().save(
        buf,
        format="JPEG",
        quality=90,
        exif=_exif(orientation, A1111_PARAMETERS).tobytes(),
        icc_profile=_srgb_icc(),
        xmp=XMP,
        comment=b"Steps: 20",
    )
    return buf.getvalue()


def _webp(orientation=None) -> bytes:
    buf = BytesIO()
    _pattern().save(
        buf,
        format="WEBP",
        lossless=True,
        exif=_exif(orientation, A1111_PARAMETERS).tobytes(),
        xmp=XMP,
        icc_profile=_srgb_icc(),
    )
    return buf.getvalue()


def _pixels(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as img:
        return img.tobytes()


def _assert_no_generation_data(data: bytes):
    for marker in GENERATION_MARKERS:
        assert marker not in data, marker
    with Image.open(BytesIO(data)) as img:
        img.load()
        texts = getattr(img, "text", {}) or {}
        assert not texts, texts
        assert "xmp" not in img.info and "XML:com.adobe.xmp" not in img.info
        exif = img.getexif()
        assert set(exif) <= {0x0112}, dict(exif)
        assert not exif.get_ifd(0x8769)


@pytest.mark.parametrize(
    "make", [_comfy_png, _a1111_png, _a1111_jpeg, _webp], ids=lambda f: f.__name__
)
def test_strip_removes_generation_data_and_keeps_the_pixels(make):
    original = make()
    # The fixture really carries what the strip must remove.
    assert any(marker in original for marker in GENERATION_MARKERS)

    stripped = strip_generation_data(original)

    _assert_no_generation_data(stripped)
    assert _pixels(stripped) == _pixels(original)


@pytest.mark.parametrize(
    "make", [_comfy_png, _a1111_jpeg, _webp], ids=lambda f: f.__name__
)
def test_strip_keeps_the_orientation_and_colour_profile(make):
    original = make(orientation=6)

    stripped = strip_generation_data(original)

    _assert_no_generation_data(stripped)
    with Image.open(BytesIO(stripped)) as img:
        assert img.getexif().get(0x0112) == 6
        assert img.info.get("icc_profile") == _srgb_icc()


def test_strip_drops_what_follows_a_jpeg_and_keeps_a_progressive_one_intact():
    buf = BytesIO()
    _pattern(size=(96, 64)).save(
        buf, format="JPEG", progressive=True, comment=b"Steps: 20"
    )
    original = buf.getvalue()
    trailer = b"\xff\xfe\x00\x0bSteps: 20"  # a COM segment after EOI

    stripped = strip_generation_data(original + trailer)

    _assert_no_generation_data(stripped)
    assert stripped.endswith(b"\xff\xd9")
    assert _pixels(stripped) == _pixels(original)


def test_a_png_cut_off_before_iend_is_skipped_as_unreadable(library):
    png = _comfy_png()
    iend = png.rindex(b"IEND") - 4  # the start of the IEND chunk
    with pytest.raises(ValueError, match="IEND"):
        strip_generation_data(png[:iend])
    library["add"](png[:iend], "cut.png")

    result = _copy(library)

    assert result["files"] == []
    assert result["skipped"] == [{"picture_id": 1, "reason": "unreadable"}]


def test_a_webp_cut_off_before_its_image_data_is_refused():
    webp = _webp()
    cut = webp[: webp.index(b"VP8L")]  # VP8X and ICCP survive, the image does not
    assert b"VP8X" in cut
    with pytest.raises(ValueError, match="image data"):
        strip_generation_data(cut)


def test_strip_leaves_formats_it_does_not_handle():
    buf = BytesIO()
    _pattern().save(buf, format="GIF", comment=b"Steps: 20")
    assert strip_generation_data(buf.getvalue()) is None


# ---- make_share_copies ------------------------------------------------------


@pytest.fixture
def library(tmp_path):
    image_root = tmp_path / "images"
    image_root.mkdir()
    pictures = []

    def add(data: bytes, file_name: str, original_file_name=None, created_at=None):
        (image_root / file_name).write_bytes(data)
        pictures.append(
            {
                "id": len(pictures) + 1,
                "file_path": file_name,
                "original_file_name": original_file_name,
                "created_at": created_at,
            }
        )
        return pictures[-1]

    return {
        "image_root": str(image_root),
        "root": str(tmp_path / "share-copies"),
        "add": add,
        "pictures": pictures,
    }


def _copy(library, **kwargs):
    return make_share_copies(
        library["pictures"], library["image_root"], library["root"], **kwargs
    )


def test_comfyui_and_a1111_pngs_leave_without_generation_data_by_default(library):
    comfy, a1111 = _comfy_png(), _a1111_png()
    library["add"](comfy, "aa11.png", "ComfyUI_00001_.png")
    library["add"](a1111, "bb22.png", "00001-1234.png")

    result = _copy(library)

    assert [f["name"] for f in result["files"]] == [
        "ComfyUI_00001_.png",
        "00001-1234.png",
    ]
    for entry, original in zip(result["files"], (comfy, a1111)):
        assert entry["metadata"] == METADATA_REMOVED
        assert os.path.dirname(entry["path"]) == result["directory"]
        with open(entry["path"], "rb") as fh:
            copy = fh.read()
        assert entry["size"] == len(copy)
        _assert_no_generation_data(copy)
        assert _pixels(copy) == _pixels(original)
    # The vault's own files are never touched.
    with open(os.path.join(library["image_root"], "aa11.png"), "rb") as fh:
        assert fh.read() == comfy


def test_keep_generation_data_copies_byte_for_byte(library):
    originals = [_comfy_png(), _a1111_png(), _a1111_jpeg(), _webp()]
    for index, data in enumerate(originals):
        library["add"](data, f"p{index}.bin")

    result = _copy(library, keep_generation_data=True)

    assert [f["metadata"] for f in result["files"]] == [METADATA_KEPT] * 4
    for entry, original in zip(result["files"], originals):
        with open(entry["path"], "rb") as fh:
            assert fh.read() == original


def _link(library, **kwargs):
    return _copy(
        library,
        link_originals=True,
        link_root=share_links_root(library["image_root"]),
        **kwargs,
    )


def test_link_originals_hands_over_the_originals_under_the_share_names(library):
    # A drag-out (#2420): the vault file itself, named as Ctrl+C names it, linked
    # inside the library so the link is on the original's own drive.
    comfy, a1111 = _comfy_png(), _a1111_png()
    library["add"](comfy, "aa11.png", "ComfyUI_00001_.png")
    library["add"](a1111, "bb22.png", "ComfyUI_00001_.png")
    plain = _copy(library)

    result = _link(library)

    assert [f["name"] for f in result["files"]] == [f["name"] for f in plain["files"]]
    links = os.path.join(share_links_root(library["image_root"]), result["batch"])
    for entry, stored in zip(result["files"], ("aa11.png", "bb22.png")):
        original = os.path.join(library["image_root"], stored)
        assert entry["metadata"] == METADATA_KEPT
        assert os.path.dirname(entry["path"]) == links
        assert os.path.samefile(entry["path"], original)
    # Removing the batch removes the second name only.
    sweep_share_copies(share_links_root(library["image_root"]), max_age_seconds=-1)
    assert not os.path.exists(links)
    with open(os.path.join(library["image_root"], "aa11.png"), "rb") as fh:
        assert fh.read() == comfy


def test_link_originals_falls_back_link_by_link_and_copies_only_last(
    library, monkeypatch
):
    comfy = _comfy_png()
    library["add"](comfy, "aa11.png", "ComfyUI_00001_.png")
    original = os.path.join(library["image_root"], "aa11.png")
    real_link = os.link
    links_root = share_links_root(library["image_root"])

    # The library's folder refuses (say, a mount inside it): the staging root
    # still takes a link.
    def no_link_in_library(source, target):
        if target.startswith(links_root):
            raise OSError(18, "Invalid cross-device link")
        real_link(source, target)

    monkeypatch.setattr(os, "link", no_link_in_library)
    (entry,) = _link(library)["files"]
    assert os.path.dirname(os.path.dirname(entry["path"])) == library["root"]
    assert os.path.samefile(entry["path"], original)

    # Nowhere takes a link (an original on a third drive): only then a copy.
    def cross_device(source, target):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", cross_device)
    (entry,) = _link(library)["files"]
    assert not os.path.samefile(entry["path"], original)
    with open(entry["path"], "rb") as fh:
        assert fh.read() == comfy


def test_made_names_are_when_it_was_made_with_a_suffix_only_when_needed(library):
    # Drag and Ctrl/Cmd+C (#2420): PixlStash-<date>-<time>[-n], whatever the
    # picture's original name, so a drop never asks to replace anything.
    made = datetime.datetime(2026, 10, 24, 14, 22, 42, tzinfo=datetime.timezone.utc)
    stem = made.astimezone().strftime("PixlStash-%Y-%m-%d-%H.%M.%S")
    library["add"](_comfy_png(), "a.png", "ComfyUI_00001_.png", made)
    library["add"](_comfy_png(), "b.png", "ComfyUI_00001_.png", made)
    library["add"](_a1111_jpeg(), "c.jpg", "1fea4e4c-a7a3-47b3-a909.jpg", made)
    library["add"](_comfy_png(), "d.png", None, made + datetime.timedelta(seconds=1))
    library["add"](_comfy_png(), "e.png", "x.png", None)

    names = [f["name"] for f in _link(library, file_names="made")["files"]]

    later = (made + datetime.timedelta(seconds=1)).astimezone()
    assert names == [
        f"{stem}.png",
        f"{stem}-2.png",
        f"{stem}-3.jpg",
        later.strftime("PixlStash-%Y-%m-%d-%H.%M.%S") + ".png",
        "PixlStash-5.png",
    ]
    assert len({n.casefold() for n in names}) == len(names)


def test_names_collide_into_suffixes_and_can_be_pixlstash_names(library):
    library["add"](_a1111_png(), "aa11.png", "photo.png")
    library["add"](_a1111_png(), "bb22.png", "Photo.jpg")
    library["add"](_a1111_png(), "cc33.png", "photo_2.png")
    library["add"](_a1111_png(), "dd44.png", None)

    names = [f["name"] for f in _copy(library)["files"]]
    # The extension is the copy's real format, not the stored name's.
    assert names == ["photo.png", "Photo_2.png", "photo_2_3.png", "dd44.png"]

    own = [f["name"] for f in _copy(library, file_names="pixlstash")["files"]]
    assert own == ["aa11.png", "bb22.png", "cc33.png", "dd44.png"]


def test_a_picture_without_an_original_name_is_named_by_when_it_was_made(library):
    made = datetime.datetime(2026, 10, 24, 14, 22, 42, tzinfo=datetime.timezone.utc)
    stem = made.astimezone().strftime("PixlStash %Y-%m-%d %H.%M.%S")
    library["add"](_comfy_png(), "1fea4e4c.png", None, made)
    library["add"](_comfy_png(), "598fcee3.png", None, made.replace(tzinfo=None))
    library["add"](_comfy_png(), "be143717.png", "mine.png", made)

    names = [f["name"] for f in _copy(library)["files"]]
    # Same moment twice (the naive one is read as UTC): the suffix separates them.
    assert names == [f"{stem}.png", f"{stem}_2.png", "mine.png"]
    own = [f["name"] for f in _copy(library, file_names="pixlstash")["files"]]
    assert own == ["1fea4e4c.png", "598fcee3.png", "be143717.png"]


def test_max_long_edge_shrinks_and_strips(library):
    library["add"](_comfy_png(), "big.png")
    library["add"](_a1111_jpeg(), "big.jpg")

    result = _copy(library, max_long_edge=24)

    for entry in result["files"]:
        assert entry["resized"] is True
        with open(entry["path"], "rb") as fh:
            copy = fh.read()
        _assert_no_generation_data(copy)
        with Image.open(BytesIO(copy)) as img:
            assert max(img.size) == 24


def test_a_resized_copy_is_turned_upright_rather_than_tagged(library):
    library["add"](_a1111_jpeg(orientation=6), "turned.jpg")

    (entry,) = _copy(library, max_long_edge=24, keep_generation_data=True)["files"]

    with Image.open(entry["path"]) as img:
        # 48x32 shown a quarter turn round is 32x48, so 16x24 at this cap.
        assert img.size == (16, 24)
        assert img.getexif().get(0x0112) is None


def test_max_long_edge_never_enlarges_and_keeps_a_small_picture_untouched(library):
    original = _a1111_png()
    library["add"](original, "small.png")

    (entry,) = _copy(library, max_long_edge=4096, keep_generation_data=True)["files"]

    assert entry["resized"] is False
    with open(entry["path"], "rb") as fh:
        assert fh.read() == original


def test_watermark_is_applied(library):
    library["add"](_a1111_png(), "a.png")
    mark = BytesIO()
    Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(mark, format="PNG")

    (entry,) = _copy(library, watermark_bytes=mark.getvalue())["files"]

    assert entry["watermarked"] is True
    with open(entry["path"], "rb") as fh:
        copy = fh.read()
    _assert_no_generation_data(copy)
    assert _pixels(copy) != _pixels(_a1111_png())


def _transparent_png(mode: str) -> bytes:
    """64x64 with its left half see-through, as an alpha channel or a tRNS."""
    buf = BytesIO()
    if mode == "P":
        img = Image.new("P", (64, 64), 1)
        img.putpalette([0, 0, 0, 200, 40, 40])
        img.paste(0, (0, 0, 32, 64))
        img.save(buf, format="PNG", transparency=0)
    else:
        img = _pattern("RGBA", size=(64, 64))
        img.putalpha(255)
        img.paste((0, 0, 0, 0), (0, 0, 32, 64))
        img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize("mode", ["RGBA", "P"])
@pytest.mark.parametrize("keep", [False, True])
def test_a_re_encoded_copy_keeps_its_transparency(library, mode, keep):
    library["add"](_transparent_png(mode), "clear.png")
    mark = BytesIO()
    Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(mark, format="PNG")

    for kwargs in ({"max_long_edge": 32}, {"watermark_bytes": mark.getvalue()}):
        (entry,) = _copy(library, keep_generation_data=keep, **kwargs)["files"]
        with Image.open(entry["path"]) as img:
            rgba = img.convert("RGBA")
            assert rgba.getpixel((1, 1))[3] == 0, kwargs
            assert rgba.getpixel((rgba.width - 1, 1))[3] == 255, kwargs


def test_keep_survives_a_re_encode(library):
    library["add"](_comfy_png(), "a.png")
    library["add"](_a1111_jpeg(), "b.jpg")

    entries = _copy(library, max_long_edge=24, keep_generation_data=True)["files"]

    with Image.open(entries[0]["path"]) as img:
        assert img.text["prompt"] == COMFY_PROMPT
        assert img.text["workflow"] == COMFY_WORKFLOW
    with Image.open(entries[1]["path"]) as img:
        assert b"Steps: 20" in img.getexif().get_ifd(0x8769)[0x9286]
        assert img.info.get("xmp") and img.info.get("comment") == b"Steps: 20"


def test_videos_and_unhandled_formats_pass_through_and_say_so(library):
    video = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    library["add"](video, "clip.mp4", "clip.mp4")
    gif = BytesIO()
    _pattern().save(gif, format="GIF")
    library["add"](gif.getvalue(), "anim.gif")
    library["pictures"].append({"id": 99, "file_path": "gone.png"})

    result = _copy(library)

    assert [f["metadata"] for f in result["files"]] == [METADATA_UNCHANGED] * 2
    with open(result["files"][0]["path"], "rb") as fh:
        assert fh.read() == video
    assert result["skipped"] == [{"picture_id": 99, "reason": "missing"}]


def test_sweep_removes_only_old_batches(library):
    library["add"](_a1111_png(), "a.png")
    old = _copy(library)["directory"]
    new = _copy(library)["directory"]
    stranger = os.path.join(library["root"], "not-a-batch")
    os.makedirs(stranger)
    past = time.time() - 13 * 3600
    for path in (old, stranger):
        os.utime(path, (past, past))

    assert sweep_share_copies(library["root"]) == 1

    assert not os.path.exists(old)
    assert os.path.isdir(new)
    assert os.path.isdir(stranger)


# ---- the route --------------------------------------------------------------


@pytest.fixture(scope="module")
def env():
    with tempfile.TemporaryDirectory() as temp_dir:
        os.makedirs(os.path.join(temp_dir, "images"))
        config_path = os.path.join(temp_dir, "server-config.json")
        with open(config_path, "w") as f:
            f.write(json.dumps({"port": 8000}))
        with Server(config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200
            yield {"client": client, "config_dir": temp_dir}


def test_route_stages_stripped_copies_beside_the_server_config(env):
    client = env["client"]
    comfy = _comfy_png()
    result = upload_pictures_and_wait(
        client, [("file", ("ComfyUI_00042_.png", BytesIO(comfy), "image/png"))]
    )
    picture_id = result["results"][0]["picture_id"]

    resp = client.post(
        f"{API_PREFIX}/pictures/share-copies",
        json={"picture_ids": [picture_id, 987654]},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    root = share_copies_root(env["config_dir"])
    assert os.path.dirname(body["directory"]) == root
    (entry,) = body["files"]
    assert entry["picture_id"] == picture_id
    assert entry["name"] == "ComfyUI_00042_.png"
    assert entry["metadata"] == METADATA_REMOVED
    with open(entry["path"], "rb") as fh:
        copy = fh.read()
    _assert_no_generation_data(copy)
    assert _pixels(copy) == _pixels(comfy)
    assert body["skipped"] == [{"picture_id": 987654, "reason": "not_found"}]

    kept = client.post(
        f"{API_PREFIX}/pictures/share-copies",
        json={"picture_ids": [picture_id], "keep_generation_data": True},
    ).json()["files"][0]
    with open(kept["path"], "rb") as fh:
        assert b"KSampler" in fh.read()

    linked = client.post(
        f"{API_PREFIX}/pictures/share-copies",
        json={"picture_ids": [picture_id], "link_originals": True},
    ).json()["files"][0]
    assert linked["name"] == "ComfyUI_00042_.png"
    assert linked["metadata"] == METADATA_KEPT
    assert os.stat(linked["path"]).st_nlink >= 2
    # Inside the library, on its originals' drive, in the folder no scan reads.
    assert os.path.basename(os.path.dirname(os.path.dirname(linked["path"]))) == (
        ".pixlstash-share"
    )

    # No cap. (The route also reads ids in chunks for SQLite builds with the
    # 999-parameter limit; this machine's build is not one, so that is unproven here.)
    many = client.post(
        f"{API_PREFIX}/pictures/share-copies",
        json={
            "picture_ids": [picture_id, *range(10**6, 10**6 + 2500)],
            "link_originals": True,
        },
    )
    assert many.status_code == 200, many.text
    assert [f["picture_id"] for f in many.json()["files"]] == [picture_id]
    assert len(many.json()["skipped"]) == 2500


def test_user_config_remembers_whether_an_export_keeps_the_workflow(env):
    """Strip by default; a string "false" never turns it on."""
    client = env["client"]
    url = f"{API_PREFIX}/users/me/config"
    assert client.get(url).json()["keep_workflow_on_export"] is False

    try:
        resp = client.patch(url, json={"keep_workflow_on_export": True})
        assert resp.status_code == 200, resp.text
        assert client.get(url).json()["keep_workflow_on_export"] is True

        client.patch(url, json={"keep_workflow_on_export": "false"})
        assert client.get(url).json()["keep_workflow_on_export"] is False
    finally:
        client.patch(url, json={"keep_workflow_on_export": False})


def _export(library, scale_factor, **extra):
    """Run the export writer over *library* into a dict, as a ZIP would."""
    members = {}

    class Sink:
        def write(self, full_path, arcname):
            with open(full_path, "rb") as fh:
                members[arcname] = fh.read()

        def writestr(self, arcname, data):
            members[arcname] = data

    params = {
        "export_type_d": Picture.ExportType.FULL,
        "caption_mode_d": "description",
        "include_character_name_enabled": False,
        "scale_factor": scale_factor,
        **extra,
    }
    pics = [SimpleNamespace(description="a caption", **p) for p in library["pictures"]]
    server = SimpleNamespace(vault=SimpleNamespace(image_root=library["image_root"]))
    ExportUtils._write_export_pictures(
        Sink(), server, "t", {"t": {"processed": 0}}, pics, params, {}, {}, {}
    )
    return members


@pytest.mark.parametrize("scale_factor", [1.0, 0.5])
def test_export_strips_unless_asked_to_keep(library, scale_factor):
    comfy, jpeg = _comfy_png(orientation=6), _a1111_jpeg(orientation=6)
    library["add"](comfy, "aa11.png")
    library["add"](jpeg, "bb22.jpg")

    stripped = _export(library, scale_factor, keep_generation_data=False)
    assert sorted(stripped) == [
        "image_00001.png",
        "image_00001.txt",
        "image_00002.jpg",
        "image_00002.txt",
    ]
    for name, original in (("image_00001.png", comfy), ("image_00002.jpg", jpeg)):
        _assert_no_generation_data(stripped[name])
        with Image.open(BytesIO(stripped[name])) as img:
            # The owner's rotation survives the strip.
            assert img.getexif().get(0x0112) == 6
        if scale_factor == 1.0 and name.endswith(".png"):
            assert _pixels(stripped[name]) == _pixels(original)
    assert stripped["image_00001.txt"] == "a caption\n"

    kept = _export(library, scale_factor, keep_generation_data=True)
    assert b"KSampler" in kept["image_00001.png"]
    assert b"Steps: 20" in kept["image_00002.jpg"]
    if scale_factor == 1.0:
        assert kept["image_00001.png"] == comfy

    # A caller that never says (the training dataset) keeps the files as they are.
    assert b"KSampler" in _export(library, scale_factor)["image_00001.png"]


def test_an_export_request_strips_unless_it_says_keep():
    request = SimpleNamespace(query_params=SimpleNamespace(get=lambda k: None))
    request.query_params.getlist = lambda k: []
    parse = ExportUtils._parse_export_params
    assert parse(request, {})["keep_generation_data"] is False
    assert parse(request, {"keep_generation_data": True})["keep_generation_data"]


def test_a_picture_that_cannot_be_stripped_is_left_out_not_sent(library):
    library["add"](_comfy_png()[:60], "aa11.png")
    library["add"](_comfy_png(), "bb22.png")

    members = _export(library, 1.0, keep_generation_data=False)

    assert "image_00001.png" not in members
    _assert_no_generation_data(members["image_00002.png"])
