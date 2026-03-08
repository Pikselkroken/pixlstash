from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from threading import Lock
from types import ModuleType
from typing import Any

from pixlvault.image_plugins.base import ImagePlugin
from pixlvault.pixl_logging import get_logger

logger = get_logger(__name__)

IGNORED_PLUGIN_FILES = {
    "plugin_template.py",
}


@dataclass
class PluginLoadError:
    file: str
    message: str


class ImagePluginManager:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._plugins: dict[str, ImagePlugin] = {}
        self._errors: list[PluginLoadError] = []
        self._lock = Lock()

    @property
    def built_in_dir(self) -> str:
        return os.path.join(self.base_dir, "built-in")

    @property
    def user_dir(self) -> str:
        return os.path.join(self.base_dir, "user")

    def plugin_dirs(self) -> list[tuple[str, str]]:
        return [
            ("user", self.user_dir),
            ("built_in", self.built_in_dir),
        ]

    def reload(self) -> None:
        with self._lock:
            self._plugins = {}
            self._errors = []
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
                        logger.warning(
                            "Ignoring duplicate plugin name '%s' from %s",
                            plugin_name,
                            path,
                        )
                        continue
                    self._plugins[plugin_name] = plugin

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
                    PluginLoadError(file=path, message="No ImagePlugin subclass found")
                )
                return None
            plugin = plugin_class()
            return plugin
        except Exception as exc:
            message = f"Failed to initialize plugin: {exc}"
            self._errors.append(PluginLoadError(file=path, message=message))
            logger.warning("%s (%s)", message, path)
            return None

    def _load_module(self, path: str) -> ModuleType | None:
        try:
            module_name = "pixlvault_dynamic_plugin_" + os.path.basename(path).replace(
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
        for value in module.__dict__.values():
            if not isinstance(value, type):
                continue
            if value is ImagePlugin:
                continue
            if issubclass(value, ImagePlugin):
                return value
        return None


_PLUGIN_MANAGER: ImagePluginManager | None = None
_PLUGIN_MANAGER_LOCK = Lock()


def _default_plugin_base_dir() -> str:
    return os.path.dirname(__file__)


def get_image_plugin_manager() -> ImagePluginManager:
    global _PLUGIN_MANAGER
    with _PLUGIN_MANAGER_LOCK:
        if _PLUGIN_MANAGER is None:
            _PLUGIN_MANAGER = ImagePluginManager(_default_plugin_base_dir())
            _PLUGIN_MANAGER.reload()
        return _PLUGIN_MANAGER
