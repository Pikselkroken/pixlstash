"""Tagger plugin registry — manages first-party TaggerPlugin instances."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

_FIRST_PARTY_PLUGINS = [
    ("pixlstash.tagger_plugins.wd14", "WD14Plugin"),
    ("pixlstash.tagger_plugins.pixlstash_tagger", "PixlStashTaggerPlugin"),
    ("pixlstash.tagger_plugins.florence2", "Florence2Plugin"),
    ("pixlstash.tagger_plugins.joycaption", "JoyCaptionPlugin"),
]


@dataclass
class PluginLoadError:
    """Records a plugin that failed to import or initialise."""

    name: str
    message: str


class TaggerPluginManager:
    """Registry for first-party tagger / captioner plugins.

    Plugins are imported lazily on first call to :meth:`reload`.  If a
    plugin module fails to import (e.g. because an optional dependency like
    ``bitsandbytes`` is absent), the error is logged and the plugin is skipped
    — the rest of the app continues to boot normally.

    Use :func:`get_tagger_plugin_manager` to obtain the process-wide singleton.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, "TaggerPlugin"] = {}  # noqa: F821
        self._errors: list[PluginLoadError] = []
        self._lock = Lock()
        self._loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """(Re)load all first-party plugins.

        Failed imports are caught and recorded; they do not abort the load
        of the remaining plugins.
        """
        with self._lock:
            self._plugins = {}
            self._errors = []
            for module_path, class_name in _FIRST_PARTY_PLUGINS:
                self._load_plugin(module_path, class_name)
            self._loaded = True
            if self._errors:
                for err in self._errors:
                    logger.warning(
                        "Tagger plugin '%s' could not be loaded: %s",
                        err.name,
                        err.message,
                    )
            logger.info(
                "Tagger plugins loaded: %s",
                ", ".join(self._plugins) or "(none)",
            )

    def list_plugins(self) -> list[dict[str, Any]]:
        """Return plugin schema dicts for all successfully loaded plugins.

        Returns:
            List of dicts as produced by :meth:`TaggerPlugin.plugin_schema`.
        """
        self._ensure_loaded()
        with self._lock:
            return [self._plugins[n].plugin_schema() for n in sorted(self._plugins)]

    def list_errors(self) -> list[dict[str, str]]:
        """Return load errors for plugins that failed to import.

        Returns:
            List of ``{"name": ..., "message": ...}`` dicts.
        """
        with self._lock:
            return [{"name": e.name, "message": e.message} for e in self._errors]

    def get_all_plugins(self) -> list["TaggerPlugin"]:  # noqa: F821
        """Return all successfully loaded plugin instances."""
        self._ensure_loaded()
        with self._lock:
            return list(self._plugins.values())

    def get_plugin(self, name: str) -> "TaggerPlugin | None":  # noqa: F821
        """Return the plugin with the given name, or ``None`` if not found.

        Args:
            name: Plugin name as defined by ``TaggerPlugin.name``.

        Returns:
            Plugin instance, or ``None``.
        """
        if not name:
            return None
        self._ensure_loaded()
        with self._lock:
            return self._plugins.get(name)

    def plugin_names(self) -> list[str]:
        """Return a sorted list of successfully loaded plugin names."""
        self._ensure_loaded()
        with self._lock:
            return sorted(self._plugins)

    def tag_plugin_names(self) -> list[str]:
        """Return names of plugins that support tag generation."""
        self._ensure_loaded()
        with self._lock:
            return sorted(n for n, p in self._plugins.items() if p.supports_tags)

    def description_plugin_names(self) -> list[str]:
        """Return names of plugins that support caption generation."""
        self._ensure_loaded()
        with self._lock:
            return sorted(
                n for n, p in self._plugins.items() if p.supports_descriptions
            )

    # ------------------------------------------------------------------
    # Default settings helpers
    # ------------------------------------------------------------------

    def default_tagger_settings(self) -> dict[str, Any]:
        """Return a full default ``tagger_settings`` JSON structure.

        Tag plugins are disabled by default; Florence-2 is set as the active
        description plugin if it is registered.

        Returns:
            Default ``tagger_settings`` dict.
        """
        self._ensure_loaded()
        with self._lock:
            plugins: dict[str, Any] = {}
            for name, plugin in self._plugins.items():
                entry: dict[str, Any] = {"params": plugin.default_params()}
                if plugin.supports_tags:
                    entry["enabled"] = plugin.default_enabled
                plugins[name] = entry

            active_desc = "florence2" if "florence2" in self._plugins else None
            return {
                "active_description_plugin": active_desc,
                "active_tag_plugin": "wd14",
                "plugins": plugins,
            }

    def fill_defaults(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Return *settings* with missing plugin entries filled from defaults.

        Plugins present in the registry but absent from *settings* are added
        with their default values.  Unknown plugin names already in *settings*
        are preserved (for downgrade safety).  Per-parameter gaps within a
        plugin's ``params`` are also filled from the schema defaults.

        Args:
            settings: Existing ``tagger_settings`` dict (may be partial).

        Returns:
            Copy of *settings* with all registered plugins present.
        """
        self._ensure_loaded()
        import copy

        result = copy.deepcopy(settings) if settings else {}
        with self._lock:
            default_desc = "florence2" if "florence2" in self._plugins else None
        if "active_description_plugin" not in result:
            result["active_description_plugin"] = default_desc
        if "active_tag_plugin" not in result:
            result["active_tag_plugin"] = "wd14"
        plugins_node = result.setdefault("plugins", {})

        with self._lock:
            for name, plugin in self._plugins.items():
                if name not in plugins_node:
                    entry: dict[str, Any] = {"params": plugin.default_params()}
                    if plugin.supports_tags:
                        entry["enabled"] = plugin.default_enabled
                    plugins_node[name] = entry
                else:
                    # Fill any missing per-parameter defaults.
                    existing_params = plugins_node[name].setdefault("params", {})
                    for field in plugin.parameter_schema():
                        existing_params.setdefault(field["name"], field["default"])
                    # Ensure tag-capable plugins have an "enabled" key.
                    if plugin.supports_tags:
                        plugins_node[name].setdefault("enabled", plugin.default_enabled)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()

    def _load_plugin(self, module_path: str, class_name: str) -> None:
        """Import one plugin module and register the plugin instance.

        Errors are caught and recorded without re-raising.
        """
        try:
            import importlib

            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            instance = cls()
            plugin_name = (instance.name or "").strip()
            if not plugin_name:
                self._errors.append(
                    PluginLoadError(
                        name=class_name,
                        message="Plugin has an empty name attribute",
                    )
                )
                return
            if plugin_name in self._plugins:
                logger.warning(
                    "Ignoring duplicate tagger plugin name '%s' from %s.%s",
                    plugin_name,
                    module_path,
                    class_name,
                )
                return
            self._plugins[plugin_name] = instance
        except Exception as exc:
            self._errors.append(
                PluginLoadError(
                    name=class_name,
                    message=str(exc),
                )
            )


_manager: TaggerPluginManager | None = None
_manager_lock = Lock()


def get_tagger_plugin_manager() -> TaggerPluginManager:
    """Return the process-wide :class:`TaggerPluginManager` singleton.

    The manager is created and its plugins loaded on the first call.

    Returns:
        The singleton :class:`TaggerPluginManager`.
    """
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = TaggerPluginManager()
                _manager.reload()
    return _manager
