"""`pixlstash-cli plugins` - install, test, list and remove.

No `Server` here on purpose: every one of these tests is a file copy and an
`ast.parse`, so the whole module runs in well under a second and never needs a
vault, a hub or a model.

`plugins test` is the one verb that *imports* the plugin, so its tests execute
a plugin's module body - the shipped `plugin_template.py`, which loads without
a model, and copies of it with one mistake spliced in.
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from pixlstash import cli, plugin_install, tagger_plugins
from pixlstash.plugin_install import CAPTIONING, IMAGE, PluginError

REPO_ROOT = Path(__file__).resolve().parent.parent

IMAGE_PLUGIN = """
from typing import Any

from pixlstash.image_plugins.base import ImagePlugin


class MyFilter(ImagePlugin):
    name = "my_filter"
    display_name = "My Filter"

    def parameter_schema(self) -> list[dict[str, Any]]:
        return []

    def run(self, images, parameters, progress_callback=None, error_callback=None):
        return images
"""

CAPTIONER = """
from typing import Any

from pixlstash.tagger_plugins.base import TaggerPlugin


class MyCaptioner(TaggerPlugin):
    name = "my_captioner"
    display_name = "My Captioner"
    supports_descriptions = True

    def parameter_schema(self) -> list[dict[str, Any]]:
        return []

    def needs_download(self, parameters=None) -> bool:
        return False

    def init(self, parameters) -> None:
        pass

    def unload(self) -> None:
        pass

    def is_loaded(self) -> bool:
        return True
"""


@pytest.fixture(autouse=True)
def plugin_root(tmp_path, monkeypatch):
    """Point both user plugin directories at a scratch folder."""
    root = tmp_path / "userdata"
    monkeypatch.setattr(plugin_install, "user_data_dir", lambda _app: str(root))
    return root


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _install(source: str, *extra: str) -> int:
    return cli.main(["plugins", "install", str(source), "--yes", *extra])


# ----------------------------------------------------------------------
# Where things land
# ----------------------------------------------------------------------


def test_single_module_image_plugin_is_named_after_the_plugin(tmp_path, plugin_root):
    source = _write(tmp_path / "Downloads" / "plugin(1).py", IMAGE_PLUGIN)

    assert _install(source) == cli.EXIT_OK

    installed = plugin_root / "image-plugins" / "user" / "my_filter.py"
    assert installed.is_file()
    assert "MyFilter" in installed.read_text(encoding="utf-8")


def test_captioning_folder_installs_as_a_folder(tmp_path, plugin_root):
    folder = tmp_path / "some_download"
    _write(folder / "__init__.py", CAPTIONER)
    _write(folder / "README.md", "hello")

    assert _install(folder) == cli.EXIT_OK

    installed = plugin_root / "tagger-plugins" / "user" / "my_captioner"
    assert (installed / "__init__.py").is_file()
    assert (installed / "README.md").is_file()


def test_captioning_single_module_installs_as_a_file(tmp_path, plugin_root):
    source = _write(tmp_path / "cap.py", CAPTIONER)

    assert _install(source) == cli.EXIT_OK

    user = plugin_root / "tagger-plugins" / "user"
    assert (user / "my_captioner.py").is_file()
    assert not (user / "my_captioner").is_dir()


def test_image_plugin_in_a_folder_installs_as_the_single_module(tmp_path, plugin_root):
    """The repository ships image plugins as a folder; only the .py may land."""
    folder = tmp_path / "hello_world_stamp"
    _write(folder / "hello_world_stamp.py", IMAGE_PLUGIN)
    _write(folder / "README.md", "hello")

    assert _install(folder) == cli.EXIT_OK

    user = plugin_root / "image-plugins" / "user"
    assert (user / "my_filter.py").is_file()
    assert not (user / "README.md").exists()


def test_installing_twice_needs_force(tmp_path, plugin_root):
    source = _write(tmp_path / "a.py", IMAGE_PLUGIN)
    assert _install(source) == cli.EXIT_OK
    assert _install(source) == cli.EXIT_REFUSED
    assert _install(source, "--force") == cli.EXIT_OK


def test_reinstalling_a_plugin_over_itself_never_destroys_it(tmp_path, plugin_root):
    """`install <the installed file> --force` used to delete it and then crash."""
    source = _write(tmp_path / "a.py", IMAGE_PLUGIN)
    assert _install(source) == cli.EXIT_OK
    installed = plugin_root / "image-plugins" / "user" / "my_filter.py"

    assert _install(installed, "--force") == cli.EXIT_REFUSED
    assert installed.is_file()
    assert "MyFilter" in installed.read_text(encoding="utf-8")


def test_reinstalling_a_folder_over_itself_never_destroys_it(tmp_path, plugin_root):
    folder = tmp_path / "pkg"
    _write(folder / "__init__.py", CAPTIONER)
    assert _install(folder) == cli.EXIT_OK
    installed = plugin_root / "tagger-plugins" / "user" / "my_captioner"

    assert _install(installed, "--force") == cli.EXIT_REFUSED
    assert (installed / "__init__.py").is_file()


def test_a_failed_copy_leaves_the_previous_plugin_in_place(
    tmp_path, plugin_root, monkeypatch
):
    """The staged-then-moved write: a mid-install failure must not lose the old one."""
    first = _write(tmp_path / "a.py", IMAGE_PLUGIN)
    assert _install(first) == cli.EXIT_OK
    installed = plugin_root / "image-plugins" / "user" / "my_filter.py"

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(plugin_install.shutil, "copy2", explode)
    second = _write(tmp_path / "b.py", IMAGE_PLUGIN.replace("My Filter", "Newer"))
    assert _install(second, "--force") == cli.EXIT_REFUSED
    assert installed.is_file()
    assert "My Filter" in installed.read_text(encoding="utf-8")


def test_force_replaces_the_other_shape_too(tmp_path, plugin_root):
    """A folder install must not leave the old single-module behind."""
    stale = plugin_root / "tagger-plugins" / "user" / "my_captioner.py"
    _write(stale, CAPTIONER)
    folder = tmp_path / "pkg"
    _write(folder / "__init__.py", CAPTIONER)

    assert _install(folder, "--force") == cli.EXIT_OK
    assert not stale.exists()
    assert (plugin_root / "tagger-plugins" / "user" / "my_captioner").is_dir()


# ----------------------------------------------------------------------
# Refusals
# ----------------------------------------------------------------------


def test_the_starter_template_is_refused(tmp_path, plugin_root):
    source = _write(tmp_path / "plugin_template.py", IMAGE_PLUGIN)
    assert _install(source) == cli.EXIT_REFUSED
    assert not (plugin_root / "image-plugins" / "user").exists()


def test_a_module_with_no_plugin_class_is_refused(tmp_path):
    source = _write(tmp_path / "notaplugin.py", "x = 1\n")
    assert _install(source) == cli.EXIT_REFUSED


def test_a_computed_name_is_refused(tmp_path):
    source = _write(
        tmp_path / "computed.py",
        IMAGE_PLUGIN.replace('name = "my_filter"', "name = 'my' + '_filter'"),
    )
    assert _install(source) == cli.EXIT_REFUSED


def test_a_non_snake_case_name_is_refused(tmp_path):
    source = _write(
        tmp_path / "shouty.py", IMAGE_PLUGIN.replace('"my_filter"', '"My Filter!"')
    )
    assert _install(source) == cli.EXIT_REFUSED


def test_claiming_both_kinds_is_refused(tmp_path):
    source = _write(
        tmp_path / "both.py",
        "from pixlstash.image_plugins.base import ImagePlugin\n"
        "from pixlstash.tagger_plugins.base import TaggerPlugin\n"
        "class Both(ImagePlugin, TaggerPlugin):\n"
        '    name = "both"\n',
    )
    assert _install(source) == cli.EXIT_REFUSED


def test_syntax_errors_are_a_refusal_not_a_traceback(tmp_path, capsys):
    source = _write(tmp_path / "broken.py", "class Nope(:\n")
    assert _install(source) == cli.EXIT_REFUSED
    assert "not valid Python" in capsys.readouterr().err


@pytest.mark.parametrize(
    "kind,plugin_name,template",
    [
        (IMAGE, "rotate", IMAGE_PLUGIN),
        (CAPTIONING, "wd14", CAPTIONER),
    ],
)
def test_a_built_in_name_is_refused(tmp_path, plugin_root, kind, plugin_name, template):
    source = _write(
        tmp_path / f"{plugin_name}.py",
        template.replace("my_filter", plugin_name).replace("my_captioner", plugin_name),
    )
    assert _install(source) == cli.EXIT_REFUSED
    assert plugin_name in plugin_install.builtin_names(kind)


def test_built_in_names_are_read_from_the_shipped_sources():
    """Not a hardcoded list: it must track what the registries actually load."""
    assert {"rotate", "scaling", "pixelate"} <= plugin_install.builtin_names(IMAGE)
    assert {"wd14", "florence2", "joycaption"} <= plugin_install.builtin_names(
        CAPTIONING
    )
    # The starter templates are excluded, or installing a copy would collide
    # with something that never loads.
    assert "plugin_template" not in plugin_install.builtin_names(IMAGE)


# ----------------------------------------------------------------------
# Warnings, and --strict
# ----------------------------------------------------------------------


def test_a_missing_abstract_method_warns_but_installs(tmp_path, plugin_root, capsys):
    source = _write(
        tmp_path / "half.py",
        IMAGE_PLUGIN.replace("    def run(", "    def not_run("),
    )
    assert _install(source) == cli.EXIT_OK
    assert "does not define run" in capsys.readouterr().err
    assert (plugin_root / "image-plugins" / "user" / "my_filter.py").is_file()


def test_strict_turns_that_warning_into_a_refusal(tmp_path, plugin_root):
    source = _write(
        tmp_path / "half.py",
        IMAGE_PLUGIN.replace("    def run(", "    def not_run("),
    )
    assert _install(source, "--strict") == cli.EXIT_REFUSED
    assert not (plugin_root / "image-plugins" / "user" / "my_filter.py").exists()


def test_two_image_plugin_classes_in_one_module_warn(tmp_path, capsys):
    source = _write(
        tmp_path / "two.py",
        IMAGE_PLUGIN + '\n\nclass Second(ImagePlugin):\n    name = "second"\n'
        "    def parameter_schema(self): return []\n"
        "    def run(self, images, parameters, progress_callback=None,"
        " error_callback=None): return images\n",
    )
    assert _install(source) == cli.EXIT_OK
    assert "ImagePlugin subclasses" in capsys.readouterr().err


def test_dropping_a_folder_image_plugin_s_siblings_warns(tmp_path, plugin_root, capsys):
    """Only the one module travels; say which files are being left behind."""
    folder = tmp_path / "cool_filter"
    _write(folder / "cool_filter.py", IMAGE_PLUGIN)
    _write(folder / "helpers.py", "KERNEL = 3\n")

    assert _install(folder) == cli.EXIT_OK
    assert "helpers.py" in capsys.readouterr().err
    assert not (plugin_root / "image-plugins" / "user" / "helpers.py").exists()


def test_strict_refuses_that_too(tmp_path, plugin_root):
    """`--strict` covers the plan-level warnings, not only the per-file ones."""
    folder = tmp_path / "cool_filter"
    _write(folder / "cool_filter.py", IMAGE_PLUGIN)
    _write(folder / "helpers.py", "KERNEL = 3\n")

    assert _install(folder, "--strict") == cli.EXIT_REFUSED
    assert not (plugin_root / "image-plugins").exists()


def test_a_non_utf8_file_is_a_refusal_not_a_traceback(tmp_path, plugin_root):
    source = tmp_path / "latin.py"
    source.write_bytes(IMAGE_PLUGIN.encode() + b"\n# caf\xe9\n")
    assert _install(source) == cli.EXIT_REFUSED


def test_a_non_utf8_file_does_not_break_the_whole_listing(plugin_root, capsys):
    """`plugins list` reports a bad entry; it must not die on one."""
    user = plugin_root / "image-plugins" / "user"
    _write(user / "good.py", IMAGE_PLUGIN)
    (user / "bad.py").write_bytes(b"# caf\xe9\n")

    assert cli.main(["plugins", "list"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "my_filter" in out
    assert "! bad" in out


def test_a_captioner_with_neither_capability_warns(tmp_path, capsys):
    source = _write(
        tmp_path / "silent.py",
        CAPTIONER.replace("    supports_descriptions = True\n", ""),
    )
    assert _install(source) == cli.EXIT_OK
    assert "appears in no table" in capsys.readouterr().err


def test_an_intermediate_base_class_still_resolves(tmp_path, plugin_root):
    """A class two steps from the base is still a plugin, and inherits its methods."""
    source = _write(
        tmp_path / "layered.py",
        IMAGE_PLUGIN.replace("class MyFilter(ImagePlugin):", "class Mid(ImagePlugin):")
        .replace('    name = "my_filter"\n', "")
        .replace('    display_name = "My Filter"\n', "")
        + '\n\nclass MyFilter(Mid):\n    name = "layered_filter"\n',
    )
    assert _install(source) == cli.EXIT_OK
    assert (plugin_root / "image-plugins" / "user" / "layered_filter.py").is_file()


# ----------------------------------------------------------------------
# Zip sources
# ----------------------------------------------------------------------


def test_a_zip_of_a_folder_installs(tmp_path, plugin_root):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("my_captioner/__init__.py", CAPTIONER)
        bundle.writestr("my_captioner/README.md", "hi")

    assert _install(archive) == cli.EXIT_OK
    assert (
        plugin_root / "tagger-plugins" / "user" / "my_captioner" / "__init__.py"
    ).is_file()


def test_a_zip_that_escapes_its_folder_is_refused(tmp_path, plugin_root, capsys):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("plugin/__init__.py", CAPTIONER)
        bundle.writestr("../../escaped.py", IMAGE_PLUGIN)

    assert _install(archive) == cli.EXIT_REFUSED
    # Named, not just refused: any invalid archive returns EXIT_REFUSED, so the
    # exit code alone would pass with the traversal check deleted.
    error = capsys.readouterr().err
    assert "../../escaped.py" in error and "outside" in error
    assert not (plugin_root / "tagger-plugins").exists()


def test_a_zip_holding_a_symlink_is_refused(tmp_path, capsys):
    archive = tmp_path / "linky.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        info = zipfile.ZipInfo("plugin/link.py")
        info.external_attr = 0o120777 << 16
        # Valid Python and a valid plugin, so nothing but the symlink check can
        # be what refuses it.
        bundle.writestr(info, IMAGE_PLUGIN)
        bundle.writestr("plugin/__init__.py", CAPTIONER)

    assert _install(archive) == cli.EXIT_REFUSED
    assert "symlink" in capsys.readouterr().err


# ----------------------------------------------------------------------
# The plugins repository
# ----------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        pass


# A published plugin as the repository actually ships one: a header declaring
# author and licence (issue #961) and a README whose first prose line says what
# it does. `plugins available` reads both, so the fixture has to carry both.
CREDITED_FILTER = IMAGE_PLUGIN.replace(
    'display_name = "My Filter"',
    'display_name = "My Filter"\n'
    '    author = "Ada <ada@example.com>"\n'
    '    license = "MIT"',
)

# Hard-wrapped exactly as the published READMEs are: the first *line* of the
# summary paragraph is a fragment, so anything reading line-by-line prints
# "...so it" and stops. The summary is the first sentence of the joined
# paragraph, never the first line.
FILTER_README = """\
# My Filter

Stamps every picture with a magenta watermark, so it
needs no model. Second sentence nobody needs.

A longer explanation nobody needs in a listing.
"""


@pytest.fixture
def fake_repository(monkeypatch, tmp_path):
    """Serve a zip shaped like a codeload download of the plugins repository."""
    requested = {}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("PixlStash-plugins-main/README.md", "hi")
        bundle.writestr(
            "PixlStash-plugins-main/plugins/image/my_filter/my_filter.py",
            CREDITED_FILTER,
        )
        bundle.writestr(
            "PixlStash-plugins-main/plugins/image/my_filter/README.md", FILTER_README
        )
        # Folder slug deliberately unlike the declared name, as the published
        # repository actually ships them (`moondream2_captioner` holding
        # `moondream2`): the catalogue prints the declared name, so installing
        # has to accept it.
        bundle.writestr(
            "PixlStash-plugins-main/plugins/captioning/my_captioner_plugin/__init__.py",
            CAPTIONER,
        )

    def fake_get(url, timeout=None):
        requested["url"] = url
        return _FakeResponse(archive.getvalue())

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    return requested


@pytest.fixture
def published(monkeypatch):
    """Serve a codeload zip holding exactly the files one test names.

    The shared `fake_repository` is the repository as it really is; this is for
    the shapes it cannot hold at once - a name collision, a folder that will not
    parse - without changing what every other test in the module sees.
    """

    def serve(files: dict[str, str]) -> None:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            for path, content in files.items():
                bundle.writestr(f"PixlStash-plugins-main/{path}", content)

        import requests

        monkeypatch.setattr(
            requests,
            "get",
            lambda url, timeout=None: _FakeResponse(archive.getvalue()),
        )

    return serve


def test_installing_a_named_plugin_from_the_repository(plugin_root, fake_repository):
    assert _install("my_filter") == cli.EXIT_OK
    assert (plugin_root / "image-plugins" / "user" / "my_filter.py").is_file()
    assert (
        fake_repository["url"]
        == f"https://codeload.github.com/{plugin_install.PLUGINS_REPO}/zip/main"
    )


def test_a_repository_folder_captioner_installs_as_a_folder(
    plugin_root, fake_repository
):
    """The declared name, which is what `plugins available` printed."""
    assert _install("my_captioner") == cli.EXIT_OK
    assert (
        plugin_root / "tagger-plugins" / "user" / "my_captioner" / "__init__.py"
    ).is_file()


def test_the_repository_folder_slug_still_installs(plugin_root, fake_repository):
    """The directory name was the only name accepted before; keep it working."""
    assert _install("my_captioner_plugin") == cli.EXIT_OK
    assert (
        plugin_root / "tagger-plugins" / "user" / "my_captioner" / "__init__.py"
    ).is_file()


def test_an_unknown_plugin_name_lists_what_the_repository_has(
    plugin_root, fake_repository, capsys
):
    """Listed under the names install takes, not the repository's own slugs."""
    assert _install("nosuch") == cli.EXIT_REFUSED
    error = capsys.readouterr().err
    # Exact, not a substring apiece: "my_captioner" is a prefix of the folder
    # slug, so `in error` would pass on a listing that still printed slugs.
    assert "Available: my_captioner, my_filter" in error


def test_a_declared_name_beats_another_plugin_s_folder_slug(plugin_root, published):
    """Whole-set precedence, not first-match: the slug owner sorts first here."""
    published(
        {
            "plugins/captioning/my_filter/__init__.py": CAPTIONER,
            "plugins/image/some_filter/my_filter.py": IMAGE_PLUGIN,
        }
    )
    assert _install("my_filter") == cli.EXIT_OK
    assert (plugin_root / "image-plugins" / "user" / "my_filter.py").is_file()
    assert not (plugin_root / "tagger-plugins" / "user" / "my_filter").exists()


def test_two_plugins_declaring_one_name_are_refused_not_chosen_between(
    plugin_root, published, capsys
):
    """Alphabetical order must not decide which downloaded code gets installed."""
    published(
        {
            "plugins/captioning/first/__init__.py": CAPTIONER,
            "plugins/captioning/second/__init__.py": CAPTIONER,
        }
    )
    assert _install("my_captioner") == cli.EXIT_REFUSED
    error = capsys.readouterr().err
    assert "captioning/first" in error and "captioning/second" in error
    assert not (plugin_root / "tagger-plugins" / "user").exists()


def test_a_published_folder_that_will_not_parse_blocks_nothing(
    plugin_root, published, capsys
):
    """It is one entry the reader cannot be offered; the rest still install."""
    published(
        {
            "plugins/image/broken/broken.py": "class Nope(:",
            "plugins/image/my_filter/my_filter.py": IMAGE_PLUGIN,
        }
    )
    assert _install("my_filter") == cli.EXIT_OK
    assert (plugin_root / "image-plugins" / "user" / "my_filter.py").is_file()

    # And it is still listed, under the only name it has left: its folder's.
    assert _install("nosuch") == cli.EXIT_REFUSED
    assert "broken" in capsys.readouterr().err


def test_a_ref_may_choose_a_branch(plugin_root, fake_repository):
    assert _install("my_filter", "--ref", "v1.2.3") == cli.EXIT_OK
    assert fake_repository["url"].endswith("/zip/v1.2.3")


@pytest.mark.parametrize(
    "ref",
    [
        "../../../someone-else/evil-plugins/zip/main",
        "..",
        "main?token=leak",
        "main#x",
        "https://evil.example/x",
    ],
)
def test_a_ref_cannot_steer_the_download_at_another_repository(
    plugin_root, fake_repository, ref
):
    """`requests` collapses dot segments, so an unchecked ref is arbitrary RCE."""
    assert _install("my_filter", "--ref", ref) == cli.EXIT_REFUSED
    assert "url" not in fake_repository
    assert not (plugin_root / "image-plugins").exists()


# ----------------------------------------------------------------------
# The published catalogue: `plugins available`
# ----------------------------------------------------------------------


def _available(*extra: str) -> int:
    return cli.main(["plugins", "available", *extra])


def test_available_lists_both_kinds_with_what_the_repository_declares(
    plugin_root, fake_repository, capsys
):
    assert _available() == cli.EXIT_OK
    out = capsys.readouterr().out

    assert "my_filter" in out and "my_captioner" in out
    assert "My Filter" in out
    # The first sentence of the joined paragraph: not the title, not the
    # hard-wrapped first line, not the second sentence, not the next paragraph.
    assert "Stamps every picture with a magenta watermark, so it needs no model." in out
    assert "Second sentence" not in out
    assert "A longer explanation" not in out
    assert "Ada <ada@example.com>" in out and "MIT" in out
    # It downloads the same archive `install` does, at the same default ref.
    assert fake_repository["url"].endswith("/zip/main")


def test_available_takes_the_same_ref_as_install(plugin_root, fake_repository):
    assert _available("--ref", "v1.2.3") == cli.EXIT_OK
    assert fake_repository["url"].endswith("/zip/v1.2.3")


@pytest.mark.parametrize("ref", ["../../../someone-else/evil/zip/main", ".."])
def test_available_refuses_a_ref_that_steers_the_download(
    plugin_root, fake_repository, ref
):
    """Listing shares the installer's download, so it shares its one guard."""
    assert _available("--ref", ref) == cli.EXIT_REFUSED
    assert "url" not in fake_repository


def test_a_search_word_matches_the_summary_not_just_the_name(
    plugin_root, fake_repository, capsys
):
    """A reader searches for the word they can see, wherever it appeared."""
    assert _available("watermark") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "my_filter" in out
    assert "my_captioner" not in out


def test_a_search_word_matches_the_author(plugin_root, fake_repository, capsys):
    assert _available("ada") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "my_filter" in out and "my_captioner" not in out


def test_a_word_matching_nothing_says_so_and_says_how_many_there_are(
    plugin_root, fake_repository, capsys
):
    """Distinct from an empty repository: one is "try again", one is "broken"."""
    assert _available("nosuchthing") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "nosuchthing" in out
    assert "all 2" in out


def test_an_empty_repository_is_not_reported_as_a_failed_search(
    plugin_root, monkeypatch, capsys
):
    monkeypatch.setattr(plugin_install, "catalogue", lambda _ref: [])
    assert _available("anything") == cli.EXIT_OK
    assert "publishes no plugins" in capsys.readouterr().out


def test_available_marks_what_is_already_installed(
    plugin_root, fake_repository, capsys
):
    assert _install("my_filter") == cli.EXIT_OK
    capsys.readouterr()

    assert _available() == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "* my_filter" in out
    assert "already installed" in out
    assert "* my_captioner" not in out


def test_one_broken_published_plugin_does_not_empty_the_listing(
    plugin_root, monkeypatch, capsys
):
    """The reader is choosing between the others; naming none of them is useless."""
    broken = plugin_install.CataloguePlugin(
        kind=plugin_install.IMAGE,
        name="bad_one",
        display_name="-",
        problem="no plugin class found",
    )
    good = plugin_install.CataloguePlugin(
        kind=plugin_install.IMAGE, name="good_one", display_name="Good One"
    )
    monkeypatch.setattr(plugin_install, "catalogue", lambda _ref: [broken, good])

    assert _available() == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "good_one" in out
    assert "bad_one" in out and "no plugin class found" in out


def test_the_catalogue_never_imports_a_published_plugin(
    plugin_root, fake_repository, monkeypatch
):
    """It reads code straight off the network, so ast is the whole safety story."""
    import importlib

    def explode(*_args, **_kwargs):
        raise AssertionError("plugins available must not import anything published")

    monkeypatch.setattr(importlib.util, "module_from_spec", explode)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", explode)

    assert _available() == cli.EXIT_OK


def test_matches_searches_every_field_the_listing_shows(plugin_root):
    entry = plugin_install.CataloguePlugin(
        kind=plugin_install.IMAGE,
        name="stamper",
        display_name="The Stamper",
        summary="Draws a mark",
        author="Ada",
        license="MIT",
    )
    for word in ("stamp", "STAMPER", "the stamper", "mark", "ada", "mit"):
        assert plugin_install.matches(entry, word), word
    assert not plugin_install.matches(entry, "captioning")
    # An empty query is a listing, not a search that matches nothing.
    assert plugin_install.matches(entry, "")
    assert plugin_install.matches(entry, "   ")


# ----------------------------------------------------------------------
# Dry run, confirmation and dependencies
# ----------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path, plugin_root, capsys):
    source = _write(tmp_path / "a.py", IMAGE_PLUGIN)
    assert cli.main(["plugins", "install", str(source), "--dry-run"]) == cli.EXIT_OK
    assert "Dry run" in capsys.readouterr().out
    assert not (plugin_root / "image-plugins").exists()


def test_declining_the_prompt_writes_nothing(tmp_path, plugin_root, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    source = _write(tmp_path / "a.py", IMAGE_PLUGIN)
    assert cli.main(["plugins", "install", str(source)]) == cli.EXIT_REFUSED
    assert not (plugin_root / "image-plugins").exists()


def test_requirements_are_never_installed_implicitly(
    tmp_path, plugin_root, monkeypatch, capsys
):
    calls = []
    monkeypatch.setattr(plugin_install, "install_requirements", calls.append)
    folder = tmp_path / "pkg"
    _write(folder / "__init__.py", CAPTIONER)
    _write(folder / "requirements.txt", "definitely-not-a-real-package==1.0\n")

    assert _install(folder) == cli.EXIT_OK
    assert calls == []
    assert "--with-deps" in capsys.readouterr().out


def test_with_deps_says_what_it_will_install(
    tmp_path, plugin_root, monkeypatch, pip_report, capsys
):
    """What is listed is what pip resolved, not what the file asked for.

    The two differ whenever a requirement pulls anything in, which is most of
    the time, and it is the resolved set that lands in the environment.
    """
    calls = []
    monkeypatch.setattr(plugin_install, "install_requirements", calls.append)
    pip_report(("something", "1.0"), ("a-dependency-of-it", "2.4"), installed={})
    folder = tmp_path / "pkg"
    _write(folder / "__init__.py", CAPTIONER)
    _write(folder / "requirements.txt", "# a comment\nsomething==1.0\n")

    assert _install(folder, "--with-deps") == cli.EXIT_OK
    output = capsys.readouterr().out
    assert "This operation will install the following Python packages:" in output
    assert "something" in output
    assert "a-dependency-of-it" in output
    assert len(calls) == 1


# ----------------------------------------------------------------------
# list and remove
# ----------------------------------------------------------------------


def test_list_groups_by_kind_and_marks_a_shadowed_built_in(
    tmp_path, plugin_root, capsys
):
    _write(
        plugin_root / "image-plugins" / "user" / "rotate.py",
        IMAGE_PLUGIN.replace("my_filter", "rotate"),
    )
    _write(plugin_root / "tagger-plugins" / "user" / "my_captioner.py", CAPTIONER)
    _write(plugin_root / "image-plugins" / "user" / "junk.py", "x = 1\n")

    assert cli.main(["plugins", "list"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Captioning plugins" in out and "Image filters" in out
    assert "* rotate" in out
    assert "! junk" in out
    assert "replaces a built-in" in out


def test_list_on_an_empty_machine_says_where_plugins_go(plugin_root, capsys):
    assert cli.main(["plugins", "list"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "No plugins are installed." in out
    assert str(plugin_root / "image-plugins" / "user") in out


def test_remove_deletes_the_file(tmp_path, plugin_root):
    installed = _write(
        plugin_root / "image-plugins" / "user" / "my_filter.py", IMAGE_PLUGIN
    )
    assert cli.main(["plugins", "remove", "my_filter", "--yes"]) == cli.EXIT_OK
    assert not installed.exists()


def test_remove_deletes_a_folder(plugin_root):
    installed = plugin_root / "tagger-plugins" / "user" / "my_captioner"
    _write(installed / "__init__.py", CAPTIONER)
    assert cli.main(["plugins", "remove", "my_captioner", "--yes"]) == cli.EXIT_OK
    assert not installed.exists()


def test_remove_says_the_built_in_comes_back(plugin_root, capsys):
    _write(
        plugin_root / "image-plugins" / "user" / "rotate.py",
        IMAGE_PLUGIN.replace("my_filter", "rotate"),
    )
    assert cli.main(["plugins", "remove", "rotate", "--yes"]) == cli.EXIT_OK
    assert "built-in rotate is in use again" in capsys.readouterr().out


def test_declining_the_remove_prompt_keeps_the_file(plugin_root, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    installed = _write(
        plugin_root / "image-plugins" / "user" / "my_filter.py", IMAGE_PLUGIN
    )
    assert cli.main(["plugins", "remove", "my_filter"]) == cli.EXIT_REFUSED
    assert installed.exists()


def test_remove_refuses_an_unknown_name(plugin_root, capsys):
    assert cli.main(["plugins", "remove", "nothing", "--yes"]) == cli.EXIT_REFUSED
    assert "no plugin called" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["../../secrets", "..", "a/b", "", "a\x00b"])
def test_remove_refuses_a_name_that_is_not_a_plain_entry(plugin_root, name):
    """Half the containment: a name may not address anything but a direct child."""
    with pytest.raises(PluginError):
        plugin_install.resolve_removal(name)


def test_remove_refuses_to_follow_a_symlink_out_of_the_plugin_directory(
    plugin_root, tmp_path, capsys
):
    """The other half, and the one that can actually delete somebody's file.

    Deliberately exercises the guard rather than the name prefilter: `escape`
    is a perfectly ordinary plugin name, and only the symlink check stands
    between it and the file it points at.
    """
    victim = _write(tmp_path / "elsewhere" / "important.txt", "keep me")
    user = plugin_root / "image-plugins" / "user"
    user.mkdir(parents=True)
    (user / "escape.py").symlink_to(victim)

    assert cli.main(["plugins", "remove", "escape", "--yes"]) == cli.EXIT_REFUSED
    assert "symlink" in capsys.readouterr().err
    assert victim.exists()
    assert victim.read_text(encoding="utf-8") == "keep me"


def test_a_stray_symlink_in_one_directory_does_not_block_the_other(
    plugin_root, tmp_path
):
    """A refusal in the captioning directory must not hide the image plugin."""
    _write(tmp_path / "elsewhere.txt", "x")
    tagger = plugin_root / "tagger-plugins" / "user"
    tagger.mkdir(parents=True)
    (tagger / "twin.py").symlink_to(tmp_path / "elsewhere.txt")
    _write(plugin_root / "image-plugins" / "user" / "twin.py", IMAGE_PLUGIN)

    kind, path = plugin_install.resolve_removal("twin", IMAGE)
    assert kind == IMAGE
    assert path == plugin_root / "image-plugins" / "user" / "twin.py"


def test_remove_asks_which_kind_when_both_hold_the_name(plugin_root, capsys):
    _write(plugin_root / "image-plugins" / "user" / "twin.py", IMAGE_PLUGIN)
    _write(plugin_root / "tagger-plugins" / "user" / "twin.py", CAPTIONER)

    assert cli.main(["plugins", "remove", "twin", "--yes"]) == cli.EXIT_REFUSED
    assert "--kind" in capsys.readouterr().err

    assert (
        cli.main(["plugins", "remove", "twin", "--kind", IMAGE, "--yes"]) == cli.EXIT_OK
    )
    assert not (plugin_root / "image-plugins" / "user" / "twin.py").exists()
    assert (plugin_root / "tagger-plugins" / "user" / "twin.py").exists()


# ----------------------------------------------------------------------
# plugins test
# ----------------------------------------------------------------------

#: The starter a contributor copies. Every check below is a real mistake made
#: to it, so the fixtures cannot drift away from what people actually write -
#: and the pass case guards the shipped template itself.
TEMPLATE = Path(tagger_plugins.__file__).parent / "plugin_template.py"


def _template(*mutations: tuple[str, str]) -> str:
    """Return the shipped template with each ``(old, new)`` spliced in.

    The count assertion is the point: a mutation that silently matched
    nothing - or matched a docstring instead of the code - would leave the
    test passing against an unbroken plugin and read as coverage.
    """
    source = TEMPLATE.read_text(encoding="utf-8")
    for old, new in mutations:
        assert source.count(old) == 1, f"{old!r} appears {source.count(old)} times"
        source = source.replace(old, new)
    return source


def _check(source, *extra: str) -> int:
    return cli.main(["plugins", "test", str(source), *extra])


@pytest.fixture(autouse=True)
def _drop_dynamic_modules():
    """Drop the namespaced modules a `plugins test` run executed."""
    yield
    for name in [n for n in sys.modules if n.startswith("pixlstash_user_tagger_")]:
        del sys.modules[name]


def test_the_shipped_template_passes(capsys):
    """The starter we hand people has to clear the checker we hand them."""
    assert _check(TEMPLATE) == cli.EXIT_OK

    out = capsys.readouterr().out
    assert 'Registered "my_captioner"' in out
    assert "max_tokens: integer = 128" in out


def test_it_says_it_is_not_a_security_check_before_running_anything(capsys):
    """Running the plugin *is* the mechanism, so the caveat cannot come after.

    By the time anything else is printed the plugin's module body has already
    executed, unsandboxed. A pass here is a contract check; a user reading it
    as "this plugin is safe to install" is the failure this line exists for.
    """
    assert _check(TEMPLATE) == cli.EXIT_OK

    out = capsys.readouterr().out
    assert "not a security check" in out
    assert "unsandboxed" in out
    assert out.index("not a security check") < out.index("Registered")


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (('"type": "textarea"', '"type": "text"'), "has type 'text'"),
        (('"default": 128,', ""), "has no 'default'"),
        (('"type": "integer"', '"type": "select"'), "select with no 'options'"),
        (
            ('"type": "integer"', '"type": "select", "options": []'),
            "empty; it renders as a dropdown with nothing to choose",
        ),
        (
            ('name = "my_captioner"', 'name = "florence2"'),
            "first-party plugin is already called",
        ),
        (
            ("class MyCaptioner(TaggerPlugin):", "class MyCaptioner:"),
            "No TaggerPlugin subclass found",
        ),
        (
            ("import TaggerPlugin\n", "import TaggerPlugn\n"),
            "TaggerPlugn",
        ),
    ],
)
def test_a_mistake_in_a_copy_of_the_template_is_caught(
    tmp_path, capsys, mutation, expected
):
    source = _write(tmp_path / "mine.py", _template(mutation))

    assert _check(source) == cli.EXIT_REFUSED
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (('"label": "Max tokens",', ""), "has no 'label'"),
        (
            ("    supports_descriptions = True", "    supports_descriptions = False"),
            "neither supports_tags nor supports_descriptions",
        ),
    ],
)
def test_a_cosmetic_fault_is_reported_without_failing_the_command(
    tmp_path, capsys, mutation, expected
):
    """These plugins load and work, so refusing them would be the worse bug.

    A missing `label` renders the parameter's `name` (`field.label ||
    field.name`), and a plugin with no capability flag registers exactly as
    written - it is simply never reached. `plugins install` warns about the
    second and installs it; this agrees with that rather than inventing a
    stricter contract for the same plugin.
    """
    source = _write(tmp_path / "mine.py", _template(mutation))

    assert _check(source) == cli.EXIT_OK

    err = capsys.readouterr().err
    assert expected in err
    assert "warning:" in err
    assert "problem:" not in err


def test_a_folder_plugin_loads_with_its_own_helpers(tmp_path, capsys):
    """Proves this goes through the server's loader: a bare exec_module fails."""
    package = tmp_path / "bundled"
    _write(package / "helper.py", "TOKENS = 128\n")
    _write(
        package / "__init__.py",
        _template(
            (
                "from pixlstash.tagger_plugins.base import TaggerPlugin",
                "from pixlstash.tagger_plugins.base import TaggerPlugin\n"
                "from . import helper\n"
                "assert helper.TOKENS == 128",
            )
        ),
    )

    assert _check(package) == cli.EXIT_OK
    assert 'Registered "my_captioner"' in capsys.readouterr().out


def test_a_folder_without_an_init_is_refused(tmp_path, capsys):
    """The server skips such a folder without a message."""
    (tmp_path / "bundled").mkdir()

    assert _check(tmp_path / "bundled") == cli.EXIT_REFUSED
    assert "no __init__.py" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["_mine.py", ".mine.py"])
def test_a_name_discovery_skips_is_refused_however_well_it_loads(
    tmp_path, capsys, name
):
    """The scan filters the directory listing above the loader they share.

    So this file imports and registers perfectly here and is never looked at
    by the server - silently, which is the failure the command exists for.
    """
    source = _write(tmp_path / name, _template())

    assert _check(source) == cli.EXIT_REFUSED
    assert (
        "the server skips any entry whose name starts with" in capsys.readouterr().err
    )


def test_a_name_an_installed_plugin_already_holds_is_a_problem(
    tmp_path, plugin_root, capsys
):
    """One of the two silently loses; the checker cannot see it from the load.

    This manager scans no directory on purpose, so the server's own duplicate
    refusal can never fire here - the installed names are read statically
    instead, without importing anybody's plugin.
    """
    _write(plugin_root / "tagger-plugins" / "user" / "my_captioner.py", CAPTIONER)
    source = _write(tmp_path / "mine.py", _template())

    assert _check(source) == cli.EXIT_REFUSED
    assert "an installed plugin is already called" in capsys.readouterr().err


def test_a_plugin_does_not_collide_with_its_own_installed_copy(plugin_root, capsys):
    """Checking a plugin where it is installed is the obvious thing to try."""
    installed = _write(
        plugin_root / "tagger-plugins" / "user" / "my_captioner.py", _template()
    )

    assert _check(installed) == cli.EXIT_OK
    assert "already called" not in capsys.readouterr().err


def test_an_image_filter_is_told_it_is_the_wrong_kind(tmp_path, capsys):
    """ "No TaggerPlugin subclass found" is true and useless on its own."""
    source = _write(tmp_path / "my_filter.py", IMAGE_PLUGIN)

    assert _check(source) == cli.EXIT_REFUSED
    assert "image filter, not a captioning plugin" in capsys.readouterr().err


def test_image_runs_the_plugin_over_a_picture(tmp_path, capsys):
    """End to end: defaults merged, setup() given a device, init() before the call.

    The template is instrumented rather than run as shipped, because as shipped
    it cannot fail this. It seeds ``self._device = "cpu"`` in ``__init__`` and
    falls back to ``max_tokens or 128``, so a caption reading
    ``(128 tokens, cpu)`` is exactly what you get when ``setup()``, ``init()``
    and the merged defaults are all skipped - every assertion here passed with
    each of those three deleted from plugin_check until the sentinels below
    were spliced in.
    """
    source = _write(
        tmp_path / "mine.py",
        _template(
            ('self._device = "cpu"', 'self._device = "setup-not-called"'),
            ("self._model = object()", 'self._model = "init-called"'),
            ('"default": 128,', '"default": 64,'),
            (
                'f"{prompt} ({max_tokens} tokens, {self._device})"',
                'f"{prompt} ({max_tokens} tokens, {self._device}, {self._model})"',
            ),
        ),
    )
    image = _write(tmp_path / "sample.jpg", "not really a jpeg")

    assert _check(source, "--image", str(image)) == cli.EXIT_OK

    out = capsys.readouterr().out
    assert str(image) in out
    # 64 proves the schema's default was merged rather than the plugin's own
    # `or 128` fallback; init-called proves init() ran; the device proves
    # setup() was handed one.
    assert "(64 tokens, cuda, init-called)" in out or (
        "(64 tokens, cpu, init-called)" in out
    )


def test_a_result_not_keyed_by_the_paths_it_was_given_is_caught(tmp_path, capsys):
    """The workflow looks its results up by path and would drop these."""
    source = _write(
        tmp_path / "mine.py",
        _template(('results[path] = f"', 'results["elsewhere"] = f"')),
    )
    image = _write(tmp_path / "sample.jpg", "not really a jpeg")

    assert _check(source, "--image", str(image)) == cli.EXIT_REFUSED
    assert "not the path it was given" in capsys.readouterr().err


def test_image_loads_no_model_for_a_plugin_nothing_will_ever_call(tmp_path, capsys):
    """No capability flag means no method to call, so there is nothing to load.

    `init()` is made to raise, so the run reports `init() raised` and fails the
    command if it is reached at all - which it was, before the capability check
    moved above the download-and-init pair.
    """
    source = _write(
        tmp_path / "mine.py",
        _template(
            ("    supports_descriptions = True", "    supports_descriptions = False"),
            (
                "self._model = object()",
                'raise AssertionError("init() must not run for a flagless plugin")',
            ),
        ),
    )
    image = _write(tmp_path / "sample.jpg", "not really a jpeg")

    assert _check(source, "--image", str(image)) == cli.EXIT_OK

    err = capsys.readouterr().err
    assert "init() raised" not in err
    assert "neither supports_tags nor supports_descriptions" in err


def test_a_torch_that_will_not_answer_does_not_take_the_command_down(monkeypatch):
    """The plugin's own init() gives a better error than a traceback from here.

    An installed-but-unloadable torch raises `OSError` rather than
    `ImportError` - a missing CUDA shared library is the usual way - so the
    narrower catch let it escape and end the command before the plugin could
    report its own missing dependency.
    """
    from pixlstash import plugin_check

    class Unloadable:
        @property
        def cuda(self):
            raise OSError("libcuda.so.1: cannot open shared object file")

    monkeypatch.setitem(sys.modules, "torch", Unloadable())

    assert plugin_check._device() == "cpu"


def test_image_stops_when_the_plugin_says_its_model_is_missing(tmp_path, capsys):
    """A check command has no business starting a multi-gigabyte fetch.

    This is the courtesy, not a guarantee: `needs_download()` is the plugin's
    own answer, and a plugin that downloads inside `init()` - which is where
    `from_pretrained_local_first` does it - is past this gate already.
    """
    source = _write(
        tmp_path / "mine.py", _template(("        return False", "        return True"))
    )
    image = _write(tmp_path / "sample.jpg", "not really a jpeg")

    assert _check(source, "--image", str(image)) == cli.EXIT_REFUSED

    captured = capsys.readouterr()
    assert "Stopping rather than fetching them" in captured.err
    assert "Ran over" not in captured.out


def test_schema_types_match_the_component_that_renders_them():
    """Guardrail: `SCHEMA_TYPES` is a hand-copy of a `v-else-if` chain in Vue.

    The whole value of the schema check is that it agrees with what actually
    renders, and nothing else in this repository would notice the two drifting
    - a type added to the component would be reported here as unrenderable,
    and one removed from it would sail through. Pinned the way
    `_SUBDIRS` is pinned to the registries.

    `string` is not in the component by name: it is the `v-else`, which is
    exactly why an unlisted type is a text box rather than an error.
    """
    from pixlstash.plugin_check import SCHEMA_TYPES

    component = (
        REPO_ROOT
        / "frontend"
        / "src"
        / "components"
        / "widgets"
        / "TaggerParametersUI.vue"
    ).read_text(encoding="utf-8")
    rendered = set(re.findall(r"field\.type === ['\"]([^'\"]+)['\"]", component))

    assert rendered, "the comparison in TaggerParametersUI.vue changed shape"
    assert set(SCHEMA_TYPES) == rendered | {"string"}


# ----------------------------------------------------------------------
# Wiring
# ----------------------------------------------------------------------


def test_the_plugin_verbs_never_open_the_hub(tmp_path, plugin_root, monkeypatch):
    """A machine that has never started the server has no hub to open."""

    def explode(*_args, **_kwargs):
        raise AssertionError("the plugin verbs must not open the hub")

    monkeypatch.setattr(cli, "HubDatabase", explode)
    source = _write(tmp_path / "a.py", IMAGE_PLUGIN)
    assert _install(source) == cli.EXIT_OK
    assert cli.main(["plugins", "list"]) == cli.EXIT_OK
    assert cli.main(["plugins", "remove", "my_filter", "--yes"]) == cli.EXIT_OK


def test_the_installer_writes_where_the_registries_read():
    """Guardrail: the two paths are duplicated, so pin them to the registries.

    Built from the real ``user_data_dir`` rather than through
    ``plugin_install.user_dir``, which the autouse fixture has redirected.
    """
    from platformdirs import user_data_dir

    from pixlstash.image_plugins.registry import (
        user_plugin_dir as image_user_plugin_dir,
    )
    from pixlstash.tagger_plugins.registry import user_plugin_dir

    root = user_data_dir("pixlstash")
    assert os.path.join(root, *plugin_install._SUBDIRS[CAPTIONING]) == user_plugin_dir()
    assert (
        os.path.join(root, *plugin_install._SUBDIRS[IMAGE]) == image_user_plugin_dir()
    )


# ----------------------------------------------------------------------
# The plugin header
# ----------------------------------------------------------------------


def _header_literals(path: Path) -> dict[str, dict[str, object]]:
    """Return ``{class_name: {attr: value}}`` for the header of each class.

    Reads the source the way a tool outside PixlStash has to - ``ast`` only,
    no import - so an attribute computed at runtime shows up as absent here.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    headers: dict[str, dict[str, object]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        found: dict[str, object] = {}
        for statement in node.body:
            target = None
            if isinstance(statement, ast.AnnAssign) and statement.value is not None:
                target = statement.target
            elif isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if target.id in ("name", "author", "license", "models"):
                try:
                    found[target.id] = ast.literal_eval(statement.value)
                except ValueError:
                    # Computed rather than declared: nothing to read without
                    # importing the module, so it is absent as far as this goes.
                    continue
        # An empty `name` is an abstract intermediate (the base classes), which
        # `_analyse` skips for the same reason: it is nobody's plugin.
        if found.get("name"):
            headers[node.name] = found
    return headers


def _shipped_plugin_sources() -> list[Path]:
    """Every file that ships a plugin class, enumerated like ``builtin_names``.

    The two templates are in here as well: they are what a third-party author
    copies, so a header missing from one is a header missing from every plugin
    written after it.
    """
    from pixlstash.tagger_plugins.registry import _FIRST_PARTY_PLUGINS

    package = Path(plugin_install.__file__).parent
    sources = sorted(
        {
            *(
                package / "tagger_plugins" / f"{module.rsplit('.', 1)[1]}.py"
                for module, _class_name in _FIRST_PARTY_PLUGINS
            ),
            *(package / "image_plugins" / "built-in").glob("*.py"),
            package / "tagger_plugins" / "plugin_template.py",
        }
    )
    # Parametrising over an empty list is `1 skipped` and exit 0 - the glob
    # coming back empty (a renamed folder, a packaging change) would drop every
    # image plugin from this check without failing anything. Count instead.
    assert len(sources) >= 12, f"only found {len(sources)} shipped plugin sources"
    assert all(path.is_file() for path in sources), sources
    return sources


@pytest.mark.parametrize("source", _shipped_plugin_sources(), ids=lambda p: p.name)
def test_every_shipped_plugin_declares_a_readable_header(source):
    """author/license/models are literals a tool can read without importing."""
    headers = _header_literals(source)
    assert headers, f"{source.name} declares no plugin class"
    for class_name, header in headers.items():
        where = f"{class_name} in {source.name}"
        assert header.get("author"), f"{where} declares no author"
        assert header.get("license"), f"{where} declares no license"
        models = header.get("models")
        assert isinstance(models, list), f"{where} declares no models list"
        for model in models:
            assert isinstance(model, dict), f"{where} has a non-dict models entry"
            assert model.get("name"), f"{where} has a models entry with no name"
            assert model.get("license"), f"{where} has a models entry with no license"


def _stub_plugins():
    """Return one minimal subclass of each base, declaring nothing extra."""
    from pixlstash.image_plugins.base import ImagePlugin
    from pixlstash.tagger_plugins.base import TaggerPlugin

    class StubFilter(ImagePlugin):
        name = "stub_filter"

        def parameter_schema(self):
            return []

        def run(self, images, parameters=None, **_kwargs):
            return images

    class StubCaptioner(TaggerPlugin):
        name = "stub_captioner"

        def parameter_schema(self):
            return []

        def needs_download(self, parameters=None):
            return False

        def init(self, parameters):
            pass

        def unload(self):
            pass

        def is_loaded(self):
            return False

    return StubFilter, StubCaptioner


def test_the_header_defaults_let_a_plugin_omit_it():
    """Omitting all three still loads; the schema just carries empty values."""
    for stub in _stub_plugins():
        schema = stub().plugin_schema()
        assert schema["author"] == ""
        assert schema["license"] == ""
        assert schema["models"] == []


def test_plugin_schema_forwards_the_header():
    """Both `plugin_schema()` implementations carry the header to the registry."""
    for stub in _stub_plugins():
        stub.author = "Someone <someone@example.com>"
        stub.license = "MIT"
        stub.models = [{"name": "example/model", "license": "Apache-2.0"}]
        schema = stub().plugin_schema()
        assert schema["author"] == "Someone <someone@example.com>"
        assert schema["license"] == "MIT"
        assert schema["models"] == [{"name": "example/model", "license": "Apache-2.0"}]


def test_the_schema_never_hands_out_the_declared_models_list():
    """A caller mutating what it got back must not rewrite the declaration."""
    for stub in _stub_plugins():
        stub.models = [{"name": "example/model", "license": "MIT"}]
        schema = stub().plugin_schema()
        schema["models"].append({"name": "not/declared", "license": "Proprietary"})
        schema["models"][0]["license"] = "Proprietary"
        assert stub.models == [{"name": "example/model", "license": "MIT"}]


def test_the_cli_names_itself_the_way_it_was_actually_invoked(
    plugin_root, capsys, monkeypatch
):
    """A desktop launcher declares the working command; the CLI must use it.

    ``pixlstash-cli`` is sealed inside the app image and on nobody's PATH there,
    so a hint printing that name sends the reader to a command they do not have.
    """
    monkeypatch.setenv("PIXLSTASH_CLI_COMMAND", "pixlstash")

    assert cli.main(["plugins", "list"]) == cli.EXIT_OK
    printed = capsys.readouterr().out
    assert "pixlstash plugins install" in printed
    assert "pixlstash-cli" not in printed

    # Usage and error lines come from argparse's prog, so they follow too.
    assert cli.build_parser().prog == "pixlstash"


def test_without_a_declaration_the_console_script_is_still_the_name(monkeypatch):
    """Every non-desktop deployment is untouched by the above."""
    monkeypatch.delenv("PIXLSTASH_CLI_COMMAND", raising=False)
    assert cli.invoked_as() == "pixlstash-cli"

    # An empty declaration must not produce a prog of "" or a leading space.
    monkeypatch.setenv("PIXLSTASH_CLI_COMMAND", "  ")
    assert cli.invoked_as() == "pixlstash-cli"


def test_the_windows_desktop_derives_its_name_since_nothing_declares_one(
    monkeypatch, tmp_path
):
    """Issue #1058: on Windows the app declares nothing, on purpose.

    The command that works there is this very interpreter, so it is derived
    rather than announced - and through the same helper that fills the Settings
    panel, so the panel and the CLI's own output cannot drift apart.
    """
    resources = tmp_path / "resources"
    (resources / "python").mkdir(parents=True)
    interpreter = resources / "python" / "python.exe"
    interpreter.write_text("")
    (resources / "runtime.json").write_text("{}")
    hub = "C:\\Users\\me\\AppData\\Roaming\\PixlStash\\hub.db"

    monkeypatch.delenv("PIXLSTASH_CLI_COMMAND", raising=False)
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(cli.sys, "executable", str(interpreter))
    monkeypatch.setattr(cli.sys, "argv", ["pixlstash.cli", "--hub", hub, "libraries"])

    name = cli.invoked_as()
    assert name == f"& '{interpreter}' -m pixlstash.cli --hub '{hub}'"
    assert "pixlstash-cli" not in name, "that console script is on no PATH there"

    # argparse's prog is built before any parsing, which is why the hub is read
    # off sys.argv rather than from parsed arguments.
    assert cli.build_parser().prog == name


def test_the_hub_is_read_off_the_command_line_in_both_spellings(monkeypatch):
    """``--hub X`` and ``--hub=X`` are the same flag to argparse."""
    monkeypatch.setattr(cli.sys, "argv", ["x", "--hub", "/a/hub.db", "libraries"])
    assert cli._hub_from_argv() == "/a/hub.db"

    monkeypatch.setattr(cli.sys, "argv", ["x", "--hub=/b/hub.db", "libraries"])
    assert cli._hub_from_argv() == "/b/hub.db"

    # A trailing --hub with no value is argparse's error to report, not ours.
    monkeypatch.setattr(cli.sys, "argv", ["x", "libraries", "list"])
    assert cli._hub_from_argv() is None
    monkeypatch.setattr(cli.sys, "argv", ["x", "--hub"])
    assert cli._hub_from_argv() is None


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------
#
# `resolve_requirements` shells out to pip, which needs a network and an index,
# so the resolver itself is stubbed and what is tested is the rule applied to
# its answer. The one thing that must not be stubbed away is the shape of the
# report, so `_report` builds the real thing: pip's `--report` JSON, whose
# `install` list holds only what is *not* already satisfied.


def _report(*packages: tuple[str, str]) -> str:
    """Return a pip install report naming *packages* as (name, version)."""
    return json.dumps(
        {
            "install": [
                {"metadata": {"name": name, "version": version}}
                for name, version in packages
            ]
        }
    )


@pytest.fixture
def pip_report(monkeypatch, tmp_path):
    """Serve a canned pip `--report`, and record what pip was asked."""

    def serve(*packages: tuple[str, str], installed: dict[str, str] | None = None):
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            index = command.index("--report")
            Path(command[index + 1]).write_text(_report(*packages), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        def fake_version(name: str) -> str:
            present = installed or {}
            if name in present:
                return present[name]
            raise plugin_install.PackageNotFoundError(name)

        monkeypatch.setattr(plugin_install.subprocess, "run", fake_run)
        monkeypatch.setattr(plugin_install, "metadata_version", fake_version)
        return calls

    return serve


def test_a_new_package_is_an_addition(pip_report, tmp_path):
    pip_report(("flask", "3.1.3"), installed={})
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("flask\n", encoding="utf-8")

    changes = plugin_install.resolve_requirements(requirements)
    assert [(c.name, c.version, c.installed) for c in changes] == [
        ("flask", "3.1.3", None)
    ]
    assert not changes[0].moves


def test_a_different_version_of_an_installed_package_moves_it(pip_report, tmp_path):
    """The one case that can stop PixlStash starting."""
    pip_report(("pillow", "11.0.0"), installed={"pillow": "12.3.0"})
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pillow==11.0.0\n", encoding="utf-8")

    (change,) = plugin_install.resolve_requirements(requirements)
    assert change.moves
    assert change.installed == "12.3.0"


def test_transitive_packages_are_reported_too(pip_report, tmp_path):
    """A plugin asking for one package routinely pulls in several."""
    pip_report(
        ("Flask", "3.1.3"),
        ("Werkzeug", "3.1.8"),
        ("blinker", "1.9.0"),
        installed={},
    )
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("flask\n", encoding="utf-8")

    changes = plugin_install.resolve_requirements(requirements)
    assert [c.name for c in changes] == ["blinker", "Flask", "Werkzeug"]


def test_resolving_asks_pip_not_to_install_anything(pip_report, tmp_path):
    """It runs before the plugin is copied, so it must change nothing."""
    calls = pip_report(("flask", "3.1.3"), installed={})
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("flask\n", encoding="utf-8")

    plugin_install.resolve_requirements(requirements)
    assert "--dry-run" in calls[0]
    assert calls[0][0] == sys.executable


def test_a_pip_that_cannot_resolve_is_a_refusal_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(
        plugin_install.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="ERROR: no matching distribution\n"
        ),
    )
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("nope==1\n", encoding="utf-8")

    with pytest.raises(PluginError, match="no matching distribution"):
        plugin_install.resolve_requirements(requirements)


def test_install_refuses_dependencies_that_replace_a_package_in_use(
    tmp_path, plugin_root, pip_report, capsys
):
    """End to end: the plugin must not be copied when the deps are refused."""
    source = tmp_path / "dep_filter"
    source.mkdir()
    (source / "dep_filter.py").write_text(IMAGE_PLUGIN, encoding="utf-8")
    (source / "requirements.txt").write_text("pillow==11.0.0\n", encoding="utf-8")
    pip_report(("pillow", "11.0.0"), installed={"pillow": "12.3.0"})

    exit_code = cli.main(["plugins", "install", str(source), "--with-deps", "--yes"])
    assert exit_code != 0

    captured = capsys.readouterr()
    assert "This operation will install the following Python packages:" in captured.out
    assert "replaces 12.3.0" in captured.out
    assert "Refused" in captured.err
    # Nothing copied: the refusal happens before the plugin is written, so
    # there is no half-installed plugin to clean up.
    assert not (plugin_install.user_dir(IMAGE) / "my_filter.py").exists()


def test_force_deps_installs_anyway_and_says_so(
    tmp_path, plugin_root, pip_report, monkeypatch, capsys
):
    """The owner's machine, the owner's call, once they have been told."""
    source = tmp_path / "dep_filter"
    source.mkdir()
    (source / "dep_filter.py").write_text(IMAGE_PLUGIN, encoding="utf-8")
    (source / "requirements.txt").write_text("pillow==11.0.0\n", encoding="utf-8")
    pip_report(("pillow", "11.0.0"), installed={"pillow": "12.3.0"})
    installed: list[Path] = []
    monkeypatch.setattr(
        plugin_install, "install_requirements", lambda path: installed.append(path)
    )

    exit_code = cli.main(
        ["plugins", "install", str(source), "--with-deps", "--force-deps", "--yes"]
    )
    assert exit_code == cli.EXIT_OK
    assert "Proceeding anyway: --force-deps." in capsys.readouterr().out
    assert installed and (plugin_install.user_dir(IMAGE) / "my_filter.py").exists()


def test_a_plugin_whose_dependencies_are_all_present_says_so(
    tmp_path, plugin_root, pip_report, monkeypatch, capsys
):
    source = tmp_path / "dep_filter"
    source.mkdir()
    (source / "dep_filter.py").write_text(IMAGE_PLUGIN, encoding="utf-8")
    (source / "requirements.txt").write_text("pillow\n", encoding="utf-8")
    pip_report(installed={"pillow": "12.3.0"})
    monkeypatch.setattr(plugin_install, "install_requirements", lambda path: None)

    assert cli.main(["plugins", "install", str(source), "--with-deps", "--yes"]) == 0
    assert "Everything it needs is already installed." in capsys.readouterr().out


def test_without_with_deps_pip_is_never_consulted(
    tmp_path, plugin_root, monkeypatch, capsys
):
    """Resolution costs a network round trip, so it is not done unasked."""
    source = tmp_path / "dep_filter"
    source.mkdir()
    (source / "dep_filter.py").write_text(IMAGE_PLUGIN, encoding="utf-8")
    (source / "requirements.txt").write_text("flask\n", encoding="utf-8")

    def explode(*_args, **_kwargs):
        raise AssertionError("pip was consulted without --with-deps")

    monkeypatch.setattr(plugin_install, "resolve_requirements", explode)
    assert cli.main(["plugins", "install", str(source), "--yes"]) == 0
    assert "It is NOT installed" in capsys.readouterr().out
