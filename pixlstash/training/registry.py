"""The installed training plugins, by name.

Each is a :class:`~pixlstash.training.backend.TrainerBackend` subclass. The
first-party ones are listed in :data:`_FIRST_PARTY_PLUGINS` and are always
registered, so ai-toolkit is enabled with no setting. There is no user-plugin
loading: a trainer launches host processes (authz §16.3), so admitting
third-party code is its own decision. :func:`register` is the seam it would
use, and the one tests use.

A run's trainer is its ``trainer`` column, and a recipe's its ``"trainer"``
key; either left empty means :data:`DEFAULT_TRAINER`, the trainer every run
and recipe from before plugins used.
"""

from __future__ import annotations

import importlib
import threading

from pixlstash.pixl_logging import get_logger
from pixlstash.training.backend import TrainerBackend

logger = get_logger(__name__)

#: The trainer of a run or recipe that names none.
DEFAULT_TRAINER = "ai-toolkit"

_FIRST_PARTY_PLUGINS = [
    ("pixlstash.training_plugins.ai_toolkit", "AiToolkitPlugin"),
]

_plugins: dict[str, type[TrainerBackend]] = {}
_loaded = False
_lock = threading.Lock()


class TrainerNotInstalled(KeyError):
    """No training plugin of the asked-for name is registered."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "training plugin not installed"


def _load() -> None:
    global _loaded
    with _lock:
        if _loaded:
            return
        for module_name, class_name in _FIRST_PARTY_PLUGINS:
            cls = getattr(importlib.import_module(module_name), class_name)
            _plugins.setdefault(cls.name, cls)
        _loaded = True


def installed() -> list[type[TrainerBackend]]:
    """Every registered training plugin, first-party first."""
    _load()
    return list(_plugins.values())


def get(name: str | None) -> type[TrainerBackend]:
    """The plugin registered as *name* (None: :data:`DEFAULT_TRAINER`).

    Raises:
        TrainerNotInstalled: None is registered under that name.
    """
    _load()
    key = name or DEFAULT_TRAINER
    try:
        return _plugins[key]
    except KeyError:
        raise TrainerNotInstalled(f"training plugin '{key}' is not installed") from None


def name_of(row: dict | None) -> str:
    """The trainer a run row or a recipe names; empty means the default."""
    return (row or {}).get("trainer") or DEFAULT_TRAINER


def for_run(run: dict | None) -> type[TrainerBackend]:
    """The plugin that trains *run* (a ``training_run`` row).

    Raises:
        TrainerNotInstalled: The run's trainer is not registered.
    """
    return get(name_of(run))


def register(cls: type[TrainerBackend]) -> None:
    """Register *cls* under ``cls.name``, replacing one of that name.

    Its recipes are read from then on.
    """
    if not cls.name:
        raise ValueError(f"{cls.__name__} has no name to register under.")
    _load()
    with _lock:
        _plugins[cls.name] = cls
    logger.info("Training plugin %s registered.", cls.name)
    _reload_presets()


def unregister(name: str) -> None:
    """Remove the plugin registered as *name*, and forget its recipes."""
    _load()
    with _lock:
        _plugins.pop(name, None)
    _reload_presets()


def _reload_presets() -> None:
    # Local import: presets reads the registry, so importing it at the top
    # would be circular.
    from pixlstash.training import presets

    presets.reload()
