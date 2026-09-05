"""User-supplied tagger plugin discovery.

Deliberately constructed with ``first_party=[]`` so no heavy first-party
plugin (and therefore no torch) is ever imported - that keeps this file cheap
enough to block the gate.
"""

from __future__ import annotations

import os
import sys
import textwrap
import threading

import pytest

from pixlstash.tagger_plugins.registry import TaggerPluginManager, user_plugin_dir

_PLUGIN_BODY = textwrap.dedent(
    '''
    from pixlstash.tagger_plugins.base import TaggerPlugin


    class {cls}(TaggerPlugin):
        """Test captioner."""

        name = "{name}"
        display_name = "{name}"
        supports_descriptions = True

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

        def generate_descriptions(self, image_paths, parameters, stop_event=None):
            return {{path: "caption" for path in image_paths}}
    '''
)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


@pytest.fixture(autouse=True)
def _drop_dynamic_modules():
    """Remove the namespaced modules a test loaded, so names can be reused."""
    yield
    for name in [n for n in sys.modules if n.startswith("pixlstash_user_tagger_")]:
        del sys.modules[name]


def _manager(user_dir, first_party=None):
    mgr = TaggerPluginManager(user_dir=str(user_dir), first_party=first_party or [])
    mgr.reload()
    return mgr


def test_single_file_plugin_is_registered(tmp_path):
    _write(
        str(tmp_path / "my_captioner.py"),
        _PLUGIN_BODY.format(cls="MyCaptioner", name="my_captioner"),
    )

    mgr = _manager(tmp_path)

    assert mgr.description_plugin_names() == ["my_captioner"]
    assert mgr.list_errors() == []
    assert mgr.plugin_dirs() == {"user": str(tmp_path)}


def test_folder_plugin_with_relative_import_is_registered(tmp_path):
    pkg = tmp_path / "bundled"
    _write(str(pkg / "helper.py"), "PLUGIN_NAME = 'bundled_captioner'\n")
    _write(
        str(pkg / "__init__.py"),
        "from . import helper\n\n"
        + _PLUGIN_BODY.format(cls="Bundled", name="bundled_captioner")
        + "\nassert helper.PLUGIN_NAME == Bundled.name\n",
    )

    mgr = _manager(tmp_path)

    assert mgr.description_plugin_names() == ["bundled_captioner"]
    assert mgr.list_errors() == []


def test_module_defining_two_plugins_registers_both(tmp_path):
    _write(
        str(tmp_path / "pair.py"),
        _PLUGIN_BODY.format(cls="First", name="first_captioner")
        + _PLUGIN_BODY.format(cls="Second", name="second_captioner"),
    )

    mgr = _manager(tmp_path)

    assert mgr.plugin_names() == ["first_captioner", "second_captioner"]


def test_broken_plugin_is_recorded_and_others_still_load(tmp_path):
    _write(str(tmp_path / "broken.py"), "raise RuntimeError('boom')\n")
    _write(
        str(tmp_path / "good.py"),
        _PLUGIN_BODY.format(cls="Good", name="good_captioner"),
    )

    mgr = _manager(tmp_path)

    assert mgr.plugin_names() == ["good_captioner"]
    errors = mgr.list_errors()
    assert [e["name"] for e in errors] == ["broken"]
    assert "boom" in errors[0]["message"]


def test_name_colliding_with_first_party_is_rejected(tmp_path):
    user_dir = tmp_path / "user"
    _write(
        str(user_dir / "impostor.py"),
        _PLUGIN_BODY.format(cls="Impostor", name="florence2"),
    )
    _write(
        str(user_dir / "genuine.py"),
        _PLUGIN_BODY.format(cls="Genuine", name="genuine_captioner"),
    )
    stub_dir = tmp_path / "stub"
    _write(
        str(stub_dir / "builtin_florence2.py"),
        _PLUGIN_BODY.format(cls="Builtin", name="florence2"),
    )
    sys.path.insert(0, str(stub_dir))
    try:
        mgr = TaggerPluginManager(
            user_dir=str(user_dir),
            first_party=[("builtin_florence2", "Builtin")],
        )
        mgr.reload()
    finally:
        sys.path.remove(str(stub_dir))
        sys.modules.pop("builtin_florence2", None)

    # The built-in kept the name; the user plugin beside it still loaded.
    assert mgr.get_plugin("florence2").__class__.__name__ == "Builtin"
    assert "genuine_captioner" in mgr.plugin_names()
    errors = mgr.list_errors()
    assert [e["name"] for e in errors] == ["impostor"]
    assert "already registered" in errors[0]["message"]


def test_imported_subclass_is_not_registered(tmp_path):
    _write(
        str(tmp_path / "defines.py"),
        _PLUGIN_BODY.format(cls="Defined", name="defined_captioner"),
    )
    _write(
        str(tmp_path / "imports.py"),
        "from pixlstash_user_tagger_defines_py import Defined  # noqa: F401\n",
    )

    mgr = _manager(tmp_path)

    # "defines.py" sorts first, so its module is importable by the time
    # "imports.py" runs - but the imported class must not be registered twice.
    assert mgr.plugin_names() == ["defined_captioner"]
    assert [e["name"] for e in mgr.list_errors()] == ["imports"]


def test_absent_user_dir_is_not_an_error(tmp_path):
    mgr = _manager(tmp_path / "does-not-exist")

    assert mgr.plugin_names() == []
    assert mgr.list_errors() == []


def test_user_plugin_dir_is_under_the_platform_data_dir():
    parts = user_plugin_dir().split(os.sep)

    assert parts[-2:] == ["tagger-plugins", "user"]
    assert "pixlstash" in parts


def test_plugin_raising_in_parameter_schema_is_rejected_not_registered(tmp_path):
    """A schema that raises must fail at load, not at GET /taggers.

    ``plugin_schema()`` is called unguarded by the route *and* by
    ``fill_defaults()`` on library open, so a plugin that survives
    registration and blows up there takes the whole screen - and the boot -
    down with it.
    """
    body = _PLUGIN_BODY.format(cls="Late", name="late_captioner").replace(
        "    def parameter_schema(self):\n        return []",
        "    def parameter_schema(self):\n        raise RuntimeError('schema blew up')",
    )
    assert "schema blew up" in body, "the schema override did not take"
    _write(str(tmp_path / "late_failure.py"), body)
    _write(
        str(tmp_path / "sound.py"),
        _PLUGIN_BODY.format(cls="Sound", name="sound_captioner"),
    )

    mgr = _manager(tmp_path)

    assert mgr.plugin_names() == ["sound_captioner"]
    assert [e["name"] for e in mgr.list_errors()] == ["late_failure"]
    # The calls the route and library-open path make must not raise.
    assert mgr.list_plugins()
    mgr.default_tagger_settings()
    mgr.fill_defaults({})


def test_plugin_calling_sys_exit_does_not_end_the_process(tmp_path):
    """``SystemExit`` is not an ``Exception``, so it needs naming explicitly."""
    _write(str(tmp_path / "quitter.py"), "import sys\n\nsys.exit('bye')\n")
    _write(
        str(tmp_path / "survivor.py"),
        _PLUGIN_BODY.format(cls="Survivor", name="survivor_captioner"),
    )

    mgr = _manager(tmp_path)

    assert mgr.plugin_names() == ["survivor_captioner"]
    assert [e["name"] for e in mgr.list_errors()] == ["quitter"]


def test_plugin_querying_the_manager_at_import_does_not_deadlock(tmp_path):
    """A plugin may call back into the registry from its module body.

    A non-reentrant lock turns that into a permanent hang, which no
    ``try/except`` can catch and nothing logs.
    """
    _write(
        str(tmp_path / "reentrant.py"),
        "import pixlstash.tagger_plugins.registry as registry\n\n"
        "SEEN = registry.get_tagger_plugin_manager().plugin_names()\n\n"
        + _PLUGIN_BODY.format(cls="Reentrant", name="reentrant_captioner"),
    )

    import pixlstash.tagger_plugins.registry as registry

    previous = registry._manager
    registry._manager = TaggerPluginManager(user_dir=str(tmp_path), first_party=[])
    try:
        # Reloaded on a worker so a regression here fails red in seconds
        # instead of hanging - and burning - the whole CI shard.
        worker = threading.Thread(target=registry._manager.reload, daemon=True)
        worker.start()
        worker.join(timeout=30)
        assert not worker.is_alive(), "reload() deadlocked on a re-entrant plugin"

        assert registry._manager.plugin_names() == ["reentrant_captioner"]
        # No error recorded: marking the manager loaded before the scan is
        # what stops the re-entrant call recursing back into reload() and
        # re-executing every plugin until the stack runs out.
        assert registry._manager.list_errors() == []
        # ...and the plugin saw the partial registry rather than a full one.
        assert sys.modules["pixlstash_user_tagger_reentrant_py"].SEEN == []
    finally:
        registry._manager = previous


def test_file_and_package_with_the_same_stem_do_not_clobber_each_other(tmp_path):
    _write(
        str(tmp_path / "twin.py"),
        _PLUGIN_BODY.format(cls="TwinFile", name="twin_file"),
    )
    pkg = tmp_path / "twin"
    _write(str(pkg / "helper.py"), "MARKER = 'package'\n")
    _write(
        str(pkg / "__init__.py"),
        "from . import helper\n\n"
        + _PLUGIN_BODY.format(cls="TwinPkg", name="twin_package")
        + "\nassert helper.MARKER == 'package'\n",
    )

    mgr = _manager(tmp_path)

    assert mgr.plugin_names() == ["twin_file", "twin_package"]
    assert mgr.list_errors() == []
    # Distinct namespaces is *how* they avoid clobbering: the extension is
    # part of the module name, so neither overwrites the other in sys.modules
    # and the package's own submodule still resolves.
    assert "pixlstash_user_tagger_twin_py" in sys.modules
    assert sys.modules["pixlstash_user_tagger_twin"].helper.MARKER == "package"


def test_a_failing_neighbour_does_not_evict_a_loaded_module(tmp_path):
    """A contrived name clash must not damage the entry that already loaded.

    ``twin.py`` and a ``twin_py/`` package both namespace to
    ``pixlstash_user_tagger_twin_py``.  The failing one's cleanup has to put
    back what it displaced, or a registered plugin is left with no
    ``sys.modules`` entry and no working submodule imports.
    """
    _write(
        str(tmp_path / "twin.py"),
        _PLUGIN_BODY.format(cls="TwinFile", name="twin_file"),
    )
    # Sorts after "twin.py" ('.' < '_'), lands on the same module name, fails.
    pkg = tmp_path / "twin_py"
    _write(str(pkg / "__init__.py"), "raise RuntimeError('neighbour boom')\n")

    mgr = _manager(tmp_path)

    assert mgr.plugin_names() == ["twin_file"]
    assert [e["name"] for e in mgr.list_errors()] == ["twin_py"]
    survivor = sys.modules.get("pixlstash_user_tagger_twin_py")
    assert survivor is not None, "the failing neighbour evicted the loaded module"
    assert survivor.TwinFile.name == "twin_file"
