"""Tagger plugin registry - manages first-party and user TaggerPlugin instances."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import sys
from dataclasses import dataclass
from threading import Lock, RLock
from types import ModuleType
from typing import Any

from platformdirs import user_data_dir

from pixlstash.pixl_logging import get_logger
from pixlstash.tagger_plugins.base import TaggerPlugin

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
    """Registry for first-party and user-supplied tagger / captioner plugins.

    Plugins are imported lazily on first call to :meth:`reload`.  If a
    plugin module fails to import (e.g. because an optional dependency like
    ``bitsandbytes`` is absent), the error is logged and the plugin is skipped
    - the rest of the app continues to boot normally.

    User plugins are discovered from *user_dir* at load time only; adding one
    requires a restart.  First-party plugins are loaded first and win on a
    name collision, because some names (``florence2``) are also routed
    natively by the description workflow and a user plugin taking that name
    would be silently bypassed rather than used.

    Args:
        user_dir: Directory scanned for user-supplied plugins.  ``None``
            disables user plugin discovery.
        first_party: Override for the built-in ``(module_path, class_name)``
            list; mainly a test seam.

    Use :func:`get_tagger_plugin_manager` to obtain the process-wide singleton.
    """

    def __init__(
        self,
        user_dir: str | None = None,
        first_party: list[tuple[str, str]] | None = None,
    ) -> None:
        self.user_dir = user_dir
        self._first_party = (
            list(_FIRST_PARTY_PLUGINS) if first_party is None else list(first_party)
        )
        self._plugins: dict[str, TaggerPlugin] = {}
        self._errors: list[PluginLoadError] = []
        # Re-entrant: a user plugin's module body may legitimately call
        # get_tagger_plugin_manager() while that very load is in progress, and
        # a plain Lock turns that into an unrecoverable hang (no exception for
        # the per-plugin try/except to catch).  It sees the partial registry.
        self._lock = RLock()
        self._loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plugin_dirs(self) -> dict[str, str]:
        """Return the plugin directories the manager scans, by source."""
        return {"user": self.user_dir} if self.user_dir else {}

    def load_plugin_from_path(self, path: str) -> None:
        """Import the plugin at *path* and register the classes it defines.

        One plugin, loaded exactly as directory discovery loads it: same module
        namespacing, same containment of a failing import, same registration
        rules. ``pixlstash-cli plugins test`` calls this so its verdict is the
        server's behaviour rather than a second loader that resembles it.

        This adds to the registry rather than declaring it complete - the
        directory-level decisions (which entries are eligible, and the fact
        that first-party plugins were loaded first and so win a name
        collision) belong to :meth:`reload`, which callers still owe.

        Args:
            path: A ``*.py`` file, or a folder holding ``__init__.py``.
        """
        entry = os.path.basename(os.path.normpath(path))
        stem = entry[: -len(".py")] if entry.endswith(".py") else entry
        with self._lock:
            try:
                module = self._import_user_module(entry, path)
                self._register_module_plugins(stem, module)
            # SystemExit as well as Exception: a stray sys.exit() in a plugin
            # module body would otherwise take the caller down, which is
            # exactly the failure this containment exists to prevent.
            # KeyboardInterrupt is deliberately still allowed through.
            except (Exception, SystemExit) as exc:
                message = f"Failed to load plugin: {exc}"
                logger.warning("%s (%s)", message, path, exc_info=True)
                self._errors.append(PluginLoadError(name=stem, message=message))

    def reload(self) -> None:
        """(Re)load all first-party plugins, then user plugins.

        Failed imports are caught and recorded; they do not abort the load
        of the remaining plugins.
        """
        with self._lock:
            self._plugins = {}
            self._errors = []
            # Marked loaded up front: a user plugin that queries the manager
            # from its module body must see the partial registry rather than
            # recurse back into reload() and re-execute every plugin.
            self._loaded = True
            for module_path, class_name in self._first_party:
                self._load_plugin(module_path, class_name)
            self._load_user_plugins()
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

    def get_all_plugins(self) -> list[TaggerPlugin]:
        """Return all successfully loaded plugin instances."""
        self._ensure_loaded()
        with self._lock:
            return list(self._plugins.values())

    def unload_all(self) -> None:
        """Call ``unload()`` on every registered plugin that reports loaded.

        Each plugin is guarded on its own: one that raises must not leave the
        rest of the library resident. Does not load the registry - a plugin
        that was never imported is holding nothing.
        """
        with self._lock:
            plugins = list(self._plugins.values())
        for plugin in plugins:
            name = getattr(plugin, "name", type(plugin).__name__)
            try:
                if not plugin.is_loaded():
                    continue
                plugin.unload()
                logger.debug("Released tagger plugin '%s'.", name)
            except Exception as exc:
                logger.warning("Tagger plugin '%s' failed to unload: %s", name, exc)

    def get_plugin(self, name: str) -> TaggerPlugin | None:
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
                "active_tag_plugin": "pixlstash_tagger",
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
            result["active_tag_plugin"] = "pixlstash_tagger"
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

    # ------------------------------------------------------------------
    # User plugin discovery
    # ------------------------------------------------------------------

    def _load_user_plugins(self) -> None:
        """Import every plugin in ``user_dir`` and register its classes.

        Accepts either a single ``*.py`` file or a directory containing an
        ``__init__.py``.  One broken plugin never stops the others: the
        failure is logged and recorded as a :class:`PluginLoadError`.

        Must be called with ``self._lock`` held.
        """
        if not self.user_dir:
            return
        # Logged before the isdir guard, and unconditionally: the folder does
        # not exist until the user makes it, so the person who most needs to
        # be told where it goes is exactly the one the guard would skip.
        logger.info("User tagger plugins directory: %s", self.user_dir)
        if not os.path.isdir(self.user_dir):
            return

        try:
            entries = sorted(os.listdir(self.user_dir))
        except OSError as exc:
            logger.warning(
                "Could not read the user tagger plugin directory %s: %s",
                self.user_dir,
                exc,
            )
            return

        for entry in entries:
            if entry.startswith(".") or entry.startswith("_"):
                continue
            path = os.path.join(self.user_dir, entry)
            if os.path.isdir(path):
                if not os.path.isfile(os.path.join(path, "__init__.py")):
                    continue
            elif not entry.endswith(".py"):
                continue

            self.load_plugin_from_path(path)

    @staticmethod
    def _import_user_module(entry: str, path: str) -> ModuleType:
        """Import a user plugin file or package from *path*.

        The module is namespaced on the directory entry *including* its
        extension, as ``ImagePluginManager`` does, so ``foo.py`` and a ``foo``
        package side by side get distinct names.
        """
        module_name = "pixlstash_user_tagger_" + entry.replace(".", "_")
        if os.path.isdir(path):
            spec = importlib.util.spec_from_file_location(
                module_name,
                os.path.join(path, "__init__.py"),
                submodule_search_locations=[path],
            )
        else:
            spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create an import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        # Registered before exec so `from . import helper` inside a package
        # resolves against this module.  Any displaced entry is put back if
        # this import fails, so a contrived name clash (a `foo_py` package
        # beside `foo.py`) cannot tear a working plugin's module out of
        # sys.modules and break its submodule resolution.
        displaced = sys.modules.get(module_name)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            if displaced is not None:
                sys.modules[module_name] = displaced
            elif sys.modules.get(module_name) is module:
                del sys.modules[module_name]
            raise
        return module

    def _register_module_plugins(self, stem: str, module: ModuleType) -> None:
        """Instantiate and register every concrete plugin *module* defines."""
        module_name = module.__name__
        found = False
        for value in vars(module).values():
            if not isinstance(value, type) or not issubclass(value, TaggerPlugin):
                continue
            if value is TaggerPlugin or inspect.isabstract(value):
                continue
            if not (
                value.__module__ == module_name
                or value.__module__.startswith(module_name + ".")
            ):
                continue  # imported from elsewhere, not defined here
            found = True
            self._register_user_plugin(stem, value)
        if not found:
            self._errors.append(
                PluginLoadError(name=stem, message="No TaggerPlugin subclass found")
            )

    def _register_user_plugin(self, stem: str, cls: type[TaggerPlugin]) -> None:
        """Instantiate *cls* and add it to the registry, or record why not."""
        try:
            instance = cls()
            plugin_name = (instance.name or "").strip()
            # Exercise the schema once here, where a failure is containable.
            # Every later caller (GET /taggers, fill_defaults on library open)
            # runs unguarded, so a plugin that raises in parameter_schema(),
            # list_downloaded_artifacts() or is_loaded() must be rejected now
            # rather than take the whole Auto-tagging screen - or the boot -
            # down with it.
            instance.plugin_schema()
        except (Exception, SystemExit) as exc:
            message = f"Failed to initialise {cls.__name__}: {exc}"
            logger.warning("%s (user tagger plugin '%s')", message, stem, exc_info=True)
            self._errors.append(PluginLoadError(name=stem, message=message))
            return
        if not plugin_name:
            self._errors.append(
                PluginLoadError(
                    name=stem,
                    message=f"{cls.__name__} has an empty name attribute",
                )
            )
            return
        if plugin_name in self._plugins:
            message = (
                f"Plugin name '{plugin_name}' is already registered; "
                f"{cls.__name__} was skipped."
            )
            logger.warning("%s (user tagger plugin '%s')", message, stem)
            self._errors.append(PluginLoadError(name=stem, message=message))
            return
        self._plugins[plugin_name] = instance


def user_plugin_dir() -> str:
    """Return the directory user-supplied tagger plugins are loaded from.

    Not created here: the folder is absent until the user makes it, and a
    ``makedirs`` on every boot is start-up write churn for nothing.
    """
    return os.path.join(user_data_dir("pixlstash"), "tagger-plugins", "user")


_manager: TaggerPluginManager | None = None
_manager_lock = Lock()


def unload_loaded_tagger_plugins() -> None:
    """Release every loaded plugin in the process-wide registry.

    The one caller is :meth:`~pixlstash.vault.Vault._maybe_aggressive_unload`,
    the *Keep models in memory = off* sweep, which runs only once every worker
    is idle. That is deliberate on both counts:

    * The registry is process-wide, and the vault's engine is the one its
      plugins are bound to (``Vault._bind_engine_services``). Hanging this off
      ``InferenceEngine.close()`` instead would mean reaping a throwaway CPU
      spillover engine - which ``DescriptionTask`` and ``TagTask`` do on the
      hot path, before every batch - unloading the GPU engine's models.
    * A plugin's ``unload()`` may arrive while its own load is in flight, which
      is memory-unsafe rather than merely wrong (``tests/test_model_unload_race.py``).
      The built-in services hold one lock across load and unload; a third-party
      plugin may not, and waiting for idle is what keeps that window shut.

    Never triggers a load: a registry nothing has built holds nothing resident,
    and importing every plugin module in order to free memory would be the
    opposite of the thing being asked for.
    """
    manager = _manager
    if manager is None or not manager._loaded:
        return
    manager.unload_all()


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
                # Assigned *before* reload(), deliberately: loading executes
                # user plugin code, and a plugin that calls this function from
                # its module body must short-circuit on the unlocked check
                # above.  Building into a local and assigning afterwards would
                # send it into `_manager_lock` - a plain, non-reentrant Lock
                # already held by this thread - and hang the boot for good.
                _manager = TaggerPluginManager(user_dir=user_plugin_dir())
                _manager.reload()
    return _manager
