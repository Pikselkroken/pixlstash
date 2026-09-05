import gc
import uvicorn
import os
import sqlite3
import json
import re
import socket
import asyncio
import threading
import time
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version as package_version
from alembic.util.exc import CommandError as AlembicCommandError
from platformdirs import user_config_dir
from sqlalchemy.exc import SQLAlchemyError


from contextlib import asynccontextmanager
from fastapi import (
    Depends,
    FastAPI,
    Request,
    HTTPException,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from pillow_heif import register_heif_opener


from pixlstash.db_models import (
    User,
)

from pixlstash.auth import AuthService, LoginRequest
from pixlstash.authz import AUTHZ_GATE_ENFORCING, AuthzGate
from pixlstash.listeners import ListenersMixin, _get_lan_ip
from pixlstash.maintenance import MaintenanceMixin
from pixlstash.openapi_custom import (
    API_DESCRIPTION,
    API_OPENAPI_TAGS,
    OpenApiMixin,
    render_scalar_html,
)
from pixlstash.ssl_setup import SslSetupMixin
from pixlstash.ws.broadcaster import WsBroadcasterMixin
from pixlstash.pixl_logging import get_logger, uvicorn_log_config
from pixlstash.services import library_settings_service, scrapheap_service
from pixlstash.utils.quality.smart_score_utils import smart_score_penalised_tags
from pixlstash.db_models.tag import DEFAULT_SMART_SCORE_PENALIZED_TAGS
from pixlstash.startup_checks import StartupChecks
from pixlstash.startup_permissions import mkdir_private
from pixlstash.hub.bootstrap import (
    VAULT_RECREATE_ENV,
    bootstrap_hub,
    registered_vault_path,
    set_aside_unusable_vault,
    unusable_vault_from_open_failure,
)
from pixlstash.hub.registry import LibraryRegistry
from pixlstash.services.library_switch_service import LibrarySwitchService
from pixlstash.services.library_generation_coordinator import (
    LibraryGenerationCoordinator,
)
from pixlstash.services.builtin_caches import (
    declare_huggingface_cache,
    declare_insightface_packs,
    huggingface_cache_dir,
    insightface_models_dir,
)
from pixlstash.services.builtin_models import (
    builtin_model_dir,
    declare_builtin_models,
)
from pixlstash.services.managed_model_store import ensure_managed_folder
from pixlstash.telemetry import ensure_install_identity, start_periodic_sender
from pixlstash.vault import Vault
from pixlstash.routes.config import create_router as create_config_router
from pixlstash.routes.characters import create_router as create_characters_router
from pixlstash.routes.characters_faces import (
    create_router as create_characters_faces_router,
)
from pixlstash.routes.picture_sets import create_router as create_picture_sets_router
from pixlstash.routes.projects import create_router as create_projects_router
from pixlstash.routes.tags import create_router as create_tags_router
from pixlstash.routes.stacks import create_router as create_stacks_router
from pixlstash.routes.dedup import create_router as create_dedup_router
from pixlstash.routes.pictures import (
    create_router as create_pictures_router,
)
from pixlstash.routes.comfyui import create_router as create_comfyui_router
from pixlstash.routes.tag_predictions import (
    create_router as create_tag_predictions_router,
)
from pixlstash.routes.tag_suggestions import (
    create_router as create_tag_suggestions_router,
)
from pixlstash.routes.operations import create_router as create_operations_router
from pixlstash.routes.reviews import create_router as create_reviews_router
from pixlstash.routes.insights import create_router as create_insights_router
from pixlstash.routes.moves import create_router as create_moves_router
from pixlstash.routes.tag_health import create_router as create_tag_health_router
from pixlstash.routes.tagger_runs import (
    create_router as create_tagger_runs_router,
)
from pixlstash.routes.reference_folders import (
    create_router as create_reference_folders_router,
)
from pixlstash.routes.import_folders import (
    create_router as create_import_folders_router,
)
from pixlstash.routes.filesystem import create_router as create_filesystem_router
from pixlstash.routes.folder_structure import (
    create_router as create_folder_structure_router,
)
from pixlstash.routes.libraries import create_router as create_libraries_router
from pixlstash.routes.library_layout import (
    create_router as create_library_layout_router,
)
from pixlstash.routes.model_files import create_router as create_model_files_router
from pixlstash.routes.model_folders import create_router as create_model_folders_router
from pixlstash.routes.model_imports import create_router as create_model_imports_router
from pixlstash.routes.model_moves import create_router as create_model_moves_router
from pixlstash.routes.model_shelf import create_router as create_model_shelf_router
from pixlstash.routes.model_icons import create_router as create_model_icons_router
from pixlstash.routes.model_stacks import create_router as create_model_stacks_router
from pixlstash.routes.guest_scores import create_router as create_guest_scores_router
from pixlstash.routes.share import create_router as create_share_router
from pixlstash.routes.taggers import create_router as create_taggers_router
from pixlstash.routes.snapshots import create_router as create_snapshots_router
from pixlstash.routes.telemetry import create_router as create_telemetry_router
from pixlstash.routes.test_hooks import create_router as create_test_hooks_router
from pixlstash.server_config_io import DEVICE_ON_DISK_KEY, persist_server_config
from pixlstash.utils.atomic_write import write_json_atomic
from pixlstash.utils.path_mapper import PathMapper
from pixlstash.utils.rate_limiter import RateLimitMiddleware
from pixlstash.utils.request_origin import OriginClientMiddleware


# Logging will be set up after config is loaded
logger = get_logger(__name__)


class VersionResponse(BaseModel):
    """Body of ``GET /version``.

    Public, unauthenticated endpoint used by the SPA for version display and by
    the daily active-install telemetry ping. ``extra="allow"`` keeps the
    response forward-compatible: new diagnostic fields can be added without
    breaking older clients that ignore unknown keys.
    """

    model_config = ConfigDict(extra="allow")

    message: str = Field(
        description="Human-readable identifier for the API. Constant string.",
        examples=["PixlStash REST API"],
    )
    version: str = Field(
        description=(
            "Running PixlStash version, read from the installed package "
            "metadata (the `version` in `pyproject.toml`)."
        ),
        examples=["1.5.0"],
    )
    install_type: str = Field(
        description=(
            "How this server was installed, for active-install telemetry. "
            "Always exactly one of: `docker` (the reliable signal - set via "
            "the `PIXLSTASH_IN_DOCKER=1` env flag or the presence of "
            "`/.dockerenv`), `pip` (the default for a plain Python/pip "
            "install), `electron` (the cross-platform desktop app, which "
            "declares `PIXLSTASH_INSTALL_TYPE=electron`), or `other` (the "
            "uncertain fallback - e.g. the Windows Inno Setup build that "
            "declares `PIXLSTASH_INSTALL_TYPE=other`). Clients should treat "
            "any unrecognised value as `other`."
        ),
        examples=["docker", "pip", "electron", "other"],
    )
    docker_variant: str = Field(
        description=(
            "Which Docker image variant is running, reported from the "
            "`PIXLSTASH_DOCKER_VARIANT` env var. Only meaningful when "
            "`install_type` is `docker`; defaults to `gpu` otherwise."
        ),
        examples=["gpu", "cpu"],
    )


class SessionStatusResponse(BaseModel):
    """Body of ``GET /check-session`` (returned as a JSONResponse)."""

    model_config = ConfigDict(extra="allow")

    status: str


class NetworkInfoResponse(BaseModel):
    """Body of ``GET /network/info``."""

    model_config = ConfigDict(extra="allow")

    lan_ip: str
    is_private: bool


class MessageResponse(BaseModel):
    """Generic ``{"message": ...}`` body (login / logout)."""

    model_config = ConfigDict(extra="allow")

    message: str


class RegistrationStatusResponse(BaseModel):
    """Body of ``GET /login`` (registration check)."""

    model_config = ConfigDict(extra="allow")

    needs_registration: bool


API_V1_PREFIX = "/api/v1"


class Server(
    WsBroadcasterMixin, ListenersMixin, MaintenanceMixin, OpenApiMixin, SslSetupMixin
):
    """
    Main server class for the PixlStash FastAPI application.

    Attributes:
        server_config_path(str): Server-side-only configuration file.
        DEFAULT_MAX_VRAM_GB: Class-level VRAM budget override (GB). When set
            (e.g. by the pytest ``--max-vram-gb`` option) it takes precedence
            over the persisted user config value for all Server instances.
            ``None`` means use the user config.
        DEFAULT_FORCE_CPU: Class-level CPU-inference override. When ``True``,
            forces CPU inference after startup checks complete, preventing the
            startup check from clobbering a ``--force-cpu`` flag set by the
            test framework. ``None`` means startup checks decide.
        DEFAULT_PORT: Class-level port override. When set (e.g. by the pytest
            conftest to a free OS-assigned port), it replaces the port from the
            persisted config for all Server instances. ``None`` means use the
            config value.
        DEFAULT_CLEANUP_MISSING_PICTURES: Class-level startup cleanup toggle.
            When ``True``, startup removes picture rows that point to missing
            source files before thumbnail generation. ``False`` means disabled.
        DEFAULT_INSIGHTFACE_MODEL_PACK: Class-level InsightFace model-pack
            override. When set (e.g. by a test), it replaces the
            ``insightface_model_pack`` value from the persisted config for all
            Server instances. ``None`` means use the config value.
        DEFAULT_DECLARE_MODEL_ROOTS: Whether start-up declares the model roots
            PixlStash owns. ``False`` in the test suite, where it is the only
            way to keep the shelf's contents machine-independent: those roots
            are machine-global by design, so a Server on a temp config dir would
            otherwise describe whichever engines the developer's home happens to
            hold (``test_workers_api`` caught it as ``assert 3 == 0``). Pointing
            them at a temp directory instead is no longer an option - since #905
            the downloaders read the same accessor, so a temp directory means
            every engine is downloaded again.
    """

    DEFAULT_MAX_VRAM_GB: float | None = None
    DEFAULT_FORCE_CPU: bool | None = None
    DEFAULT_FAST_CAPTIONS: bool = False
    DEFAULT_PORT: int | None = None
    DEFAULT_CLEANUP_MISSING_PICTURES: bool = False
    DEFAULT_INSIGHTFACE_MODEL_PACK: str | None = None
    DEFAULT_DECLARE_MODEL_ROOTS: bool = True

    @staticmethod
    def running_in_docker() -> bool:
        """Return True when the server is running inside a Docker container.

        Docker is the **reliable** half of the install-type telemetry, so this
        check uses two independent positive signals and treats either as proof
        of a containerised run:

        1. ``PIXLSTASH_IN_DOCKER == "1"`` - set explicitly by our own images
           (primary signal).
        2. The presence of ``/.dockerenv`` - a marker file the Docker runtime
           creates in every container root, present even if the env flag was
           lost (e.g. an overridden entrypoint or a stripped environment).

        Both checks are cheap (a dict lookup and a single ``stat``). The
        filesystem fallback only fires when the env flag is absent, and that
        case is logged so a "docker without the flag" deployment is visible in
        the logs rather than silently misclassified.
        """
        if os.environ.get("PIXLSTASH_IN_DOCKER", "") == "1":
            return True

        # Secondary signal: the Docker runtime drops this marker file into the
        # container root. We only reach here when the env flag is missing, so a
        # positive result means an unexpected-but-still-docker deployment.
        try:
            if os.path.exists("/.dockerenv"):
                logger.info(
                    "Detected Docker via /.dockerenv marker file while "
                    "PIXLSTASH_IN_DOCKER was unset (value=%r); treating install "
                    "as docker.",
                    os.environ.get("PIXLSTASH_IN_DOCKER"),
                )
                return True
        except OSError as exc:
            # A failed stat must not crash version reporting; fall through to
            # the non-docker default but record why the fallback signal was
            # inconclusive.
            logger.warning(
                "Could not stat /.dockerenv while detecting Docker "
                "(error=%s); assuming not running in Docker.",
                exc,
            )
        return False

    # install_type telemetry: exactly these values may ever be reported.
    # "docker" is the reliable signal, "pip" the default, "other" the explicit
    # opt-out used by installers (e.g. the Windows Inno Setup wheel build) that
    # otherwise look like a plain pip install.
    #
    # "dev" is a declaration, not a detection: a machine that sets it is ours,
    # and the metrics collector subtracts that bucket from the active-install
    # figure whatever version it happens to be running. Without it a development
    # checkout was only excluded by *inferring* from its unpublished version,
    # which silently stopped working the day pre-releases started counting as
    # real users. Every consumer of this value has to know the bucket, so the
    # list is mirrored in three other places -- see
    # tests/test_install_type_buckets.py, which fails if they drift.
    INSTALL_TYPES = ("docker", "pip", "electron", "other", "dev")

    #: Token in ``frontend/index.html`` replaced with the detected install type
    #: when the server hands the SPA out. The frontend cannot wait for
    #: ``GET /version``: the version check runs from a child component's
    #: ``onMounted`` and stamps its 24h throttle before the request, so whatever
    #: it knows at that instant is what gets reported for the day. Passing the
    #: value in the document removes the race for every channel at once, and
    #: keeps the backend the single source of truth -- no bucket list is
    #: duplicated frontend-side (see tests/test_install_type_buckets.py).
    INSTALL_TYPE_PLACEHOLDER = "__PIXLSTASH_INSTALL_TYPE__"

    #: Marks the host as a development machine, whatever channel it runs.
    #:
    #: Needed because ``PIXLSTASH_INSTALL_TYPE`` is not only a telemetry label:
    #: the exact value ``electron`` is a runtime switch, gating ``cookie_secure``,
    #: the loopback listener in :meth:`run` and the external-listener startup
    #: check. The desktop shell therefore has to keep declaring ``electron`` even
    #: when a developer's environment says ``dev``, and it sets this instead so
    #: the machine can still be labelled ours without changing how it runs.
    #:
    #: Set to 1 by the shell for a dev backend (``PIXLSTASH_DESKTOP_DEV``); a
    #: developer running a *bundled* desktop build can export it by hand.
    DEV_MACHINE_ENV_VAR = "PIXLSTASH_TELEMETRY_DEV"

    @staticmethod
    def detect_install_type() -> str:
        """Return the install type for telemetry: one of ``INSTALL_TYPES``.

        Resolution order:

        0. :data:`DEV_MACHINE_ENV_VAR` - declares the *machine* rather than the
           channel, so it outranks everything below it. Reported as ``dev``,
           which the metrics collector subtracts from active installs.
        1. ``PIXLSTASH_INSTALL_TYPE`` override - if set to one of the allowed
           values it wins outright, letting an installer declare its channel
           (e.g. ``other`` for the Windows build) without a code change. An
           empty or invalid value is ignored (and logged) so a typo can never
           leak a junk value into telemetry.
        2. Docker detection (:meth:`running_in_docker`) - the reliable signal.
        3. Default to ``pip``.

        The return value is guaranteed to be a member of ``INSTALL_TYPES``.
        """
        dev_marker = os.environ.get(Server.DEV_MACHINE_ENV_VAR, "").strip().lower()
        if dev_marker in {"1", "true", "yes", "on"}:
            logger.info(
                "%s=%r declares a development machine; reporting install_type='dev'.",
                Server.DEV_MACHINE_ENV_VAR,
                dev_marker,
            )
            return "dev"

        override = os.environ.get("PIXLSTASH_INSTALL_TYPE", "").strip().lower()
        if override:
            if override in Server.INSTALL_TYPES:
                logger.info(
                    "Using PIXLSTASH_INSTALL_TYPE override for install_type=%r.",
                    override,
                )
                return override
            logger.warning(
                "Ignoring invalid PIXLSTASH_INSTALL_TYPE=%r (allowed: %s); "
                "falling back to automatic detection.",
                override,
                ", ".join(Server.INSTALL_TYPES),
            )

        if Server.running_in_docker():
            return "docker"
        return "pip"

    def __init__(
        self,
        server_config_path,
        path_map: dict[str, str] | None = None,
        legacy_identity_prompt=None,
        library_switch_prompt=None,
    ):
        """
        Initialize the Server instance.

        Args:
            server_config_path (str): Path to the server-only config file.
            path_map: Optional dict mapping host path prefixes to their
                container equivalents. Set by the ``--path-map`` CLI arg.
            legacy_identity_prompt: Optional callable passed straight through
                to :func:`bootstrap_hub`, asked to authorize a detected but
                unprepared legacy vault instead of requiring
                ``pixlstash-cli libraries prepare-legacy-identity`` first.
            library_switch_prompt: Optional callable passed straight through to
                :func:`bootstrap_hub`, offered the attached libraries that still
                open when the active one's vault has gone missing.
        """
        # Boot-time instrumentation (issue: v1.11.0 startup latency). Local,
        # operator-visible stage timings only - no telemetry, nothing leaves
        # the process. Same perf_counter-and-log shape TaskRunner already uses
        # for per-task timing (pixlstash/task_runner.py). Started before the
        # gc.collect() below so GC time is counted as its own stage rather
        # than silently excluded from "Server.__init__ total".
        _boot_t0 = time.perf_counter()
        _stage_t = _boot_t0

        def _log_stage(name: str) -> None:
            nonlocal _stage_t
            now = time.perf_counter()
            logger.info("[boot] %s: %.3fs", name, now - _stage_t)
            _stage_t = now

        # Ensure garbage collection before starting server to free up memory.
        # This is mainly to ensure repeated runs within the testing framework do not accumulate memory usage.
        gc.collect()
        _log_stage("gc.collect()")

        self._server_config_path = server_config_path
        self.path_mapper = PathMapper(path_map)

        # Before init_server_config, deliberately: ensure_install_identity reads
        # the presence of the server config to decide whether this is a fresh
        # install or an existing one upgrading in. Once init_server_config has
        # run, that file always exists and the distinction is gone. The ID is
        # written locally and transmitted by nothing in this release.
        ensure_install_identity(server_config_path)

        self._server_config = self.init_server_config(server_config_path)
        self._startup_check_report = StartupChecks(
            server_config=self._server_config,
            server_config_path=self._server_config_path,
            logger=logger,
        ).run()
        _log_stage("startup checks (disk/VRAM/CUDA/SSL)")
        persist_server_config(server_config_path, self._server_config)

        # Internal loopback transport (Electron desktop shell).
        #
        # The desktop app launches this backend purely as a private, in-process
        # service: a free *ephemeral* port on 127.0.0.1, spoken to over plain
        # HTTP (see electron/src/backend/ServerProcess.ts - it hardcodes
        # http://127.0.0.1:<port>, health-checks it over node:http, and only
        # whitelists http loopback). That internal transport is intrinsic to the
        # loopback and must NOT be derived from server-config: a config whose
        # require_ssl is enabled for *external* exposure must not turn this
        # internal connection into HTTPS, or the shell can never reach it.
        #
        # The loopback scheme/host/port are therefore derived independently in
        # run() (see _run_electron_listeners) - always plain HTTP on the
        # PIXLSTASH_HOST/PIXLSTASH_PORT the shell forces. require_ssl /
        # ssl_keyfile / ssl_certfile are left untouched here because they now
        # govern only the optional *external* listener the desktop app can
        # enable, never this internal connection.
        #
        # cookie_secure is the one thing we must still pin off: the cookie jar is
        # process-wide (shared by both listeners) and the window speaks HTTP to
        # the loopback, so a Secure cookie would be dropped and the window could
        # never authenticate. The external HTTPS listener is a different origin
        # and simply serves the same (non-Secure) cookie over TLS. Applied only
        # to the in-memory copy; the on-disk config keeps the user's settings.
        if os.environ.get("PIXLSTASH_INSTALL_TYPE", "").strip().lower() == "electron":
            self._server_config["cookie_secure"] = False

        # SSL config
        if self._server_config.get("require_ssl", False):
            self._ensure_ssl_certificates()

        logger.debug(
            "Creating Vault instance with image root: "
            + str(self._server_config["image_root"])
        )

        register_heif_opener()

        # The hub decides which library opens, not server config. On a first run
        # it registers configured image_root as library 1, but never treats the
        # config or the vault's mere presence as identity-import authority. Only
        # the desktop preparer's durable hub operation can authorize that copy.
        # From then on the registry's active row wins.
        # The hub sits beside server-config.json rather than at a fixed platform
        # path, so it follows ``--server-config`` wherever it points. That is
        # what the plan specifies (the hub location *is* the config-dir decision,
        # issue #168), and it means a test or an alternate deployment gets its
        # own hub instead of reaching into the user's real one.
        hub_path = os.path.join(os.path.dirname(self._server_config_path), "hub.db")
        self._hub_bootstrap = bootstrap_hub(
            self._server_config["image_root"],
            hub_path,
            legacy_identity_prompt=legacy_identity_prompt,
            library_switch_prompt=library_switch_prompt,
        )
        self.hub = self._hub_bootstrap.hub
        self.library_registry = LibraryRegistry(self.hub)
        # Exactly one managed model folder always exists, created on first run
        # beside the hub. Without it a fresh install has nowhere to drop or
        # import a model into, so drag-in would be impossible; see
        # services/managed_model_store.py for why it is `managed` rather than a
        # seeded `user` folder nobody is allowed to remove.
        ensure_managed_folder(self.hub, os.path.dirname(self._server_config_path))
        _log_stage("hub bootstrap (open/migrate hub.db)")
        # The three roots PixlStash's models live in, declared rather than
        # scanned. Off in the test suite: they are machine-global, so a Server
        # on a temp config dir would otherwise describe whichever engines the
        # developer's home happens to hold.
        if Server.DEFAULT_DECLARE_MODEL_ROOTS:
            # PixlStash's own engines: we downloaded them, so we know what they
            # are without reading a header - and half of them are ONNX or `.pt`,
            # which the scanner does not yield at all. Cheap: existence checks
            # and a handful of upserts, no hashing.
            try:
                declare_builtin_models(self.hub, builtin_model_dir())
            except (sqlite3.Error, OSError) as exc:
                # The shelf losing its engine rows is not a reason to refuse to
                # start; everything else on it still works. `OSError` as well as
                # the database errors: the declaration walks a machine-global
                # directory that may be unreadable, on a different mount, or
                # gone, and this comment promised non-critical while the handler
                # only covered half of what the call can raise.
                logger.error(
                    "Could not declare the built-in model folder (%s); "
                    "PixlStash's own engines will not be listed on the shelf "
                    "this session.",
                    exc,
                )
            # The other two roots models land in. Same deal as above and same
            # failure policy: each is declared independently so one unreadable
            # root cannot cost the shelf the other two.
            for label, resolve, declare in (
                (
                    "InsightFace packs",
                    insightface_models_dir,
                    declare_insightface_packs,
                ),
                ("HuggingFace cache", huggingface_cache_dir, declare_huggingface_cache),
            ):
                try:
                    folder_path = resolve()
                    if folder_path:
                        declare(self.hub, folder_path)
                except (sqlite3.Error, OSError) as exc:
                    logger.error(
                        "Could not declare the %s (%s); it will not be listed "
                        "on the shelf this session.",
                        label,
                        exc,
                    )
        _log_stage("model shelf declarations (builtin/insightface/huggingface)")
        if self._hub_bootstrap.migrated:
            logger.info(
                "First run after the hub/vault split: identity now lives in %s",
                self.hub.path,
            )

        self.vault = self._open_registered_vault()
        self._hub_bootstrap.library = (
            self.library_registry.by_uuid(self._hub_bootstrap.library.uuid)
            or self._hub_bootstrap.library
        )
        _log_stage("vault opened (VaultDatabase open/migrate + task-runner wiring)")

        self._ws_clients = []
        self._ws_clients_lock = threading.Lock()
        self._ws_loop = None
        self.vault.add_event_listener(self.handle_vault_event)

        # Identity comes from the hub, never from the active vault. Pointing this
        # at ``self.vault.db`` would mean switching library switches *who you
        # are*: the owner would be logged out, or logged into whatever user row
        # the other library happened to carry.
        self.auth = AuthService(
            self._hub_bootstrap.engine,
            self._server_config,
            self._server_config_path,
            logger,
        )
        # Tokens are stamped with the library that is active when they are
        # minted. Resolved through the registry on every mint rather than
        # captured here, so a token minted after a library switch belongs to the
        # library the user is actually looking at.
        self.auth.library_uuid_provider = self._active_library_uuid
        # Guest sessions are per-library and stay in the vault, so the auth
        # service needs a handle on it as well as on the hub. Re-pointed when
        # the active library changes.
        self.auth.vault_db = self.vault.db

        # A full restore swaps the database file underneath the running server,
        # which invalidates the token cache and every in-memory session. Give
        # the restore path a way to reach the auth service so it can clear them
        # (issue #666, §18.5).
        self.vault.auth_service = self.auth
        self._user = self.auth.ensure_user()
        # Headless installs (Docker): claim the still-unclaimed owner account
        # from PIXLSTASH_INITIAL_USERNAME/PIXLSTASH_INITIAL_PASSWORD, because
        # the loopback-only first-owner registration gate is unreachable from
        # inside a container. No-op when the vars are unset or the account is
        # already claimed. This is the single startup chokepoint for env
        # provisioning, regardless of how the server was launched.
        if self.auth.claim_owner_from_env():
            self._user = self.auth.user
        # Desktop (Electron) shell: register the pre-authenticated loopback
        # owner session so the local window opens straight into the library.
        # No-op for every other install type (env var unset).
        self.auth.seed_desktop_session()
        self.apply_user_settings_to_vault(self.vault)
        self.reconcile_library_settings(self.vault)
        self.vault.start()
        _log_stage("auth wired + background workers started")

        # Owns replacing the vault when the active library changes, and the
        # state requests are refused in while that is happening.
        self.library_coordinator = LibraryGenerationCoordinator(self)
        self.library_switch = LibrarySwitchService(self)

        self.api = FastAPI(
            title="PixlStash API",
            version=self._get_version(),
            description=API_DESCRIPTION,
            openapi_tags=API_OPENAPI_TAGS,
            lifespan=self.lifespan,
            redoc_url=None,
        )

        # CORS: always allow localhost/127.0.0.1 on any port plus the machine's
        # own LAN IP (any port) so the Vite dev server works over LAN without
        # any extra configuration. Additional origins can be added via cors_origins.
        self.allow_origins = list(self._server_config.get("cors_origins") or [])
        _cors_hosts = ["localhost", r"127\.0\.0\.1"]
        _lan_ip = _get_lan_ip()
        if _lan_ip and _lan_ip not in ("127.0.0.1", "localhost"):
            _cors_hosts.append(re.escape(_lan_ip))
        self.allow_origin_regex = r"^https?\://(" + "|".join(_cors_hosts) + r")(:\d+)?$"
        self.api.add_middleware(
            CORSMiddleware,
            allow_origins=self.allow_origins,
            allow_origin_regex=self.allow_origin_regex,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        # Captures the originating tab's ``X-Client-Id`` header into
        # ``request.state.origin_client_id`` / ``origin_client_id_var`` so
        # mutation handlers can echo it back on the WebSocket event envelope.
        # Echo-matching only - never used for authz/scoping.
        self.api.add_middleware(OriginClientMiddleware)
        # Centralised authorization gate (Phase 1 of the authz refactor;
        # docs/backend_architecture.md §16.2). Attached as a router-level
        # dependency on every include_router in _setup_routes, then resolved
        # against the mounted routes below. Report-only at the shipped default
        # (AUTHZ_GATE_ENFORCING=False): it denies nothing and only logs the
        # undeclared-route backlog. The auth service is injected so the Step-3
        # owner-class enforcement (OWNER_ONLY/LOCAL_OWNER_ONLY) can delegate to
        # require_unscoped_owner/real_client_ip once the flag is enforcing; an
        # enforcing gate with owner-class routes but no auth boot-fails.
        self.authz = AuthzGate(
            enforcing=AUTHZ_GATE_ENFORCING, auth=self.auth, server=self
        )
        self._add_cors_exception_handler()
        self._setup_routes()
        self._install_custom_openapi()
        # Build the route-identity policy map now that every router is mounted,
        # and print the undeclared-route backlog (or, when enforcing, fail boot on
        # any undeclared/dead route). Consumes the same route walk as the CI
        # coverage-matrix guardrail so the two can never disagree.
        self.authz.enforce_startup(self.api)
        from pixlstash.middleware.library_admission import LibraryAdmissionMiddleware

        # Added last so Starlette makes it outermost: its lease begins before
        # authentication and ends only after the final ASGI body frame.
        self.api.add_middleware(LibraryAdmissionMiddleware, server=self)
        _log_stage("FastAPI app built (middleware + routes + authz gate)")

        # Temporary storage for export tasks
        self.export_tasks = {}

        # Temporary storage for import tasks
        self.import_tasks = {}
        # The one in-flight folder-structure read (v1.11 Phase 2). A single slot
        # rather than a dict: the mapping screen only ever shows one read, and a
        # second concurrent one would fight the first for the same GPU queue.
        self.folder_structure_read = None
        self.folder_structure_lock = threading.Lock()
        # The one in-flight (or last-settled) mapping commit (v1.11 Phase 3).
        # Single slot for the same reason as the read above, and because a
        # second commit while one runs would race the first over the same
        # reference folder's scan.
        self.folder_structure_commit = None
        self.folder_structure_commit_lock = threading.Lock()
        # An import killed between indexing and assigning left a library half
        # made and no way to ask for the rest - the accepted mapping only ever
        # lived in this slot. It is written to the vault now, so start-up can
        # finish the job. Here rather than in the router factory that defines
        # it, because that factory runs before the two lines above.
        resume_mapping_commit = getattr(self, "resume_folder_mapping_commit", None)
        if resume_mapping_commit is not None:
            resume_mapping_commit()
        # Temporary storage for async streaming-import staging sessions (#459).
        # Keyed by staging_id; each records the on-disk staging dir, the streamed
        # files, the declared file count, and (after the safe handoff) the
        # background PictureImportTask id.
        self.staging_sessions = {}
        self._shutdown_on_lifespan = False
        self._telemetry_thread = None
        logger.info(
            "[boot] Server.__init__ total: %.3fs", time.perf_counter() - _boot_t0
        )

    def _maybe_send_telemetry_ping(self) -> None:
        """Send the daily install ping, if the owner has turned it on.

        Runs from the server process rather than the browser on purpose: the
        update check in ``useVersionCheck.js`` is frontend-only and
        localStorage-gated, so a headless install would otherwise only ever
        report when somebody opened the web UI. The retention design assumes
        Docker installs ping daily because they are persistent services, and
        that is only true if the ping comes from here.

        Runs a daily loop rather than firing once: a container that stays up
        for six weeks would otherwise ping once, and Docker installs pinging
        daily is the assumption the whole retention design rests on.

        Never raises and never blocks startup: it hands off to a daemon thread
        and returns. A failure to report is not worth degrading the server for.
        """
        try:
            if self._telemetry_thread is not None:
                return

            def consent_is_on() -> bool:
                """Re-read consent from the live user row on every cycle.

                A boolean captured at startup keeps transmitting after the user
                has opted out, and the window between opting out and the next
                restart is exactly when honouring it matters.
                """
                # `auth.user` and `_user` are detached startup snapshots. The
                # config PATCH writes through a fresh ORM session, so reading
                # either cached object here misses both opt-ins and opt-outs
                # until the process restarts. An hourly DB read is negligible
                # and makes persisted consent the single source of truth.
                user = self.auth.get_user()
                return bool(getattr(user, "telemetry_send_install_id", False))

            self._telemetry_thread = start_periodic_sender(
                self._server_config_path,
                Server.detect_install_type(),
                consent_is_on,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            # Deliberately NOT a bare `except Exception`. A broad catch here
            # swallowed an AttributeError from a wrong method name, so the ping
            # silently never sent while the UI reported consent as honoured.
            # A programming error must surface, not be logged as a warning.
            logger.warning(
                "Could not dispatch the telemetry ping (%s); continuing. This "
                "affects reporting only, never the running server.",
                exc,
            )

    @property
    def server_config_path(self) -> str:
        """Path to the server-only config file this server was started with.

        Read-only. The telemetry install ID is stored beside this file, so it
        follows a custom ``--server-config`` location.
        """
        return self._server_config_path

    def __enter__(self):
        # Allow use as a context manager for robust cleanup
        return self

    def _close_active_vault(self) -> None:
        """Close the vault and remove generation-bound temporary artifacts."""
        vault = getattr(self, "vault", None)
        if vault is None:
            return
        image_root = vault.image_root
        try:
            vault.close()
        finally:
            library_switch = getattr(self, "library_switch", None)
            if library_switch is not None:
                library_switch._clear_generation_retained_state(image_root)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self) -> None:
        """Close the vault AND the hub.

        The one supported teardown outside a ``with`` block. Closing only the
        vault (``server.vault.close()``) leaks the hub's SQLite connection -
        harmless on POSIX, where an open file can still be unlinked, but fatal
        on Windows, where TemporaryDirectory cleanup then fails with a sharing
        violation on ``hub.db``. That leak was every remaining failure in
        backend-windows shard 2 once the earlier startup defects were fixed.
        """
        if getattr(self, "vault", None) is not None:
            logger.info("Closing the vault and cleaning up resources")
            self._close_active_vault()
        self._close_hub()
        gc.collect()

    def request_fatal_shutdown(self) -> None:
        """Ask every programmatic listener to exit after fatal vault loss."""
        self._fatal_shutdown_requested = True
        for listener in getattr(self, "_uvicorn_servers", ()):
            listener.should_exit = True

    def apply_user_settings_to_vault(self, vault: Vault) -> None:
        """Push the owner's stored settings onto *vault*.

        Shared by startup and by switching library. The settings live in the hub
        and so survive a switch; the vault they are applied to does not, which is
        why this has to run again for every vault the process opens.
        """
        if self._user and self._user.description is not None:
            vault.set_description(self._user.description)
        vault.set_keep_models_in_memory(
            getattr(self._user, "keep_models_in_memory", True)
        )
        effective_vram_gb = (
            Server.DEFAULT_MAX_VRAM_GB
            if Server.DEFAULT_MAX_VRAM_GB is not None
            else getattr(self._user, "max_vram_gb", None)
        )
        vault.set_max_vram_usage_gb(effective_vram_gb)
        # Initialise tagger_settings from the stored JSON (fills defaults for any
        # missing plugin entries so the engine always has a complete config).
        if self._user is not None:
            import json as _json
            from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

            raw_settings = getattr(self._user, "tagger_settings", None)
            if raw_settings:
                try:
                    parsed = _json.loads(raw_settings)
                except (ValueError, TypeError):
                    parsed = {}
            else:
                parsed = {}
            filled = get_tagger_plugin_manager().fill_defaults(parsed)
            vault.set_tagger_settings(filled)

    def reconcile_library_settings(self, vault: Vault, library=None) -> None:
        """Repair scores this library missed while it was closed.

        Changing the penalised-tag weights invalidates cached smart scores in
        whichever library is open at the time. A library that was closed then
        never finds out, and nothing revisits it, because a NULL score is the
        only signal that a recompute is owed. Opening the library is the one
        moment that question is answerable, so it is asked here.

        The comparison is against a keyed hash: the library stores no settings,
        only a fingerprint, and the key lives in the hub. Penalised and hidden
        tags say what someone collects and what they hide, and a library folder
        is made to be copied and handed to other people.
        """
        # The library is passed explicitly by the switch path, which reconciles
        # *before* it marks the target active: reading "the active library" there
        # would return the one being closed, and key the fingerprint with the
        # wrong salt.
        library = library or self.library_registry.active_library()
        if library is None:
            return
        try:
            penalised = smart_score_penalised_tags(
                getattr(self._user, "smart_score_penalised_tags", None),
                DEFAULT_SMART_SCORE_PENALIZED_TAGS,
            )
            library_settings_service.reconcile_settings_fingerprint(
                vault.db, library.settings_salt, penalised
            )
        except Exception:
            # A failed reconcile leaves scores as they were, which is the state
            # the library was already in. Never worth failing a startup or a
            # switch over.
            logger.exception(
                "Could not reconcile the settings fingerprint for library %s",
                library.name,
            )

    def _open_registered_vault(self) -> Vault:
        """Open the active library, turning a dead end into an answerable question.

        ``validate_vault_folder`` reads ``sqlite_master`` and nothing else, so a
        vault can pass registration and still be one no migration path reaches -
        a schema stamped at a baseline it predates dies here, on a table it
        never had. Before this, that was a SQLAlchemy traceback on the desktop
        splash screen with no way forward. Now it is the same offer an
        unreadable file gets: start over with an empty database, keeping the old
        one. Environmental failures (locked, full, unreadable) are re-raised
        untouched - see :func:`unusable_vault_from_open_failure` - and the
        original exception is logged in full either way.
        """
        library = self._hub_bootstrap.library
        try:
            return self.build_vault(
                registered_vault_path(self.hub, library, self._hub_bootstrap)
            )
        except (SQLAlchemyError, AlembicCommandError) as exc:
            unusable = unusable_vault_from_open_failure(library, exc)
            if unusable is None:
                raise
            logger.exception(
                "Could not open the library database at %s", library.vault_path
            )
            if os.environ.get(VAULT_RECREATE_ENV) != "1":
                raise unusable from exc

        # Authorised by a human on the launch that failed. The half-built vault
        # above is unreachable and its engine is collected with it; nothing here
        # can reuse a connection that was mid-migration when it raised.
        set_aside_unusable_vault(library.vault_path)
        library = self.library_registry.forget_vault_fingerprint(library)
        self._hub_bootstrap.library = library
        return self.build_vault(
            registered_vault_path(self.hub, library, self._hub_bootstrap)
        )

    def build_vault(self, image_root: str) -> Vault:
        """Construct (but do not start) a Vault over *image_root*.

        Shared by startup and by switching library, so a vault opened by a
        switch is configured exactly like one opened at boot. Anything that
        diverges here is a bug that only shows up after the first switch, which
        is the hardest kind to find.
        """
        startup_forced_cpu = self._startup_check_report.get("forced_cpu", False)
        force_cpu = (
            Server.DEFAULT_FORCE_CPU
            if Server.DEFAULT_FORCE_CPU is not None
            else startup_forced_cpu
        )
        return Vault(
            image_root=image_root,
            description=User().description,
            server_config_path=self._server_config_path,
            path_mapper=self.path_mapper,
            disable_background_workers=self._server_config.get(
                "disable_background_workers", False
            ),
            force_cpu=bool(force_cpu),
            fast_captions=Server.DEFAULT_FAST_CAPTIONS,
            daily_snapshots_enabled=self._server_config.get("daily_snapshots", True),
            insightface_model_pack=self._server_config.get(
                "insightface_model_pack", "buffalo_l"
            ),
            scrapheap_retention_days=scrapheap_service.read_retention_days(
                self._server_config
            ),
            scrapheap_retention_reduced_at=scrapheap_service.read_retention_reduced_at(
                self._server_config
            ),
        )

    def _active_library_uuid(self):
        """Return the active library's uuid, for stamping newly minted tokens."""
        library = self.library_registry.active_library()
        return library.uuid if library else None

    @property
    def library_generation(self) -> int:
        """Ephemeral context fence for async work spanning a library switch."""
        service = getattr(self, "library_switch", None)
        return service.generation if service is not None else 0

    @property
    def hub_engine(self):
        """The database identity lives in, which is the hub and not the vault.

        Anything reading or writing the user or its tokens goes through this.
        Reaching for ``vault.db`` instead would work against whichever library
        happens to be active, which is precisely the bug the split removes.
        """
        return self._hub_bootstrap.engine

    def _close_hub(self):
        """Release the hub connection and its engine, if they were opened.

        Separate from the vault's teardown because the two have different
        lifetimes: switching library closes and reopens the vault while the hub
        stays open for the life of the process.
        """
        bootstrap = getattr(self, "_hub_bootstrap", None)
        if bootstrap is None:
            return
        bootstrap.engine.close()
        bootstrap.hub.close()

    def run(self):
        self._shutdown_on_lifespan = True
        version = self._get_version()
        host = self._server_config.get("host", "127.0.0.1")
        port = self._server_config.get("port", 9537)
        # Env overrides (honoured by the Docker entrypoint and the Electron
        # desktop shell, which binds the backend to a free loopback port).
        # An unset/blank value leaves the config-derived value untouched.
        host = os.environ.get("PIXLSTASH_HOST", "").strip() or host
        _port_override = os.environ.get("PIXLSTASH_PORT", "").strip()
        if _port_override:
            try:
                port = int(_port_override)
            except ValueError:
                logger.warning(
                    "Ignoring invalid PIXLSTASH_PORT=%r (not an integer).",
                    _port_override,
                )
        # Desktop shell: the window reaches the backend over an ephemeral
        # loopback HTTP port, and remote access (if enabled) is a *second*,
        # independent listener. This needs two bound sockets in one process,
        # which uvicorn.run() can't express, so it has its own launch path.
        if os.environ.get("PIXLSTASH_INSTALL_TYPE", "").strip().lower() == "electron":
            self._run_electron_listeners(version, host, port)
            return

        scheme = "https" if self._server_config.get("require_ssl", False) else "http"
        server_url = f"{scheme}://{host}:{port}"
        self._print_banner(version, [("Server", server_url)])
        uvicorn_kwargs = dict(
            host=host,
            port=port,
            log_config=uvicorn_log_config,
        )
        if self._server_config.get("require_ssl", False):
            uvicorn_kwargs["ssl_keyfile"] = self._server_config.get("ssl_keyfile")
            uvicorn_kwargs["ssl_certfile"] = self._server_config.get("ssl_certfile")
            print(
                f"[SSL] Running with SSL: keyfile={self._server_config.get('ssl_keyfile')}, certfile={self._server_config.get('ssl_certfile')}"
            )
        # Keep the concrete listener handle so a fatal post-retirement switch
        # failure can terminate the process just as it does in Electron's
        # multi-listener path. ``uvicorn.run`` hides that handle.
        listener = uvicorn.Server(uvicorn.Config(self.api, **uvicorn_kwargs))
        self._uvicorn_servers = [listener]
        try:
            listener.run()
        except KeyboardInterrupt:
            # Ctrl-C is how a foreground server is stopped, not a crash, so it
            # must not print a traceback. ``uvicorn.run()`` swallows this for
            # exactly that reason; constructing the listener above to keep its
            # handle opts out of that wrapper, so the suppression has to be
            # repeated here. SIGTERM is unaffected either way: uvicorn captures
            # it, sets ``should_exit`` and returns normally.
            logger.info("Interrupted; shutting down.")
        finally:
            if getattr(self, "vault", None) is not None:
                self._close_active_vault()
            self._close_hub()

    @asynccontextmanager
    async def lifespan(self, app):
        # Startup logic
        loop = asyncio.get_running_loop()
        # Only claim _ws_loop if nothing else (e.g. a WebSocket handler) has set it
        # yet. This avoids overwriting the WebSocket loop when TestClient creates a
        # fresh event loop per HTTP request.
        was_set_by_us = self._ws_loop is None
        if was_set_by_us:
            self._ws_loop = loop
        if Server.DEFAULT_CLEANUP_MISSING_PICTURES:
            await loop.run_in_executor(None, self._cleanup_missing_pictures)
        # Thumbnail generation is NOT run here anymore. It used to block startup
        # (awaited before vault.start()), which on a large library - or after the
        # v1.8.0 upgrade reset thumbnails to NULL - held the server unusable for
        # many minutes. It was also redundant: the blocking pass wrote only the
        # file, never the thumbnail_width/square_crop columns, so the background
        # MissingThumbnailFinder (keyed on thumbnail_width IS NULL) regenerated
        # every picture again. That finder now solely owns generation - it runs
        # after vault.start() (non-blocking) and reports progress via
        # get_worker_progress, which the in-app upgrade bar consumes.
        self.vault.start()
        self._maybe_send_telemetry_ping()
        if os.environ.get("PIXLSTASH_INSTALL_TYPE", "").strip().lower() == "electron":
            # The desktop window uses the ephemeral loopback HTTP port (env), not
            # the configured host/port - those describe the optional external
            # listener and only apply when it is enabled. Report what is actually
            # serving rather than the config, which previously logged a phantom
            # https://0.0.0.0:<port> URL even with remote access off.
            loop_host = os.environ.get("PIXLSTASH_HOST", "").strip() or "127.0.0.1"
            loop_port = os.environ.get(
                "PIXLSTASH_PORT", ""
            ).strip() or self._server_config.get("port", 9537)
            logger.info(
                "PixlStash is ready (desktop window): http://%s:%s/",
                loop_host,
                loop_port,
            )
            if self._server_config.get("external_server_enabled", False):
                # Only report remote access as active when the external listener
                # actually bound. _build_electron_configs refuses to bind it when
                # the owner has no password (a LAN device could otherwise claim
                # the empty account); gate this log on the same condition so it
                # never claims "enabled" for a listener that was refused.
                if Server._external_listener_password_ready(self):
                    ext_scheme = (
                        "https"
                        if self._server_config.get("require_ssl", False)
                        else "http"
                    )
                    logger.info(
                        "Remote access enabled: %s://0.0.0.0:%s/",
                        ext_scheme,
                        self._server_config.get("port", 9537),
                    )
                else:
                    logger.warning(
                        "Remote access is configured but NOT active: the owner "
                        "account has no password set, so the external listener "
                        "was refused. Set an owner password (Settings → "
                        "Account), then restart, to expose it on the network."
                    )
        else:
            host = self._server_config.get("host", "127.0.0.1")
            port = self._server_config.get("port", 9537)
            scheme = (
                "https" if self._server_config.get("require_ssl", False) else "http"
            )
            logger.info(
                "PixlStash is ready. Open in your browser: %s://%s:%s/",
                scheme,
                host,
                port,
            )
        yield
        # Shutdown logic - only clear _ws_loop if this lifespan instance set it
        if was_set_by_us:
            self._ws_loop = None
        if self._shutdown_on_lifespan and hasattr(self, "vault"):
            self._close_active_vault()

    @staticmethod
    def init_server_config(server_config_path):
        config_dir = os.path.dirname(server_config_path)
        # This directory contains the hub credential store. A plain makedirs()
        # becomes 0775 under Linux's common umask 0002, after which the SQLite
        # guard correctly refuses the directory the app itself just created.
        mkdir_private(Path(config_dir))

        # SSL certs are always stored in the platform user-config dir so they
        # stay in a consistent, writable location regardless of where the
        # server-config file itself resides (e.g. a custom --server-config path).
        _ssl_dir = os.path.join(user_config_dir("pixlstash"), "ssl")
        default_log_path = os.path.join(config_dir, "server.log")
        default_ssl_cert_path = os.path.join(_ssl_dir, "cert.pem")
        default_ssl_key_path = os.path.join(_ssl_dir, "key.pem")
        default_image_root = os.path.join(config_dir, "images")

        server_config = {}
        if not os.path.exists(server_config_path):
            server_config = {
                "host": "localhost",
                "port": 9537,
                "log_level": "info",
                "log_file": default_log_path,
                "require_ssl": False,
                # ssl_keyfile / ssl_certfile are added by the require_ssl
                # gating block below - only when SSL is actually enabled.
                # Whether the desktop app exposes a second, external listener on
                # host:port (loopback access is always on; see run()). Ignored by
                # the standalone server, which binds host:port directly.
                "external_server_enabled": False,
                "cookie_samesite": "Lax",
                "cookie_secure": False,
                "image_root": default_image_root,
                "default_device": "auto",
                "insightface_model_pack": "buffalo_l",
                "min_free_disk_gb": 1.0,
                "min_free_vram_mb": 1024.0,
                "cors_origins": [],
                "max_attachment_size_mb": 50,
                "filesystem_roots": [],
            }
            write_json_atomic(server_config_path, server_config)
        else:
            with open(server_config_path, "r") as f:
                server_config = json.load(f)

                # Ensure server config options exist
                if "host" not in server_config:
                    server_config["host"] = "localhost"
                if "port" not in server_config:
                    server_config["port"] = 8000
                if "log_level" not in server_config:
                    server_config["log_level"] = "info"
                if "log_file" not in server_config:
                    server_config["log_file"] = default_log_path
                if "require_ssl" not in server_config:
                    server_config["require_ssl"] = False
                if "external_server_enabled" not in server_config:
                    server_config["external_server_enabled"] = False
                if "cookie_samesite" not in server_config:
                    server_config["cookie_samesite"] = "Lax"
                if "cookie_secure" not in server_config:
                    server_config["cookie_secure"] = False
                if "image_root" not in server_config:
                    server_config["image_root"] = default_image_root
                if "default_device" not in server_config:
                    server_config["default_device"] = "auto"
                if "insightface_model_pack" not in server_config:
                    server_config["insightface_model_pack"] = "buffalo_l"
                if "min_free_disk_gb" not in server_config:
                    server_config["min_free_disk_gb"] = 1.0
                if "min_free_vram_mb" not in server_config:
                    server_config["min_free_vram_mb"] = 1024.0
                if "cors_origins" not in server_config:
                    server_config["cors_origins"] = []
                if "max_attachment_size_mb" not in server_config:
                    server_config["max_attachment_size_mb"] = 50
                if "generate_thumbnails_on_startup" not in server_config:
                    server_config["generate_thumbnails_on_startup"] = True
                if "filesystem_roots" not in server_config:
                    server_config["filesystem_roots"] = []
                if "daily_snapshots" not in server_config:
                    server_config["daily_snapshots"] = True

        # Inference-device override. The Electron desktop launches the backend
        # with its own --server-config and selects the device by which runtime is
        # active: the bundled env ships CPU-only torch, GPU wheels are added on
        # demand as overlays. It passes the active runtime's device via
        # PIXLSTASH_DEFAULT_DEVICE so the backend uses what the runtime actually
        # provides, regardless of the config's default_device (which can't know
        # whether torch is the CPU build or a GPU overlay). General-purpose: a
        # Docker deploy can use it too. Blank/unset leaves the config untouched.
        _device_override = (
            os.environ.get("PIXLSTASH_DEFAULT_DEVICE", "").strip().lower()
        )
        if _device_override:
            # Validate against the known device values (the same set
            # StartupChecks accepts). An invalid value is rejected with a
            # warning and ignored, leaving the config's own default_device in
            # place, rather than being written straight through and silently
            # falling back to CPU with no explanation.
            _valid_devices = {"cpu", "cuda", "gpu", "auto"}
            if _device_override in _valid_devices:
                # Remember what the owner configured: persist_server_config
                # writes that back, never the runtime's answer.
                server_config[DEVICE_ON_DISK_KEY] = server_config.get("default_device")
                server_config["default_device"] = _device_override
            else:
                logger.warning(
                    "Ignoring invalid PIXLSTASH_DEFAULT_DEVICE=%r; expected one "
                    "of %s. Keeping configured default_device=%r.",
                    _device_override,
                    sorted(_valid_devices),
                    server_config.get("default_device"),
                )

        # SSL key/cert paths live in the config *only* when SSL is enabled.
        # When require_ssl is off they are never read (see _ensure_ssl_certificates
        # and the uvicorn launch, both guarded by require_ssl), so persisting
        # them just clutters the user's config - and re-injecting them on every
        # boot means a user who deletes them sees them reappear. Add the
        # defaults when SSL is on; strip them when it is off so existing
        # pollution self-heals on the next write.
        if server_config.get("require_ssl", False):
            server_config.setdefault("ssl_keyfile", default_ssl_key_path)
            server_config.setdefault("ssl_certfile", default_ssl_cert_path)
        else:
            server_config.pop("ssl_keyfile", None)
            server_config.pop("ssl_certfile", None)

        # Resolve SSL paths that are relative: interpret them relative to the
        # config file's directory, not the process's CWD, so that the certs
        # always live alongside the config regardless of where the server is
        # launched from.
        for key in ("ssl_keyfile", "ssl_certfile"):
            value = server_config.get(key)
            if value and not os.path.isabs(value):
                server_config[key] = os.path.join(config_dir, value)

        # Apply any test-level port override (set by the pytest conftest before
        # Server is instantiated). This lets tests run on a free port even when
        # the production server is already occupying the configured port.
        if Server.DEFAULT_PORT is not None:
            server_config["port"] = Server.DEFAULT_PORT

        # Apply any test-level InsightFace model-pack override so tests can force
        # a pack without writing JSON (mirrors DEFAULT_FORCE_CPU / DEFAULT_PORT).
        if Server.DEFAULT_INSIGHTFACE_MODEL_PACK is not None:
            server_config["insightface_model_pack"] = (
                Server.DEFAULT_INSIGHTFACE_MODEL_PACK
            )

        return server_config

    def _add_cors_exception_handler(self):
        # No HTTPException handler here on purpose. One used to rebuild every
        # HTTPException as a fresh JSONResponse to re-add the CORS pair, and in
        # doing so dropped exc.headers - so no route's Retry-After ever reached
        # a client (#1097). It was never needed: FastAPI's default handler
        # already forwards exc.headers, and CORSMiddleware sits outside
        # ExceptionMiddleware, so it stamps Access-Control-Allow-Origin and
        # Vary: Origin on the result anyway. Measured identical on an allowed
        # origin, a disallowed one and no origin at all.
        #
        # The Exception handler below is *not* redundant in the same way: a
        # 500 is answered by ServerErrorMiddleware, which sits outside
        # CORSMiddleware and so never gets stamped. The validation handler
        # probably is redundant, but it takes no headers from its exception,
        # so it is left alone rather than widened into this fix.

        @self.api.exception_handler(Exception)
        async def generic_exception_handler(request, exc):
            logger.error(f"Unhandled exception: {exc}")
            origin = request.headers.get("origin")
            headers = {
                "Access-Control-Allow-Credentials": "true",
            }
            if origin and (
                origin in self.allow_origins
                or (
                    self.allow_origin_regex
                    and re.match(self.allow_origin_regex, origin)
                )
            ):
                headers["Access-Control-Allow-Origin"] = origin
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error"},
                headers=headers,
            )

        @self.api.exception_handler(RequestValidationError)
        async def validation_exception_handler(request, exc):
            origin = request.headers.get("origin")
            headers = {
                "Access-Control-Allow-Credentials": "true",
            }
            if origin and (
                origin in self.allow_origins
                or (
                    self.allow_origin_regex
                    and re.match(self.allow_origin_regex, origin)
                )
            ):
                headers["Access-Control-Allow-Origin"] = origin

            detail = exc.errors()
            for err in detail:
                if err.get("type") == "string_too_short" and "password" in (
                    err.get("loc") or []
                ):
                    return JSONResponse(
                        status_code=422,
                        content={
                            "detail": "Password must be at least 8 characters long."
                        },
                        headers=headers,
                    )

            return JSONResponse(
                status_code=422,
                content={"detail": detail},
                headers=headers,
            )

    def _get_version(self):
        # Prefer pyproject.toml when running from the repo so that the version
        # is always authoritative and never stale from an old editable install.
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        pyproject_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "pyproject.toml"
        )
        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
            ver = data.get("project", {}).get("version")
            if ver:
                return ver
        except OSError as exc:
            logger.debug("Could not read version from pyproject.toml: %s", exc)

        # Fall back to installed package metadata (pip install / wheel deployment).
        try:
            return package_version("pixlstash")
        except PackageNotFoundError:
            return "unknown"

    def _get_frontend_dist_dir(self):
        package_dir = os.path.abspath(os.path.dirname(__file__))
        packaged_dist_dir = os.path.join(package_dir, "frontend", "dist")
        if os.path.isdir(packaged_dist_dir):
            return packaged_dist_dir

        repo_root = os.path.abspath(os.path.join(package_dir, ".."))
        repo_dist_dir = os.path.join(repo_root, "frontend", "dist")
        if os.path.isdir(repo_dist_dir):
            return repo_dist_dir

        return None

    def _get_frontend_index_path(self):
        dist_dir = self._get_frontend_dist_dir()
        if not dist_dir:
            return None
        index_path = os.path.join(dist_dir, "index.html")
        if not os.path.isfile(index_path):
            return None
        return index_path

    def _index_html_response(self):
        """Return the SPA document with the install type substituted in.

        ``None`` when there is no built frontend, so callers fall back the same
        way they did when this served the file straight off disk.

        Cached on ``(path, install_type)`` rather than re-read per request: the
        SPA entry point is hit on every cold navigation, and the substitution is
        the same string every time until one of those two changes.
        """
        index_path = self._get_frontend_index_path()
        if not index_path:
            return None

        install_type = Server.detect_install_type()
        key = (index_path, install_type)
        if (
            getattr(self, "_index_html_cache", None) is None
            or self._index_html_cache[0] != key
        ):
            with open(index_path, encoding="utf-8") as handle:
                html = handle.read()
            self._index_html_cache = (
                key,
                html.replace(Server.INSTALL_TYPE_PLACEHOLDER, install_type),
            )
        return HTMLResponse(content=self._index_html_cache[1])

    def _setup_routes(self):
        ###############################
        # Rate limiting              ##
        ###############################
        # Pass the configured limit/window through, but fall back to ``None``
        # (NOT inline numbers) when a key is unset so the middleware uses its
        # module-level ``_LIMIT`` / ``_WINDOW`` defaults. Those constants are the
        # single source of truth for the defaults and the documented test hook -
        # patching ``rate_limiter._LIMIT`` / ``_WINDOW`` only takes effect when
        # the instance value is ``None``.
        rate_limit_cfg = self._server_config.get("rate_limit_max_requests")
        rate_window_cfg = self._server_config.get("rate_limit_window_seconds")
        self.api.add_middleware(
            RateLimitMiddleware,
            enabled=not bool(self._server_config.get("disable_rate_limit", False)),
            limit=int(rate_limit_cfg) if rate_limit_cfg is not None else None,
            window=int(rate_window_cfg) if rate_window_cfg is not None else None,
        )

        ###############################
        # Static file endpoints      ##
        ###############################
        dist_dir = self._get_frontend_dist_dir()
        if dist_dir:
            assets_dir = os.path.join(dist_dir, "assets")
            if os.path.isdir(assets_dir):
                self.api.mount(
                    "/assets",
                    StaticFiles(directory=assets_dir),
                    name="frontend-assets",
                )

        # Images embedded in the API reference description (logo + token
        # screenshots). Bundled with the package and served same-origin so
        # /scalar works offline, without depending on pixlstash.dev. The
        # static docs generator copies the same files next to each published
        # page, so the page-relative URLs resolve there too.
        scalar_assets_dir = os.path.join(
            os.path.dirname(__file__), "data", "scalar-assets"
        )
        if os.path.isdir(scalar_assets_dir):
            self.api.mount(
                "/scalar-assets",
                StaticFiles(directory=scalar_assets_dir),
                name="scalar-assets",
            )

        @self.api.get("/", include_in_schema=False)
        async def read_root():
            index_response = self._index_html_response()
            if index_response:
                return index_response
            version = self._get_version()
            return {"message": "PixlStash REST API", "version": version}

        @self.api.get("/version", tags=["server"], response_model=VersionResponse)
        async def read_version():
            version = self._get_version()
            install_type = Server.detect_install_type()
            # Defensive guard: detect_install_type() already constrains its
            # output, but the returned value feeds cross-cutting telemetry, so
            # never let anything outside the contract escape. Anything
            # unexpected collapses to the uncertain "other" bucket.
            if install_type not in Server.INSTALL_TYPES:
                logger.warning(
                    "detect_install_type() produced unexpected value %r; "
                    "reporting install_type='other'.",
                    install_type,
                )
                install_type = "other"
            docker_variant = os.environ.get("PIXLSTASH_DOCKER_VARIANT", "gpu")
            logger.info(
                "[/version] install_type=%r PIXLSTASH_DOCKER_VARIANT=%r -> "
                "docker_variant=%r",
                install_type,
                os.environ.get("PIXLSTASH_DOCKER_VARIANT"),
                docker_variant,
            )
            return {
                "message": "PixlStash REST API",
                "version": version,
                "install_type": install_type,
                "docker_variant": docker_variant,
            }

        @self.api.get("/scalar", include_in_schema=False)
        async def scalar_reference():
            # Scalar API reference UI, rendered client-side from the live
            # OpenAPI schema. Served alongside the built-in Swagger /docs.
            return HTMLResponse(content=render_scalar_html("/openapi.json"))

        @self.api.get("/favicon.ico", include_in_schema=False)
        def favicon():
            index_path = self._get_frontend_index_path()
            if index_path:
                favicon_path = os.path.join(os.path.dirname(index_path), "favicon.ico")
                if os.path.isfile(favicon_path):
                    return FileResponse(
                        favicon_path, media_type="image/vnd.microsoft.icon"
                    )
            favicon_path = os.path.join(
                os.path.dirname(__file__), "..", "frontend", "public", "favicon.ico"
            )
            return FileResponse(favicon_path, media_type="image/vnd.microsoft.icon")

        # The /ws/updates broadcaster route lives in WsBroadcasterMixin; register
        # it here so the WebSocket lifecycle stays owned by the broadcaster.
        self.register_ws_updates_route()

        # Every include_router carries the authz gate as a router-level
        # dependency (dependencies=[Depends(self.authz)]). This is the single
        # wiring point for the centralised, deny-by-default authorization model
        # (Phase 1; docs/backend_architecture.md §16.2). In Step 1 the gate is
        # report-only, so it denies nothing - it only observes undeclared routes.
        gate = [Depends(self.authz)]
        self.api.include_router(
            create_config_router(self),
            prefix=API_V1_PREFIX,
            include_in_schema=False,
            dependencies=gate,
        )
        self.api.include_router(
            create_telemetry_router(self),
            prefix=API_V1_PREFIX,
            tags=["telemetry"],
            dependencies=gate,
        )
        self.api.include_router(
            create_characters_router(self),
            prefix=API_V1_PREFIX,
            tags=["characters"],
            dependencies=gate,
        )
        self.api.include_router(
            create_characters_faces_router(self),
            prefix=API_V1_PREFIX,
            tags=["characters"],
            dependencies=gate,
        )
        self.api.include_router(
            create_picture_sets_router(self),
            prefix=API_V1_PREFIX,
            tags=["picture_sets"],
            dependencies=gate,
        )
        self.api.include_router(
            create_projects_router(self),
            prefix=API_V1_PREFIX,
            tags=["projects"],
            dependencies=gate,
        )
        self.api.include_router(
            create_tags_router(self),
            prefix=API_V1_PREFIX,
            tags=["tags"],
            dependencies=gate,
        )
        self.api.include_router(
            create_stacks_router(self),
            prefix=API_V1_PREFIX,
            tags=["stacks"],
            dependencies=gate,
        )
        self.api.include_router(
            create_dedup_router(self),
            prefix=API_V1_PREFIX,
            tags=["dedup"],
            dependencies=gate,
        )
        # tag_predictions must be registered before pictures so that the
        # specific path /pictures/{id}/tag_predictions is not swallowed by
        # the wildcard /pictures/{id}/{field} route in the pictures router.
        self.api.include_router(
            create_tag_predictions_router(self),
            prefix=API_V1_PREFIX,
            include_in_schema=False,
            dependencies=gate,
        )
        # guest_scores must be registered before pictures for the same reason:
        # /pictures/guest-scores must not be swallowed by /pictures/{id}/{field}.
        self.api.include_router(
            create_guest_scores_router(self),
            prefix=API_V1_PREFIX,
            include_in_schema=False,
            dependencies=gate,
        )
        self.api.include_router(
            create_tag_suggestions_router(self),
            prefix=API_V1_PREFIX,
            tags=["tag_suggestions"],
            dependencies=gate,
        )
        self.api.include_router(
            create_reviews_router(self),
            prefix=API_V1_PREFIX,
            tags=["reviews"],
            dependencies=gate,
        )
        self.api.include_router(
            create_operations_router(self),
            prefix=API_V1_PREFIX,
            tags=["operations"],
            dependencies=gate,
        )
        self.api.include_router(
            create_tag_health_router(self),
            prefix=API_V1_PREFIX,
            tags=["tag_health"],
            dependencies=gate,
        )
        self.api.include_router(
            create_insights_router(self),
            prefix=API_V1_PREFIX,
            tags=["insights"],
            dependencies=gate,
        )
        self.api.include_router(
            create_moves_router(self),
            prefix=API_V1_PREFIX,
            tags=["moves"],
            dependencies=gate,
        )
        self.api.include_router(
            create_tagger_runs_router(self),
            prefix=API_V1_PREFIX,
            tags=["tagger_runs"],
            dependencies=gate,
        )
        self.api.include_router(
            create_pictures_router(self),
            prefix=API_V1_PREFIX,
            tags=["pictures"],
            dependencies=gate,
        )
        self.api.include_router(
            create_comfyui_router(self),
            prefix=API_V1_PREFIX,
            include_in_schema=False,
            dependencies=gate,
        )
        self.api.include_router(
            create_reference_folders_router(self),
            prefix=API_V1_PREFIX,
            include_in_schema=False,
            dependencies=gate,
        )
        self.api.include_router(
            create_import_folders_router(self),
            prefix=API_V1_PREFIX,
            include_in_schema=False,
            dependencies=gate,
        )
        self.api.include_router(
            create_libraries_router(self),
            prefix=API_V1_PREFIX,
            dependencies=gate,
        )
        # No router-level ``tags=``: every route in this module declares
        # ``tags=["model_shelf"]`` itself, and FastAPI concatenates the two into
        # a duplicated tag that reaches OpenAPI and the generated route table.
        self.api.include_router(
            create_model_shelf_router(self),
            prefix=API_V1_PREFIX,
            dependencies=gate,
        )
        self.api.include_router(
            create_model_folders_router(self),
            prefix=API_V1_PREFIX,
            dependencies=gate,
        )
        self.api.include_router(
            create_model_moves_router(self),
            prefix=API_V1_PREFIX,
            dependencies=gate,
        )
        self.api.include_router(
            create_model_imports_router(self),
            prefix=API_V1_PREFIX,
            dependencies=gate,
        )
        self.api.include_router(
            create_model_files_router(self),
            prefix=API_V1_PREFIX,
            dependencies=gate,
        )
        self.api.include_router(
            create_model_stacks_router(self),
            prefix=API_V1_PREFIX,
            dependencies=gate,
        )
        self.api.include_router(
            create_model_icons_router(self),
            prefix=API_V1_PREFIX,
            dependencies=gate,
        )
        self.api.include_router(
            create_filesystem_router(self),
            prefix=API_V1_PREFIX,
            include_in_schema=False,
            dependencies=gate,
        )
        self.api.include_router(
            create_folder_structure_router(self),
            prefix=API_V1_PREFIX,
            include_in_schema=False,
            dependencies=gate,
        )
        self.api.include_router(
            create_library_layout_router(self),
            prefix=API_V1_PREFIX,
            include_in_schema=False,
            dependencies=gate,
        )
        self.api.include_router(
            create_taggers_router(self),
            prefix=API_V1_PREFIX,
            include_in_schema=False,
            dependencies=gate,
        )
        self.api.include_router(
            create_snapshots_router(self),
            prefix=API_V1_PREFIX,
            tags=["snapshots"],
            dependencies=gate,
        )
        # Public share endpoint - no API prefix; auth is embedded in the URL token.
        self.api.include_router(
            create_share_router(self),
            tags=["share"],
            dependencies=gate,
        )

        # E2E-only test hooks. Registered ONLY when ``enable_test_hooks`` is
        # true (default False) so the route is absent (404), not merely 403, in
        # production. The only caller that sets the flag is the Playwright e2e
        # backend launcher (frontend/e2e/serve_e2e_backend.py); production
        # configs never set it. The endpoint is additionally owner-only.
        if self._server_config.get("enable_test_hooks", False):
            logger.warning(
                "enable_test_hooks=True: registering e2e-only test-hooks router "
                "(/api/v1/test-hooks/*). This must NEVER be set in production."
            )
            self.api.include_router(
                create_test_hooks_router(self),
                prefix=API_V1_PREFIX,
                include_in_schema=False,
                dependencies=gate,
            )

        @self.api.middleware("http")
        async def auth_middleware(request: Request, call_next):
            return await self.auth.auth_middleware(
                request,
                call_next,
                self.allow_origins,
                self.allow_origin_regex,
            )

        @self.api.get(
            f"{API_V1_PREFIX}/check-session",
            tags=["auth"],
            response_model=SessionStatusResponse,
        )
        async def check_session(request: Request):
            return self.auth.check_session(request)

        @self.api.get(
            f"{API_V1_PREFIX}/network/info",
            include_in_schema=False,
            response_model=NetworkInfoResponse,
        )
        def network_info(request: Request):
            self.auth.require_user_id(request)
            try:
                lan_ip = socket.gethostbyname(socket.gethostname())
            except OSError:
                lan_ip = "127.0.0.1"
            import ipaddress

            try:
                addr = ipaddress.ip_address(lan_ip)
                is_private = addr.is_private or addr.is_loopback
            except ValueError:
                is_private = True
            return {"lan_ip": lan_ip, "is_private": is_private}

        @self.api.post(
            f"{API_V1_PREFIX}/login", tags=["auth"], response_model=MessageResponse
        )
        def login(login_request: LoginRequest, http_request: Request):
            response = self.auth.login(login_request, http_request)
            self._user = self.auth.user
            return response

        @self.api.get(
            f"{API_V1_PREFIX}/login",
            tags=["auth"],
            response_model=RegistrationStatusResponse,
        )
        def check_registration():
            return self.auth.check_registration()

        @self.api.post(
            f"{API_V1_PREFIX}/logout", tags=["auth"], response_model=MessageResponse
        )
        def logout(response: Response, request: Request):
            return self.auth.logout(response, request)

        @self.api.get(f"{API_V1_PREFIX}/protected", include_in_schema=False)
        async def protected():
            return {"message": "You are authenticated!"}

        @self.api.get("/{full_path:path}", include_in_schema=False)
        async def frontend_fallback(full_path: str):
            dist_dir = self._get_frontend_dist_dir()
            if not dist_dir:
                raise HTTPException(status_code=404, detail="Not Found")

            safe_path = os.path.normpath(full_path).lstrip(os.sep)
            candidate = os.path.abspath(os.path.join(dist_dir, safe_path))
            if candidate.startswith(dist_dir) and os.path.isfile(candidate):
                return FileResponse(candidate)

            index_response = self._index_html_response()
            if not index_response:
                raise HTTPException(status_code=404, detail="Not Found")
            return index_response
