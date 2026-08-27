from __future__ import annotations

import importlib.util
import inspect
import os
from dataclasses import dataclass
from threading import Lock
from types import ModuleType
from typing import Any

from platformdirs import user_data_dir

from pixlstash.image_plugins.base import ImagePlugin
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

IGNORED_PLUGIN_FILES = {
    "plugin_template.py",
}


@dataclass
class PluginLoadError:
    file: str
    message: str


class ImagePluginManager:
    def __init__(self, built_in_dir: str, user_dir: str):
        self.built_in_dir = built_in_dir
        self.user_dir = user_dir
        self._plugins: dict[str, ImagePlugin] = {}
        self._errors: list[PluginLoadError] = []
        self._lock = Lock()

    def plugin_dirs(self) -> list[tuple[str, str]]:
        return [
            ("user", self.user_dir),
            ("built_in", self.built_in_dir),
        ]

    def reload(self) -> None:
        with self._lock:
            self._plugins = {}
            self._errors = []
            # name -> (source, path) of the file that claimed it, so a later
            # collision can name the *claiming* file rather than itself.
            origins: dict[str, tuple[str, str]] = {}
            logger.info("User image plugins directory: %s", self.user_dir)
            for source, folder in self.plugin_dirs():
                if not os.path.isdir(folder):
                    continue
                for entry in sorted(os.listdir(folder)):
                    if not entry.endswith(".py"):
                        continue
                    if entry in IGNORED_PLUGIN_FILES:
                        continue
                    path = os.path.join(folder, entry)
                    plugin = self._load_plugin_from_path(path)
                    if plugin is None:
                        continue
                    plugin_name = (plugin.name or "").strip()
                    if not plugin_name:
                        self._errors.append(
                            PluginLoadError(
                                file=path,
                                message="Plugin missing non-empty name",
                            )
                        )
                        continue
                    if plugin_name in self._plugins:
                        claimed_by, claimed_path = origins[plugin_name]
                        if source == "built_in" and claimed_by == "user":
                            # User directories are walked first, so a shadowed
                            # built-in arrives second and used to be logged as
                            # "the duplicate" - pointing at the file that lost,
                            # not the one that took over. Name the user file.
                            message = (
                                f"Replaces the built-in image plugin '{plugin_name}'"
                            )
                            self._errors.append(
                                PluginLoadError(file=claimed_path, message=message)
                            )
                            logger.warning("%s (%s)", message, claimed_path)
                        else:
                            logger.warning(
                                "Ignoring duplicate plugin name '%s' from %s",
                                plugin_name,
                                path,
                            )
                        continue
                    self._plugins[plugin_name] = plugin
                    origins[plugin_name] = (source, path)

    def list_plugins(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._plugins[name].plugin_schema() for name in sorted(self._plugins)
            ]

    def list_errors(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {"file": error.file, "message": error.message} for error in self._errors
            ]

    def get_plugin(self, name: str) -> ImagePlugin | None:
        if not name:
            return None
        with self._lock:
            return self._plugins.get(name)

    def _load_plugin_from_path(self, path: str) -> ImagePlugin | None:
        module = self._load_module(path)
        if module is None:
            return None
        try:
            plugin_class = self._find_plugin_class(module)
            if plugin_class is None:
                self._errors.append(
                    PluginLoadError(
                        file=path,
                        message="No concrete ImagePlugin subclass defined in this file",
                    )
                )
                return None
            plugin = plugin_class()
            # Exercise the schema once here, where a failure is containable.
            # `list_plugins()` comprehends over every plugin and runs unguarded,
            # so one plugin raising in `parameter_schema()` - or declaring a
            # `models` header that is not a list of dicts - would take
            # `GET /pictures/plugins` down for all of them. The tagger registry
            # probes at load for the same reason (`_register_user_plugin`).
            plugin.plugin_schema()
            return plugin
        except Exception as exc:
            message = f"Failed to initialize plugin: {exc}"
            self._errors.append(PluginLoadError(file=path, message=message))
            logger.warning("%s (%s)", message, path)
            return None

    def _load_module(self, path: str) -> ModuleType | None:
        try:
            module_name = "pixlstash_dynamic_plugin_" + os.path.basename(path).replace(
                ".", "_"
            )
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                self._errors.append(
                    PluginLoadError(file=path, message="Failed to create import spec")
                )
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception as exc:
            message = f"Failed to import plugin module: {exc}"
            self._errors.append(PluginLoadError(file=path, message=message))
            logger.warning("%s (%s)", message, path)
            return None

    @staticmethod
    def _find_plugin_class(module: ModuleType) -> type[ImagePlugin] | None:
        """Return the concrete plugin class *module* itself defines.

        A class the module merely imported belongs to whoever defined it;
        returning it would let a plugin that imports a built-in for reference
        ship that built-in in place of the class its author wrote, and - since
        a user plugin also wins a name collision - replace the built-in with
        it. ``TaggerPluginManager._register_module_plugins`` excludes imported
        classes the same way (its extra ``__module__`` prefix clause is for the
        package shape, which this loader does not accept).

        An abstract class is only a *fallback*, not a skip: an intermediate
        base above the real one must not win, but a file whose only plugin
        class is abstract is an author who forgot a method, and the
        instantiation error names both the class and the method - a better
        report than "no plugin class here" for a file that plainly has one.
        """
        fallback: type[ImagePlugin] | None = None
        for value in module.__dict__.values():
            if not isinstance(value, type):
                continue
            if not issubclass(value, ImagePlugin) or value is ImagePlugin:
                continue
            if value.__module__ != module.__name__:
                continue  # imported from elsewhere, not defined here
            if inspect.isabstract(value):
                fallback = fallback or value
                continue
            return value
        return fallback


_PLUGIN_MANAGER: ImagePluginManager | None = None
_PLUGIN_MANAGER_LOCK = Lock()


def user_plugin_dir() -> str:
    """Return the directory user-supplied image plugins are loaded from.

    Named rather than inlined so `pixlstash-cli plugins` can install into the
    same place without constructing a manager (which would import and run every
    plugin on disk).
    """
    return os.path.join(user_data_dir("pixlstash"), "image-plugins", "user")


def get_image_plugin_manager() -> ImagePluginManager:
    global _PLUGIN_MANAGER
    with _PLUGIN_MANAGER_LOCK:
        if _PLUGIN_MANAGER is None:
            built_in = os.path.join(os.path.dirname(__file__), "built-in")
            _PLUGIN_MANAGER = ImagePluginManager(built_in, user_plugin_dir())
            _PLUGIN_MANAGER.reload()
        return _PLUGIN_MANAGER
