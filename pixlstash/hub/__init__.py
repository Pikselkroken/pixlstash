"""The hub: app-level identity, settings, and the library registry.

The hub database lives outside every library and holds what never belonged in a
vault - the user, credentials, tokens, per-user preferences, machine settings,
and which libraries this installation knows about. See the multi-library plan
and ``docs/backend_architecture.md`` §17.
"""

from pixlstash.hub.cli_hint import cli_hint, running_in_docker
from pixlstash.hub.db import (
    HubDatabase,
    HubPermissionError,
    check_file_mode,
    default_hub_path,
)
from pixlstash.hub.engine import HubEngine
from pixlstash.hub.registry import (
    ActiveLibraryError,
    Library,
    LibraryError,
    LibraryExistsError,
    LibraryNotFoundError,
    LibraryRegistry,
    NotAVaultError,
    resolve_path,
    validate_vault_folder,
)
from pixlstash.hub.schema import CURRENT_SCHEMA_VERSION, HubSchemaTooNewError

__all__ = [
    "ActiveLibraryError",
    "CURRENT_SCHEMA_VERSION",
    "HubDatabase",
    "HubEngine",
    "HubPermissionError",
    "HubSchemaTooNewError",
    "Library",
    "LibraryError",
    "LibraryExistsError",
    "LibraryNotFoundError",
    "LibraryRegistry",
    "NotAVaultError",
    "check_file_mode",
    "cli_hint",
    "default_hub_path",
    "resolve_path",
    "running_in_docker",
    "validate_vault_folder",
]
