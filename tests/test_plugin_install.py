"""`pixlstash-cli plugins` — install, list and remove.

No `Server` here on purpose: every one of these tests is a file copy and an
`ast.parse`, so the whole module runs in well under a second and never needs a
vault, a hub or a model.
"""

from __future__ import annotations

import ast
import io
import os
import zipfile
from pathlib import Path

import pytest

from pixlstash import cli, plugin_install
from pixlstash.plugin_install import CAPTIONING, IMAGE, PluginError

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


@pytest.fixture
def fake_repository(monkeypatch, tmp_path):
    """Serve a zip shaped like a codeload download of the plugins repository."""
    requested = {}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("PixlStash-plugins-main/README.md", "hi")
        bundle.writestr(
            "PixlStash-plugins-main/plugins/image/my_filter/my_filter.py", IMAGE_PLUGIN
        )
        bundle.writestr(
            "PixlStash-plugins-main/plugins/captioning/my_captioner/__init__.py",
            CAPTIONER,
        )

    def fake_get(url, timeout=None):
        requested["url"] = url
        return _FakeResponse(archive.getvalue())

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    return requested


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
    assert _install("my_captioner") == cli.EXIT_OK
    assert (
        plugin_root / "tagger-plugins" / "user" / "my_captioner" / "__init__.py"
    ).is_file()


def test_an_unknown_plugin_name_lists_what_the_repository_has(
    plugin_root, fake_repository, capsys
):
    assert _install("nosuch") == cli.EXIT_REFUSED
    error = capsys.readouterr().err
    assert "my_filter" in error and "my_captioner" in error


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
    tmp_path, plugin_root, monkeypatch, capsys
):
    calls = []
    monkeypatch.setattr(plugin_install, "install_requirements", calls.append)
    folder = tmp_path / "pkg"
    _write(folder / "__init__.py", CAPTIONER)
    _write(folder / "requirements.txt", "# a comment\nsomething==1.0\n")

    assert _install(folder, "--with-deps") == cli.EXIT_OK
    assert "something==1.0" in capsys.readouterr().out
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

    Reads the source the way a tool outside PixlStash has to — ``ast`` only,
    no import — so an attribute computed at runtime shows up as absent here.
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
    # Parametrising over an empty list is `1 skipped` and exit 0 — the glob
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
