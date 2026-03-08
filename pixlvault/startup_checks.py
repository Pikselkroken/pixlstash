from __future__ import annotations

import os
import shutil
import socket
import tempfile
from importlib import metadata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import onnxruntime as ort
except Exception:
    ort = None

try:
    import torch
except Exception:
    torch = None

from pixlvault.picture_tagger import PictureTagger


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
        self._check_watch_folders(outcome)
        self._check_optional_dependencies(outcome)
        self._check_device_and_vram(outcome)

        for note in outcome.notes:
            self._logger.info("[startup-check] %s", note)
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
        if default_device not in {"cpu", "cuda", "gpu", "auto"}:
            outcome.hard_failures.append(
                "default_device must be one of: cpu, cuda, gpu, auto."
            )

        samesite = str(self._server_config.get("cookie_samesite", "Lax"))
        if samesite not in {"Lax", "Strict", "None"}:
            outcome.hard_failures.append(
                "cookie_samesite must be one of: Lax, Strict, None."
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
            os.makedirs(image_root, exist_ok=True)
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

    def _check_watch_folders(self, outcome: StartupCheckOutcome) -> None:
        watch_folders = self._server_config.get("watch_folders") or []
        if not isinstance(watch_folders, list):
            outcome.hard_failures.append("watch_folders must be a list.")
            return

        for entry in watch_folders:
            folder = entry
            if isinstance(entry, dict):
                folder = entry.get("folder")
            if not folder:
                continue
            if not os.path.exists(folder):
                outcome.warnings.append(f"Watch folder does not exist: {folder}")

    def _check_optional_dependencies(self, outcome: StartupCheckOutcome) -> None:
        openssl_path = shutil.which("openssl")
        if self._server_config.get("require_ssl", False) and not openssl_path:
            outcome.hard_failures.append(
                "require_ssl is enabled but openssl is not available on PATH."
            )
        elif not openssl_path:
            outcome.warnings.append("Optional dependency missing: openssl")

        if not shutil.which("nvidia-smi"):
            outcome.warnings.append(
                "Optional GPU utility missing: nvidia-smi (GPU telemetry may be reduced)."
            )

    def _check_device_and_vram(self, outcome: StartupCheckOutcome) -> None:
        device_value = str(self._server_config.get("default_device", "cpu")).lower()
        if device_value == "gpu":
            device_value = "cuda"

        is_auto_mode = device_value == "auto"
        is_explicit_gpu = device_value == "cuda"

        if device_value == "cpu":
            PictureTagger.FORCE_CPU = True
            outcome.notes.append(
                "default_device is set to cpu in config; using CPU inference."
            )
            return

        if torch is None:
            self._handle_gpu_check_failure(
                outcome,
                is_auto_mode,
                is_explicit_gpu,
                "PyTorch is unavailable; forcing CPU inference.",
                "PyTorch is unavailable while default_device is set to cuda.",
            )
            return

        if not torch.cuda.is_available():
            self._handle_gpu_check_failure(
                outcome,
                is_auto_mode,
                is_explicit_gpu,
                "CUDA is unavailable; forcing CPU inference.",
                "CUDA is unavailable while default_device is set to cuda.",
            )
            return

        providers = []
        try:
            providers = ort.get_available_providers() if ort is not None else []
        except Exception:
            providers = []
        if "CUDAExecutionProvider" not in providers:
            provider_list = ", ".join(providers) if providers else "none"
            onnx_package = self._detect_onnxruntime_package()
            remediation = self._onnx_cuda_remediation_hint(onnx_package)
            self._handle_gpu_check_failure(
                outcome,
                is_auto_mode,
                is_explicit_gpu,
                (
                    "ONNX CUDAExecutionProvider unavailable "
                    f"(available providers: {provider_list}; package: {onnx_package}); "
                    f"forcing CPU inference. {remediation}"
                ),
                (
                    "ONNX CUDAExecutionProvider unavailable while default_device is set to cuda "
                    f"(available providers: {provider_list}; package: {onnx_package}). "
                    f"{remediation}"
                ),
            )
            return

        min_free_vram_mb = float(
            self._server_config.get(
                "min_free_vram_mb",
                self.MIN_FREE_VRAM_MB_DEFAULT,
            )
        )
        self._server_config["min_free_vram_mb"] = min_free_vram_mb

        try:
            free_bytes, total_bytes = torch.cuda.mem_get_info()
            free_mb = free_bytes / float(1024**2)
            total_mb = total_bytes / float(1024**2)
        except Exception as exc:
            self._handle_gpu_check_failure(
                outcome,
                is_auto_mode,
                is_explicit_gpu,
                f"Unable to read VRAM availability ({exc}); forcing CPU inference.",
                f"Unable to read VRAM availability while default_device is set to cuda: {exc}",
            )
            return

        if free_mb < min_free_vram_mb:
            self._handle_gpu_check_failure(
                outcome,
                is_auto_mode,
                is_explicit_gpu,
                (
                    f"Insufficient free VRAM ({free_mb:.0f} MB of {total_mb:.0f} MB; "
                    f"requires >= {min_free_vram_mb:.0f} MB); forcing CPU inference."
                ),
                (
                    f"Insufficient free VRAM while default_device is set to cuda "
                    f"({free_mb:.0f} MB of {total_mb:.0f} MB; requires >= {min_free_vram_mb:.0f} MB)."
                ),
            )
            return

        PictureTagger.FORCE_CPU = False
        if device_value == "auto":
            self._server_config["default_device"] = "cuda"
        outcome.notes.append(
            f"GPU check passed ({free_mb:.0f} MB free VRAM); using CUDA inference."
        )

    def _force_cpu_with_warning(
        self,
        outcome: StartupCheckOutcome,
        warning: str,
    ) -> None:
        PictureTagger.FORCE_CPU = True
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
            outcome.hard_failures.append(explicit_gpu_failure)
            return
        self._force_cpu_with_warning(outcome, fallback_warning)

    def _detect_onnxruntime_package(self) -> str:
        for package_name in ("onnxruntime-gpu", "onnxruntime"):
            try:
                distribution = metadata.distribution(package_name)
                return f"{package_name} {distribution.version}"
            except metadata.PackageNotFoundError:
                continue
            except Exception:
                continue
        return "unknown"

    def _onnx_cuda_remediation_hint(self, onnx_package: str) -> str:
        config_hint = (
            f"Server config path: {self._server_config_path}.\n"
            "Set `default_device` to `cpu` or `auto` there to avoid strict CUDA startup checks."
        )
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
                prefix="pixlvault-startup-check-",
            ) as handle:
                handle.write(b"ok")
                temp_path = handle.name
            os.remove(temp_path)
        except Exception as exc:
            outcome.hard_failures.append(f"{label} '{dir_path}' is not writable: {exc}")
