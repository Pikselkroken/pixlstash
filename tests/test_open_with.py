"""Open with: the OS app listing and the edit-copy session, without a Server.

Nothing here launches a real app: the listing tools are fed canned output and
the edit-copy check runs on files in a temp folder. The routes and the import
are in ``tests/test_share_copies.py``, which already has a Server.
"""

import json
import os
import threading
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from pixlstash.services import edit_copies
from pixlstash.services.edit_copies import (
    SESSION_FILE,
    EditCopyWatcher,
    changed_file,
    create_session,
    read_session,
)
from pixlstash.utils import open_with
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.open_with import OpenWithApp

GIO_OUTPUT = """Default application for "image/png": xviewer.desktop
Registered applications:
\torg.gimp.GIMP.desktop
\tfirefox.desktop
\tkrita_png.desktop
\txviewer.desktop
Recommended applications:
\torg.gimp.GIMP.desktop
\tfirefox.desktop
\txviewer.desktop
"""


def _png(colour=(10, 20, 30)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), colour).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg() -> bytes:
    buf = BytesIO()
    Image.effect_noise((64, 64), 50).convert("RGB").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _empty_cache():
    open_with._cache.clear()
    yield
    open_with._cache.clear()


# ---- Linux ------------------------------------------------------------------


def test_gio_mime_takes_both_blocks_recommended_first_with_the_default():
    default, ids = open_with.parse_gio_mime(GIO_OUTPUT)
    assert default == "xviewer.desktop"
    # krita_png is only Registered; Recommended not being empty must not hide it.
    assert ids == [
        "org.gimp.GIMP.desktop",
        "firefox.desktop",
        "xviewer.desktop",
        "krita_png.desktop",
    ]


def test_gio_mime_without_recommended_uses_registered_and_adds_the_default():
    output = (
        'Default application for "image/png": other.desktop\n'
        "Registered applications:\n\tgimp.desktop\n"
        "No recommended applications\n"
    )
    assert open_with.parse_gio_mime(output) == (
        "other.desktop",
        ["gimp.desktop", "other.desktop"],
    )
    assert open_with.parse_gio_mime("No default applications\n") == (None, [])


def _desktop(directory, name, body):
    os.makedirs(os.path.join(directory, "applications"), exist_ok=True)
    with open(os.path.join(directory, "applications", name), "w") as fh:
        fh.write(body)


def test_linux_listing_reads_names_and_puts_viewers_and_browsers_last(
    tmp_path, monkeypatch
):
    home, system = str(tmp_path / "home"), str(tmp_path / "sys")
    monkeypatch.setenv("XDG_DATA_HOME", home)
    monkeypatch.setenv("XDG_DATA_DIRS", system)
    _desktop(
        system,
        "org.gimp.GIMP.desktop",
        "[Desktop Entry]\nName=GNU Image Manipulation Program\n"
        "Categories=Graphics;RasterGraphics;\n[Desktop Action new]\nName=New\n",
    )
    # The home copy wins over the system one.
    _desktop(system, "firefox.desktop", "[Desktop Entry]\nName=Old\n")
    _desktop(
        home,
        "firefox.desktop",
        "[Desktop Entry]\nName=Firefox\nCategories=Network;WebBrowser;\n",
    )
    _desktop(
        system,
        "xviewer.desktop",
        "[Desktop Entry]\nName=Image Viewer\nCategories=Utility;Viewer;\n",
    )
    # NoDisplay is "not in the menu", which Krita's per-format entry is.
    _desktop(
        system,
        "krita_png.desktop",
        "[Desktop Entry]\nName=Krita\nCategories=Office;Graphics;\nNoDisplay=true\n",
    )
    calls = []

    def fake_tool(argv, env=None):
        calls.append((argv, env))
        return GIO_OUTPUT

    monkeypatch.setattr(open_with, "_run_tool", fake_tool)
    apps = open_with.editors_first(open_with._list_linux("/home/me/sunset.png"))

    assert calls == [(["gio", "mime", "image/png"], {"LC_ALL": "C"})]
    assert [(a.name, a.default, a.editor) for a in apps] == [
        ("GNU Image Manipulation Program", False, True),
        ("Krita", False, True),
        ("Firefox", False, False),
        ("Image Viewer", True, False),
    ]
    assert apps[2].launch == os.path.join(home, "applications", "firefox.desktop")
    assert "launch" not in apps[0].public()


def test_a_hidden_desktop_entry_and_an_id_with_a_path_are_dropped(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "none"))
    _desktop(str(tmp_path), "gone.desktop", "[Desktop Entry]\nName=G\nHidden=true\n")
    assert (
        open_with.read_desktop_entry(open_with.find_desktop_file("gone.desktop"))
        is None
    )
    assert open_with.find_desktop_file("../gone.desktop") is None


def test_a_failing_tool_is_an_empty_list_and_asked_again_next_time(monkeypatch):
    monkeypatch.setattr(open_with.sys, "platform", "linux")
    monkeypatch.setattr(open_with, "_run_tool", lambda argv, env=None: None)
    assert open_with.list_apps("/home/me/a.png") == []
    # The tool recovers: the empty answer was not cached for the TTL.
    gimp = OpenWithApp(id="gimp.desktop", name="GIMP")
    monkeypatch.setattr(open_with, "_list_linux", lambda path: [gimp])
    assert open_with.list_apps("/home/me/a.png") == [gimp]


# ---- macOS and Windows: canned helper output ---------------------------------


def test_macos_json_marks_viewers_and_browsers():
    output = json.dumps(
        [
            {
                "id": "/Applications/Preview.app",
                "name": "Preview",
                "default": True,
                "role": "Viewer",
                "browser": False,
            },
            {
                "id": "/Applications/Pixelmator Pro.app",
                "name": "Pixelmator Pro",
                "default": False,
                "role": "Editor",
                "browser": False,
            },
            {
                "id": "/Applications/Safari.app",
                "name": "Safari",
                "default": False,
                "role": "",
                "browser": True,
            },
        ]
    )
    apps = open_with.editors_first(open_with.parse_macos_apps(output))
    assert [(a.name, a.default, a.editor) for a in apps] == [
        ("Pixelmator Pro", False, True),
        ("Preview", True, False),
        ("Safari", False, False),
    ]
    assert open_with.parse_macos_apps("not json") == []


def test_windows_lines_keep_only_browsers_apart():
    output = (
        "C:\\Windows\\system32\\mspaint.exe\tPaint\t0\t0\n"
        "Microsoft.Windows.Photos_8wekyb3d8bbwe!App\tPhotos\t1\t0\n"
        "C:\\Program Files\\Edge\\msedge.exe\tMicrosoft Edge\t0\t1\n"
        "malformed line\n"
    )
    apps = open_with.parse_windows_apps(output)
    assert [(a.id, a.name, a.default, a.editor) for a in apps] == [
        ("C:\\Windows\\system32\\mspaint.exe", "Paint", False, True),
        ("Microsoft.Windows.Photos_8wekyb3d8bbwe!App", "Photos", True, True),
        ("C:\\Program Files\\Edge\\msedge.exe", "Microsoft Edge", False, False),
    ]


def test_one_row_per_name_and_the_default_keeps_its_name():
    apps = [
        OpenWithApp(id="chromium_a.desktop", name="Chromium", editor=False),
        OpenWithApp(
            id="chromium_b.desktop", name="Chromium", default=True, editor=False
        ),
        OpenWithApp(id="gimp.desktop", name="GIMP"),
    ]
    assert [a.id for a in open_with.editors_first(apps)] == [
        "gimp.desktop",
        "chromium_b.desktop",
    ]


# ---- the app-id invariant ------------------------------------------------------


def test_only_a_listed_id_resolves_to_an_app(monkeypatch):
    listed = OpenWithApp(id="gimp.desktop", name="GIMP", launch="/usr/share/x.desktop")
    monkeypatch.setattr(open_with, "list_apps", lambda path: [listed])
    assert open_with.find_app("/home/me/a.png", "gimp.desktop") is listed
    for unlisted in ("", "/usr/bin/sh", "gimp", "GIMP"):
        assert open_with.find_app("/home/me/a.png", unlisted) is None


def test_launch_puts_the_file_in_argv_and_detaches(monkeypatch):
    calls = []
    monkeypatch.setattr(
        open_with.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw))
    )
    monkeypatch.setattr(open_with.sys, "platform", "linux")
    app = OpenWithApp(id="gimp.desktop", name="GIMP", launch="/usr/share/g.desktop")
    open_with.launch(app, "/home/me/a b; rm -rf x.png")
    ((argv, kwargs),) = calls
    assert argv == [
        "gio",
        "launch",
        "/usr/share/g.desktop",
        "/home/me/a b; rm -rf x.png",
    ]
    assert kwargs["start_new_session"] is True
    assert "shell" not in kwargs


# ---- edit-copy sessions ----------------------------------------------------------


def _picture(tmp_path, data=None):
    images = tmp_path / "images"
    images.mkdir(exist_ok=True)
    (images / "sunset.png").write_bytes(data or _png())
    return {
        "id": 7,
        "file_path": "sunset.png",
        "original_file_name": "sunset.png",
        "created_at": None,
    }, str(images)


def test_a_session_is_a_byte_identical_copy_and_a_readable_session_file(tmp_path):
    original = _png()
    picture, image_root = _picture(tmp_path, original)
    root = str(tmp_path / "edit-copies")
    made = create_session(
        picture, image_root, root, library_uuid="lib", app_name="GIMP"
    )

    assert os.path.basename(made["path"]) == "sunset.png"
    with open(made["path"], "rb") as fh:
        assert fh.read() == original
    session = read_session(os.path.dirname(made["path"]))
    assert session == {
        "picture_id": 7,
        "library_uuid": "lib",
        "file": "sunset.png",
        "seen": {"sunset.png": ImageUtils.calculate_hash_from_bytes(original)},
        "app": "GIMP",
    }
    assert os.path.basename(os.path.dirname(made["path"])) == made["session"]


def test_an_unreadable_session_file_is_none(tmp_path):
    (tmp_path / SESSION_FILE).write_text("{not json")
    assert read_session(str(tmp_path)) is None
    (tmp_path / SESSION_FILE).write_text(json.dumps({"picture_id": "7", "seen": {}}))
    assert read_session(str(tmp_path)) is None


def test_which_files_count_as_a_saved_edit(tmp_path):
    original, edited = _png(), _png((200, 0, 0))
    seen = ImageUtils.calculate_hash_from_bytes(original)
    for name, data in (
        ("same.png", original),
        (".hidden.png", edited),
        ("backup.png~", edited),
        ("notes.txt", edited),
        ("half.png", edited[: len(edited) // 2]),
        # A JPEG cut off mid-write still passes Image.verify().
        ("half.jpg", _jpeg()[: len(_jpeg()) * 2 // 3]),
    ):
        (tmp_path / name).write_bytes(data)
        assert changed_file(str(tmp_path / name), seen) is None, name
    (tmp_path / "a-dir.png").mkdir()
    assert changed_file(str(tmp_path / "a-dir.png"), None) is None

    # The extension follows the decoded format, not the name.
    (tmp_path / "edited.jpg").write_bytes(edited)
    data, ext, sha = changed_file(str(tmp_path / "edited.jpg"), seen)
    assert (data, ext, sha) == (
        edited,
        ".png",
        ImageUtils.calculate_hash_from_bytes(edited),
    )


def test_the_sweep_keeps_a_session_an_import_or_a_save_touched(tmp_path):
    picture, image_root = _picture(tmp_path)
    root = str(tmp_path / "edit-copies")
    made = [
        create_session(picture, image_root, root, library_uuid=None, app_name=None)
        for _ in range(3)
    ]
    day_ago = time.time() - edit_copies.EDIT_COPY_MAX_AGE_SECONDS - 60
    for session in made:
        folder = os.path.dirname(session["path"])
        for path in [folder, *(os.path.join(folder, n) for n in os.listdir(folder))]:
            os.utime(path, (day_ago, day_ago))
    stale, imported, saved = made
    imported_dir = os.path.dirname(imported["path"])
    edit_copies.write_session(imported_dir, read_session(imported_dir))
    # Saved over in place while nothing imported it: the folder stays old.
    with open(saved["path"], "wb") as fh:
        fh.write(_png((1, 2, 3)))
    os.utime(os.path.dirname(saved["path"]), (day_ago, day_ago))

    assert edit_copies.sweep_edit_sessions(root) == 1
    assert sorted(os.listdir(root)) == sorted([imported["session"], saved["session"]])


def test_a_sweep_that_cannot_read_a_session_logs_and_carries_on(tmp_path, monkeypatch):
    class Unreadable:
        name = "d" * 32
        path = str(tmp_path / name)

        def is_symlink(self):
            raise PermissionError("denied")

    monkeypatch.setattr(edit_copies.os, "scandir", lambda root: iter([Unreadable()]))
    assert edit_copies.sweep_edit_sessions(str(tmp_path)) == 0


def test_stop_waits_for_an_import_in_flight_and_runs_none_after(tmp_path):
    session = tmp_path / ("c" * 32)
    session.mkdir()
    started, release = threading.Event(), threading.Event()
    calls = []

    def on_settled(path):
        calls.append(path)
        started.set()
        release.wait(5)

    watcher = EditCopyWatcher(str(tmp_path), on_settled, debounce_s=0)
    watcher._schedule(session.name)
    assert started.wait(5)
    stopper = threading.Thread(target=watcher.stop)
    stopper.start()
    stopper.join(0.2)
    assert stopper.is_alive(), "stop() returned while an import was running"
    release.set()
    stopper.join(5)
    assert not stopper.is_alive()
    watcher._run(session.name)
    assert len(calls) == 1


def test_the_watcher_debounces_per_session_and_survives_a_raising_callback(tmp_path):
    root = str(tmp_path)
    session_a, session_b = "a" * 32, "b" * 32
    for name in (session_a, session_b):
        (tmp_path / name).mkdir()
    settled = []
    done = threading.Event()

    def on_settled(path):
        settled.append(path)
        if path.endswith(session_a):
            raise RuntimeError("boom")
        done.set()

    watcher = EditCopyWatcher(root, on_settled, debounce_s=0.01)
    handler = edit_copies._Handler(root, watcher._schedule)
    event = SimpleNamespace(event_type="modified", is_directory=False)
    for path in (
        os.path.join(root, session_a, "x.png"),
        os.path.join(root, session_a, "x.png"),
        os.path.join(root, "not-a-session", "x.png"),
    ):
        handler.on_any_event(
            SimpleNamespace(**vars(event), src_path=path, dest_path="")
        )
    handler.on_any_event(
        SimpleNamespace(
            event_type="opened",
            is_directory=False,
            src_path=os.path.join(root, session_b, "x.png"),
            dest_path="",
        )
    )
    # A move from another drive (Windows: no relative path) is skipped, not
    # raised into watchdog's thread.
    real_relpath = os.path.relpath

    def cross_drive(path, start=None):
        if str(path).startswith("D:"):
            raise ValueError("path is on mount 'D:', start on mount 'C:'")
        return real_relpath(path, start)

    edit_copies.os.path.relpath = cross_drive
    try:
        handler.on_any_event(
            SimpleNamespace(**vars(event), src_path="D:\\elsewhere\\t", dest_path="")
        )
    finally:
        edit_copies.os.path.relpath = real_relpath
    # A rename into the folder counts by its destination.
    handler.on_any_event(
        SimpleNamespace(
            **vars(event),
            src_path="/elsewhere/t",
            dest_path=os.path.join(root, session_b, "x.png"),
        )
    )
    assert done.wait(5)
    deadline = time.time() + 5
    while len(settled) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert sorted(settled) == sorted(
        [os.path.join(root, session_a), os.path.join(root, session_b)]
    )
    watcher.stop()
