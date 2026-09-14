from __future__ import annotations

import os
import shutil
import socket
import tempfile
from importlib import metadata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixlstash.startup_permissions import mkdir_private
from pixlstash.utils.accelerator import (
    CUDA,
    MPS,
    VALID_DEVICE_SETTINGS,
    accelerator_total_memory_mb,
    available_accelerator,
    is_accelerated,
    device_display_name,
    normalise_device,
    resolve_device,
)

_UNSET: Any = object()
_torch_mod: Any = _UNSET
_ort_mod: Any = _UNSET


def _torch():
    """Return the ``torch`` module, or ``None`` when it cannot be imported.

    Imported on demand rather than at module scope. ``torch`` costs seconds to
    import and this module is reached from ``pixlstash.server``, so importing
    it eagerly would make server startup - and every test - pay for it before a
    single check runs. A ``None`` result is never swallowed: callers surface it
    as a hard failure or a forced-CPU note.
    """
    global _torch_mod
    if _torch_mod is _UNSET:
        try:
            import torch
        except Exception:
            _torch_mod = None
        else:
            _torch_mod = torch
    return _torch_mod


def _ort():
    """Return the ``onnxruntime`` module, or ``None`` when unavailable.

    Deferred for the same reason as :func:`_torch`; a ``None`` result is
    reported by the caller as a hard startup failure.
    """
    global _ort_mod
    if _ort_mod is _UNSET:
        try:
            import onnxruntime as ort
        except Exception:
            _ort_mod = None
        else:
            _ort_mod = ort
    return _ort_mod


class StartupCheckError(Exception):
    def __init__(self, failures: list[str]):
        self.failures = list(failures)
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        return "Startup checks failed:\n- " + "\n- ".join(self.failures)


@dataclass
class StartupCheckOutcome:
    hard_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    forced_cpu: bool = False


class StartupChecks:
    """Run startup preflight checks for server safety and readiness.

    Args:
        server_config (dict): Mutable server configuration dictionary.
        server_config_path (str): Path to server configuration file.
        logger: Logger instance used to report check results.
    """

    MIN_FREE_DISK_GB_DEFAULT = 1.0
    MIN_FREE_VRAM_MB_DEFAULT = 1024.0

    def __init__(self, server_config: dict, server_config_path: str, logger):
        self._server_config = server_config
        self._server_config_path = server_config_path
        self._logger = logger

    def run(self) -> dict[str, Any]:
        outcome = StartupCheckOutcome()

        self._check_config_sanity(outcome)
        self._check_image_root(outcome)
        self._check_database_path(outcome)
        self._check_free_disk_space(outcome)
        self._check_port_bindable(outcome)
        self._check_migration_assets(outcome)
        self._check_optional_dependencies(outcome)
        self._check_device_and_vram(outcome)

        for note in outcome.notes:
            self._logger.debug("[startup-check] %s", note)
        for warning in outcome.warnings:
            self._logger.warning("[startup-check] %s", warning)

        if outcome.hard_failures:
            self._logger.error(
                "[startup-check] Failed with %d hard failure(s).",
                len(outcome.hard_failures),
            )
            raise StartupCheckError(outcome.hard_failures)

        self._logger.info(
            "[startup-check] Passed (%d warning(s), forced_cpu=%s)",
            len(outcome.warnings),
            outcome.forced_cpu,
        )
        return {
            "warnings": list(outcome.warnings),
            "notes": list(outcome.notes),
            "forced_cpu": outcome.forced_cpu,
        }

    def _check_config_sanity(self, outcome: StartupCheckOutcome) -> None:
        ort = _ort()

        host = self._server_config.get("host")
        if not isinstance(host, str) or not host.strip():
            outcome.hard_failures.append("Invalid server host in config.")

        port = self._server_config.get("port")
        try:
            port_int = int(port)
            if port_int < 0 or port_int > 65535:
                raise ValueError()
            self._server_config["port"] = port_int
        except Exception:
            outcome.hard_failures.append("Port must be an integer between 1 and 65535.")

        default_device = str(self._server_config.get("default_device", "cpu")).lower()
        if default_device not in VALID_DEVICE_SETTINGS:
            # One frozenset, shared with the PIXLSTASH_DEFAULT_DEVICE override in
            # server.py. They used to be two hand-maintained copies of the same
            # list and drifted: a value one accepted, the other rejected.
            outcome.hard_failures.append(
                "default_device must be one of: "
                f"{', '.join(sorted(VALID_DEVICE_SETTINGS))}."
            )

        samesite = str(self._server_config.get("cookie_samesite", "Lax"))
        if samesite not in {"Lax", "Strict", "None"}:
            outcome.hard_failures.append(
                "cookie_samesite must be one of: Lax, Strict, None."
            )

        host = str(self._server_config.get("host", "localhost"))
        require_local = self._server_config.get("require_local_for_write", True)
        if host == "0.0.0.0" and not require_local:
            outcome.warnings.append(
                "require_local_for_write is disabled while host is 0.0.0.0. "
                "Full login (username/password and ALL-scope tokens) is accessible from any IP address. "
                "Set require_local_for_write=true to restrict full access to local network connections."
            )

        # §16.3 host-capability / reverse-proxy hardening warnings.
        trusted_proxies = self._server_config.get("trusted_proxies") or []
        if host == "0.0.0.0" and not trusted_proxies:
            outcome.warnings.append(
                "host is 0.0.0.0 with trusted_proxies empty. If PixlStash is "
                "behind a reverse proxy this makes the locality gate a silent "
                "FALSE-ALLOW: every client appears to arrive from the proxy's "
                "(private) IP, so host-capability and require_local_for_write "
                "checks treat all remote callers as local. Add the proxy's IP to "
                "trusted_proxies AND configure the proxy to strip inbound "
                "X-Forwarded-For, so the owner's real client IP is used."
            )

        if self._server_config.get("allow_remote_host_ops", False):
            outcome.warnings.append(
                "allow_remote_host_ops is enabled: a remote authenticated OWNER "
                "can drive host-filesystem capability endpoints (browse, "
                "import/reference-folder writes, sidecar import/export). This "
                "grants the server's host-filesystem authority to any caller who "
                "can authenticate as the owner from off-box. The loopback-only "
                "red-line routes (server restart, open-in-file-manager) are NOT "
                "affected. Disable it unless remote host operations are required."
            )

        if ort is None:
            outcome.hard_failures.append(
                "onnxruntime is required but could not be imported."
            )

    def _check_image_root(self, outcome: StartupCheckOutcome) -> None:
        image_root = str(self._server_config.get("image_root") or "").strip()
        if not image_root:
            outcome.hard_failures.append("image_root is missing from config.")
            return

        try:
            # Missing library paths must satisfy the SQLite namespace guard
            # even under umask 0002. Existing paths remain an explicit user
            # repair decision rather than being silently chmodded here.
            mkdir_private(Path(image_root))
        except Exception as exc:
            outcome.hard_failures.append(
                f"Unable to create image_root directory '{image_root}': {exc}"
            )
            return

        self._assert_dir_writable(image_root, "image_root", outcome)

    def _check_database_path(self, outcome: StartupCheckOutcome) -> None:
        image_root = str(self._server_config.get("image_root") or "").strip()
        if not image_root:
            return

        db_path = os.path.join(image_root, "vault.db")
        parent = os.path.dirname(db_path)
        self._assert_dir_writable(parent, "database directory", outcome)

        try:
            if os.path.exists(db_path):
                with open(db_path, "ab"):
                    pass
            else:
                with open(db_path, "ab"):
                    pass
                os.remove(db_path)
        except Exception as exc:
            outcome.hard_failures.append(
                f"Database file '{db_path}' is not writable: {exc}"
            )

    def _check_free_disk_space(self, outcome: StartupCheckOutcome) -> None:
        image_root = str(self._server_config.get("image_root") or "").strip()
        if not image_root:
            return

        min_free_gb = float(
            self._server_config.get("min_free_disk_gb", self.MIN_FREE_DISK_GB_DEFAULT)
        )
        self._server_config["min_free_disk_gb"] = min_free_gb

        try:
            usage = shutil.disk_usage(image_root)
            free_gb = usage.free / float(1024**3)
        except Exception as exc:
            outcome.hard_failures.append(
                f"Unable to determine free disk space for '{image_root}': {exc}"
            )
            return

        if free_gb < min_free_gb:
            outcome.hard_failures.append(
                f"Insufficient disk space at '{image_root}': {free_gb:.2f} GB free, requires >= {min_free_gb:.2f} GB."
            )
        else:
            outcome.notes.append(
                f"Disk space OK at image_root: {free_gb:.2f} GB free (threshold {min_free_gb:.2f} GB)."
            )

    def _check_port_bindable(self, outcome: StartupCheckOutcome) -> None:
        host = str(self._server_config.get("host", "localhost"))
        port = int(self._server_config.get("port", 8000))

        # Under the Electron desktop shell the configured host:port describes the
        # optional *external* listener, not the connection the window uses (that
        # is an always-free ephemeral loopback port - see Server.run). So only
        # check the configured port when remote access is actually enabled, and
        # check it on the interface it will really bind (0.0.0.0).
        if os.environ.get("PIXLSTASH_INSTALL_TYPE", "").strip().lower() == "electron":
            if not self._server_config.get("external_server_enabled", False):
                return
            host = "0.0.0.0"

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        except Exception as exc:
            outcome.hard_failures.append(f"Server cannot bind to {host}:{port}: {exc}")
        finally:
            sock.close()

    def _check_migration_assets(self, outcome: StartupCheckOutcome) -> None:
        module_dir = Path(__file__).resolve().parent
        repo_root = module_dir.parent

        candidate_locations = [
            (repo_root / "alembic.ini", repo_root / "migrations"),
            (module_dir / "alembic.ini", module_dir / "migrations"),
        ]

        for candidate_ini, candidate_migrations in candidate_locations:
            if candidate_ini.exists() and candidate_migrations.exists():
                outcome.notes.append(
                    f"Alembic assets found at {candidate_ini} and {candidate_migrations}."
                )
                return

        expected = " or ".join(
            f"({candidate_ini}, {candidate_migrations})"
            for candidate_ini, candidate_migrations in candidate_locations
        )
        outcome.hard_failures.append(f"Alembic assets not found. Expected {expected}.")

    def _check_optional_dependencies(self, outcome: StartupCheckOutcome) -> None:
        if self._server_config.get("require_ssl", False):
            keyfile = self._server_config.get("ssl_keyfile", "")
            certfile = self._server_config.get("ssl_certfile", "")
            certs_exist = os.path.exists(keyfile) and os.path.exists(certfile)
            if not certs_exist:
                outcome.warnings.append(
                    "require_ssl is enabled but no existing certificate files were found. "
                    "PixlStash will generate a self-signed certificate automatically."
                )

        # Only worth saying where nvidia-smi could have been there. It is an
        # NVIDIA tool, so on a Mac its absence is not a missing dependency, it
        # is the machine - and warning about it on every boot taught Apple
        # Silicon owners that PixlStash's start-up warnings are noise to scroll
        # past, which is the last thing this check wants to teach.
        if available_accelerator() != MPS and not shutil.which("nvidia-smi"):
            outcome.warnings.append(
                "Optional GPU utility missing: nvidia-smi (GPU telemetry may be reduced)."
            )

    def _has_onnxruntime_conflict(self) -> bool:
        """Return True if both onnxruntime and onnxruntime-gpu are installed.

        Having both packages installed causes ONNX to silently use CPU inference
        even when a CUDA-capable GPU is present.
        """
        cpu_installed = False
        gpu_installed = False
        for package_name in ("onnxruntime", "onnxruntime-gpu"):
            try:
                metadata.distribution(package_name)
                if package_name == "onnxruntime":
                    cpu_installed = True
                else:
                    gpu_installed = True
            except metadata.PackageNotFoundError:
                continue
            except Exception as exc:
                # An unexpected metadata-read error must not be silently
                # swallowed: log it with the package name so a broken install
                # is diagnosable, then treat the package as "not detected".
                self._logger.warning(
                    "[startup-check] Could not read distribution metadata for "
                    "%s while checking for an onnxruntime conflict: %s",
                    package_name,
                    exc,
                )
                continue
        return cpu_installed and gpu_installed

    def _check_device_and_vram(self, outcome: StartupCheckOutcome) -> None:
        torch = _torch()
        ort = _ort()

        # This function is the real device gate, not InferenceEngine.create():
        # forcing CPU here writes "cpu" into the config, which Server.build_vault
        # turns into force_cpu=True, which pins the device before any engine-side
        # probe runs. So a CUDA-only check here made every later accelerator
        # change dead code - which is exactly what it did on Apple Silicon.
        device_value = normalise_device(
            self._server_config.get("default_device", "cpu")
        )

        is_auto_mode = device_value == "auto"
        # "gpu" is an explicit request for *an* accelerator, not for CUDA. It is
        # host-resolved like "auto" but refuses like a named device: an owner who
        # asked for the GPU should be told there is none, not quietly booted on
        # the CPU.
        is_explicit_gpu = device_value in {CUDA, MPS, "gpu"}
        # The torch handle is passed down rather than re-found: _torch() is the
        # cache the simulated-host fixtures seed, so probing through it is what
        # lets a CPU-only runner verify the CUDA and ROCm paths.
        requested = resolve_device(device_value, torch_module=torch)

        # PyTorch's ROCm build drives the AMD GPU through the CUDA API (torch.cuda.*
        # works, HIP masquerades as CUDA), so the checks below run unchanged; only
        # torch.version.hip distinguishes it. ROCm is experimental and unverified,
        # and we ship the CPU ONNX Runtime alongside it, so the ONNX models run on
        # CPU by design while torch uses the GPU.
        is_rocm = (
            torch is not None
            and getattr(getattr(torch, "version", None), "hip", None) is not None
        )
        # Name what was *asked for*, not what resolution settled on. When a
        # host-resolved setting finds nothing, `requested` is "cpu", and saying
        # "CPU is unavailable; forcing CPU inference" is nonsense.
        if is_rocm:
            accel_name = "ROCm"
        elif is_accelerated(requested):
            accel_name = device_display_name(requested)
        else:
            accel_name = "GPU"
        accel_note = " (experimental, unverified)" if is_rocm else ""

        if device_value == "cpu":
            outcome.notes.append(
                "default_device is set to cpu in config; using CPU inference."
            )
            outcome.forced_cpu = True
            return

        if torch is None:
            self._handle_gpu_check_failure(
                outcome,
                is_auto_mode,
                is_explicit_gpu,
                "PyTorch is unavailable; forcing CPU inference.",
                "PyTorch is unavailable while default_device is set to "
                f"{device_value}.",
            )
            return

        # available_accelerator() wraps its own probes and answers "none" for a
        # broken install rather than raising, which is what the bare
        # torch.cuda.is_available() call here needed a try/except for.
        detected = available_accelerator(torch)
        gpu_available = detected is not None and requested == detected
        if not gpu_available:
            if detected is not None:
                # Asked for one accelerator, found another. Naming both is the
                # difference between a fixable message and a mystery.
                reason = (
                    f"{accel_name} is unavailable (this host has "
                    f"{device_display_name(detected)})"
                )
            else:
                reason = f"{accel_name} is unavailable"
            self._handle_gpu_check_failure(
                outcome,
                is_auto_mode,
                is_explicit_gpu,
                f"{reason}; forcing CPU inference.",
                f"{reason} while default_device is set to {device_value}.",
            )
            return
        self._server_config["resolved_device"] = requested

        providers = []
        try:
            providers = ort.get_available_providers() if ort is not None else []
        except Exception as exc:
            # A failure here means a broken ONNX Runtime install. Do not swallow
            # it silently: without this log a broken ORT masquerades as "no CUDA
            # provider" on a CUDA box, sending inference to CPU with no clue why.
            providers = []
            self._logger.warning(
                "[startup-check] ort.get_available_providers() failed (%s); "
                "treating ONNX Runtime as having no available providers. ONNX "
                "models will run on CPU.",
                exc,
            )
        if is_rocm:
            # The ROCm/MIGraphX ONNX Runtime build isn't on PyPI, so we bundle the
            # CPU ORT on ROCm; the InsightFace face-extraction and optional WD14
            # ONNX models run on CPU by design while PyTorch (HIP) uses the GPU for
            # embeddings, captioning and the other torch workloads. This is expected
            # on ROCm, so it's a note rather than a warning.
            outcome.notes.append(
                "AMD GPU (ROCm) is experimental and unverified. PyTorch will use the "
                "GPU; the ONNX face-extraction and WD14 tagger models run on CPU."
            )
        elif requested == MPS:
            # CoreML is how ONNX Runtime reaches the same GPU torch reaches
            # through MPS. Its absence is the exact analogue of a missing
            # CUDAExecutionProvider: torch still uses the GPU, the ONNX models
            # (WD14, InsightFace) do not.
            if "CoreMLExecutionProvider" not in providers:
                provider_list = ", ".join(providers) if providers else "none"
                outcome.warnings.append(
                    "ONNX CoreMLExecutionProvider unavailable "
                    f"(available providers: {provider_list}; package: "
                    f"{self._detect_onnxruntime_package()}). PyTorch will still "
                    "use Metal for all non-ONNX inference; the WD14 tagger ONNX "
                    "model will run on CPU. Fix with: pip install --upgrade "
                    "onnxruntime."
                )
            else:
                outcome.notes.append(
                    "ONNX CoreMLExecutionProvider is available; the WD14 tagger "
                    "will run on the GPU."
                )
        elif "CUDAExecutionProvider" not in providers:
            provider_list = ", ".join(providers) if providers else "none"
            onnx_package = self._detect_onnxruntime_package()
            gpu_arch_note = self._detect_gpu_arch_note()
            remediation = self._onnx_cuda_remediation_hint(onnx_package, gpu_arch_note)
            conflict_note = (
                " Both 'onnxruntime' and 'onnxruntime-gpu' are installed - "
                "this conflict likely caused the fallback to CPU. "
                "Fix with: pip uninstall -y onnxruntime && pip install onnxruntime-gpu."
                if self._has_onnxruntime_conflict()
                else ""
            )
            # If PyTorch CUDA is available the GPU works - only the ONNX WD14 tagger
            # will fall back to CPU.  Treat this as a warning, not a hard failure.
            if is_explicit_gpu:
                outcome.warnings.append(
                    "ONNX CUDAExecutionProvider unavailable while default_device is set to cuda "
                    f"(available providers: {provider_list}; package: {onnx_package}){gpu_arch_note}. "
                    "PyTorch CUDA will still be used for all non-ONNX inference; "
                    f"the WD14 tagger ONNX model will run on CPU.{conflict_note} "
                    f"{remediation}"
                )
            else:
                outcome.warnings.append(
                    "ONNX CUDAExecutionProvider unavailable "
                    f"(available providers: {provider_list}; package: {onnx_package}){gpu_arch_note}. "
                    "PyTorch CUDA will still be used for all non-ONNX inference; "
                    f"the WD14 tagger ONNX model will run on CPU.{conflict_note} {remediation}"
                )
            # Don't return - continue with the VRAM check so other GPU paths work.

        min_free_vram_mb = float(
            self._server_config.get(
                "min_free_vram_mb",
                self.MIN_FREE_VRAM_MB_DEFAULT,
            )
        )
        self._server_config["min_free_vram_mb"] = min_free_vram_mb

        try:
            if requested == MPS:
                # Unified memory has no "free VRAM" to read: the GPU shares one
                # pool with the OS and every other process, and any instant
                # reading of it is stale before it is used. Metal's recommended
                # working set is the number the driver actually enforces
                # against, so that is what is checked and what the note reports
                # - deliberately described as a working set, not as free memory.
                total_mb = float(accelerator_total_memory_mb(MPS, torch_module=torch))
                free_mb = total_mb - (
                    torch.mps.driver_allocated_memory() / float(1024**2)
                )
                if total_mb <= 0:
                    raise RuntimeError("Metal reported no recommended working set size")
            else:
                free_bytes, total_bytes = torch.cuda.mem_get_info()
                free_mb = free_bytes / float(1024**2)
                total_mb = total_bytes / float(1024**2)
        except Exception as exc:
            self._handle_gpu_check_failure(
                outcome,
                is_auto_mode,
                is_explicit_gpu,
                f"Unable to read {accel_name} memory ({exc}); forcing CPU inference.",
                f"Unable to read {accel_name} memory while default_device is set "
                f"to {device_value}: {exc}",
            )
            return

        if free_mb < min_free_vram_mb:
            self._handle_gpu_check_failure(
                outcome,
                is_auto_mode,
                is_explicit_gpu,
                (
                    f"Insufficient free {accel_name} memory ({free_mb:.0f} MB of "
                    f"{total_mb:.0f} MB; requires >= {min_free_vram_mb:.0f} MB); "
                    "forcing CPU inference."
                ),
                (
                    f"Insufficient free {accel_name} memory while default_device "
                    f"is set to {device_value} ({free_mb:.0f} MB of "
                    f"{total_mb:.0f} MB; requires >= {min_free_vram_mb:.0f} MB)."
                ),
            )
            return

        # "working set" rather than "free VRAM" on unified memory: the figure is
        # what Metal recommends this process claim from a pool it shares with
        # everything else, not memory that is free and nobody else's.
        memory_label = "MB working set" if requested == MPS else "MB free VRAM"
        outcome.notes.append(
            f"GPU check passed ({free_mb:.0f} {memory_label}); "
            f"using {accel_name}{accel_note} inference."
        )

    def _force_cpu_with_warning(
        self,
        outcome: StartupCheckOutcome,
        warning: str,
        is_auto_mode: bool = False,
    ) -> None:
        # Only persist "cpu" when it was an explicit user choice.  When in auto
        # mode we keep "auto" in the config so that the next startup re-evaluates
        # CUDA availability (e.g. after upgrading the CUDA runtime or drivers).
        if not is_auto_mode:
            self._server_config["default_device"] = "cpu"
        outcome.forced_cpu = True
        outcome.warnings.append(warning)

    def _handle_gpu_check_failure(
        self,
        outcome: StartupCheckOutcome,
        is_auto_mode: bool,
        is_explicit_gpu: bool,
        fallback_warning: str,
        explicit_gpu_failure: str,
    ) -> None:
        if is_explicit_gpu and not is_auto_mode:
            # Refusing rather than silently downgrading is deliberate: the owner
            # named a device, so booting on a different one is a lie. But a
            # refusal that does not say how to boot strands them, so the failure
            # carries the config path and the two values that start the server.
            outcome.hard_failures.append(
                f"{explicit_gpu_failure}\n{self._device_config_hint()}"
            )
            return
        self._force_cpu_with_warning(
            outcome, fallback_warning, is_auto_mode=is_auto_mode
        )

    def _detect_gpu_arch_note(self) -> str:
        """Return a human-readable note if the GPU arch may be unsupported by ORT."""
        torch = _torch()

        if torch is None or not torch.cuda.is_available():
            return ""
        try:
            major, minor = torch.cuda.get_device_capability(0)
            sm = major * 10 + minor
            # ORT 1.x releases lag behind new GPU architectures.
            # sm >= 120 = Blackwell (RTX 5xxx) - not included in ORT until a later release.
            if sm >= 120:
                name = torch.cuda.get_device_name(0)
                return (
                    f" - GPU {name} (sm_{major}{minor}, Blackwell) may not be supported "
                    "by the installed onnxruntime-gpu; upgrade to a newer ORT release or "
                    "build from source"
                )
        except Exception:
            # Best-effort diagnostic only: if we cannot query GPU capability,
            # fall back to no additional note rather than failing startup.
            return ""
        return ""

    def _detect_onnxruntime_package(self) -> str:
        for package_name in ("onnxruntime-gpu", "onnxruntime"):
            try:
                distribution = metadata.distribution(package_name)
                return f"{package_name} {distribution.version}"
            except metadata.PackageNotFoundError:
                continue
            except Exception:
                # Best-effort package probe for a diagnostic hint; an unreadable
                # distribution just means "try the next name / unknown".
                continue
        return "unknown"

    def _device_config_hint(self) -> str:
        """How to change the device, named exactly enough to act on."""
        return (
            f"Server config path: {self._server_config_path}.\n"
            "Set `default_device` to `cpu` or `auto` there to avoid strict CUDA startup checks."
        )

    def _onnx_cuda_remediation_hint(
        self, onnx_package: str, gpu_arch_note: str = ""
    ) -> str:
        config_hint = self._device_config_hint()
        if onnx_package.startswith("onnxruntime "):
            return (
                "Detected CPU-only ONNX Runtime.\nInstall GPU support with "
                "`pip uninstall -y onnxruntime && pip install onnxruntime-gpu`.\n"
                f"{config_hint}"
            )
        if onnx_package == "unknown":
            return (
                "Verify ONNX Runtime installation and ensure CUDA provider support is installed.\n"
                f"(for pip: `pip install onnxruntime-gpu`). {config_hint}"
            )
        return (
            "Ensure ONNX Runtime CUDA dependencies are installed and accessible on this machine.\n"
            f"{config_hint}"
        )

    def _assert_dir_writable(
        self,
        dir_path: str,
        label: str,
        outcome: StartupCheckOutcome,
    ) -> None:
        try:
            os.makedirs(dir_path, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=dir_path,
                delete=False,
                prefix="pixlstash-startup-check-",
            ) as handle:
                handle.write(b"ok")
                temp_path = handle.name
            os.remove(temp_path)
        except Exception as exc:
            outcome.hard_failures.append(f"{label} '{dir_path}' is not writable: {exc}")
