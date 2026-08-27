"""Boot a freshly installed PixlStash and assert it actually serves.

This is the automated half of ``docs/release-test-plan.md`` §1.1/§1.4: the part
that only checks that an install *works*, as opposed to the parts that need a
human looking at a screen. It is deliberately a Python script rather than shell
steps in the workflow, because the same checks have to run identically on
Ubuntu, macOS, and Windows, and the Windows differences (venv ``Scripts``
instead of ``bin``, no POSIX job control, ``CTRL_BREAK`` instead of ``SIGTERM``)
are exactly where a bash translation would rot.

What it proves:

* the wheel installs and the ``pixlstash-server`` console script exists,
* the packaged frontend really shipped inside the wheel (``package-data`` drift
  is invisible until someone opens the page and gets a 404),
* Alembic brings a brand-new vault to head and uvicorn binds,
* the process shuts down on request without being killed.

What it deliberately does NOT prove: anything needing ML models. The server is
booted with ``disable_background_workers``, the same lever
``frontend/e2e/serve_e2e_backend.py`` uses, so no model is downloaded. Worker
startup and real inference stay in the manual plan and in the GPU jobs - a
smoke test that pulled multi-GB weights on three operating systems would be
slow enough that it stopped being run, which is the failure mode this whole
exercise is trying to remove.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger("smoke_install")

# The server binds before it is ready to answer, so every check polls rather
# than sleeping a fixed amount. A warm local venv answers in ~6 s; the ceiling
# is headroom for a cold runner (first-import of torch dominates), not an
# expectation of how long boot should take.
READY_TIMEOUT_S = 240
SHUTDOWN_TIMEOUT_S = 60


class SmokeFailure(RuntimeError):
    """Raised when a smoke assertion fails, with the diagnostic already logged."""


def venv_python(venv_dir: Path) -> Path:
    """Return the interpreter path inside ``venv_dir`` for the current OS.

    Args:
        venv_dir (Path): Root of a virtual environment created by ``venv``.

    Returns:
        Path: Path to the ``python`` executable inside that environment.
    """
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def venv_script(venv_dir: Path, name: str) -> Path:
    """Return the path to a console script installed inside ``venv_dir``.

    Args:
        venv_dir (Path): Root of a virtual environment.
        name (str): Console-script name, without any extension.

    Returns:
        Path: Path to the script for the current OS.
    """
    if os.name == "nt":
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


def write_config(config_path: Path, image_root: Path, port: int) -> None:
    """Write a minimal server config for the smoke run.

    Mirrors the shape ``docker-entrypoint.sh`` writes on first run, plus the
    ``disable_background_workers`` lever so no ML model is ever fetched.

    Args:
        config_path (Path): Destination for the JSON config.
        image_root (Path): Directory the server may use as its image root.
        port (int): Loopback port to bind.
    """
    config = {
        "host": "127.0.0.1",
        "port": port,
        "log_level": "info",
        "log_file": None,
        "require_ssl": False,
        "cookie_samesite": "Lax",
        "cookie_secure": False,
        "image_root": str(image_root),
        "default_device": "cpu",
        "disable_background_workers": True,
        "cors_origins": [],
        "watch_folders": [],
    }
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    logger.info("Wrote smoke config to %s (port=%d)", config_path, port)


def get_json(url: str, timeout: float = 5.0) -> dict:
    """GET ``url`` and decode the response as JSON.

    Args:
        url (str): Absolute URL to fetch.
        timeout (float): Per-request timeout in seconds.

    Returns:
        dict: Decoded JSON body.
    """
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def get_text(url: str, timeout: float = 5.0) -> tuple[int, str]:
    """GET ``url`` and return its status code and body text.

    Args:
        url (str): Absolute URL to fetch.
        timeout (float): Per-request timeout in seconds.

    Returns:
        tuple[int, str]: HTTP status code and decoded body.
    """
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return response.status, response.read().decode("utf-8", errors="replace")


def dump_log(log_path: Path, reason: str) -> None:
    """Log the server's captured output so a CI failure is self-explanatory.

    Args:
        log_path (Path): File the server's stdout/stderr was captured to.
        reason (str): Why the log is being dumped.
    """
    logger.error("%s - captured server output follows:", reason)
    try:
        logger.error("%s", log_path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        logger.error("Could not read the server log at %s: %s", log_path, exc)


def wait_for_ready(base_url: str, process: subprocess.Popen, log_path: Path) -> dict:
    """Poll ``/version`` until the server answers, or fail with diagnostics.

    Args:
        base_url (str): Server root, e.g. ``http://127.0.0.1:19538``.
        process (subprocess.Popen): The running server process.
        log_path (Path): File its output is being captured to.

    Returns:
        dict: The decoded ``/version`` payload.

    Raises:
        SmokeFailure: If the process exits early or never becomes ready.
    """
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        # A process that has already exited will never become ready, so stop
        # waiting out the full timeout and report the real cause instead.
        if process.poll() is not None:
            dump_log(log_path, f"Server exited early with code {process.returncode}")
            raise SmokeFailure(
                f"Server process exited with code {process.returncode} before "
                "serving /version."
            )
        try:
            payload = get_json(f"{base_url}/version")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(1.0)
            continue
        except json.JSONDecodeError as exc:
            dump_log(log_path, f"/version returned undecodable JSON: {exc}")
            raise SmokeFailure("/version did not return JSON.") from exc
        logger.info("Server ready; /version reported %s", payload)
        return payload

    dump_log(log_path, f"/version did not respond within {READY_TIMEOUT_S}s")
    raise SmokeFailure(f"Server was not ready within {READY_TIMEOUT_S}s.")


def assert_version_payload(payload: dict, expected_version: str | None) -> None:
    """Assert ``/version`` reports a sane install.

    Args:
        payload (dict): Decoded ``/version`` body.
        expected_version (str | None): Version the wheel should report, if known.

    Raises:
        SmokeFailure: If a field is missing or contradicts the built wheel.
    """
    reported = payload.get("version")
    if not reported:
        raise SmokeFailure(f"/version reported no version field: {payload!r}")

    # Catches the packaging failure where the wheel ships a stale or absent
    # version and every downstream upgrade check silently compares nothing.
    if expected_version and reported != expected_version:
        raise SmokeFailure(
            f"/version reported {reported!r} but the built wheel is "
            f"{expected_version!r}."
        )

    install_type = payload.get("install_type")
    # A pip-installed wheel must not be mistaken for Docker or Electron: this
    # value feeds telemetry, and 'other' means detection fell through.
    if install_type != "pip":
        raise SmokeFailure(
            f"/version reported install_type={install_type!r}, expected 'pip'."
        )
    logger.info("Version payload OK (version=%s install_type=pip)", reported)


def assert_spa_served(base_url: str, log_path: Path) -> None:
    """Assert the packaged single-page app is served from the wheel.

    Args:
        base_url (str): Server root.
        log_path (Path): File the server's output is being captured to.

    Raises:
        SmokeFailure: If the SPA shell is missing or not served.
    """
    try:
        status, body = get_text(f"{base_url}/")
    except (urllib.error.URLError, OSError) as exc:
        dump_log(log_path, f"GET / failed: {exc}")
        raise SmokeFailure("GET / failed.") from exc

    if status != 200:
        dump_log(log_path, f"GET / returned HTTP {status}")
        raise SmokeFailure(f"GET / returned HTTP {status}, expected 200.")

    # The built shell is tiny and its only stable marker is the mount point
    # Vite leaves in place. Its absence means pixlstash/frontend/dist did not
    # make it into the wheel - the package-data drift this check exists for.
    if '<div id="app"' not in body:
        dump_log(log_path, "GET / did not return the built SPA shell")
        raise SmokeFailure(
            "GET / did not contain the SPA mount point; the frontend is "
            "probably missing from the wheel."
        )
    logger.info("SPA shell served from the installed wheel.")


def stop_server(process: subprocess.Popen, log_path: Path) -> None:
    """Ask the server to stop and assert it does so without being killed.

    Args:
        process (subprocess.Popen): The running server process.
        log_path (Path): File its output is being captured to.

    Raises:
        SmokeFailure: If it has to be force-killed.
    """
    # Windows has no SIGTERM for a console app; CTRL_BREAK is the portable way
    # to ask a process group started with CREATE_NEW_PROCESS_GROUP to stop.
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.terminate()

    try:
        process.wait(timeout=SHUTDOWN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)
        dump_log(log_path, "Server ignored the shutdown request and was killed")
        raise SmokeFailure(
            f"Server did not exit within {SHUTDOWN_TIMEOUT_S}s of being asked "
            "to stop; it had to be killed. That is the orphaned-process failure "
            "the release plan checks for by hand."
        ) from None

    logger.info("Server stopped with exit code %s.", process.returncode)


def run_smoke(venv_dir: Path, port: int, expected_version: str | None) -> None:
    """Run the full install smoke against a venv that already has the wheel.

    Args:
        venv_dir (Path): Virtual environment with ``pixlstash`` installed.
        port (int): Loopback port to bind.
        expected_version (str | None): Version the wheel should report.

    Raises:
        SmokeFailure: On the first failed assertion.
    """
    server_bin = venv_script(venv_dir, "pixlstash-server")
    if not server_bin.exists():
        raise SmokeFailure(
            f"Console script {server_bin} was not installed by the wheel."
        )
    logger.info("Found console script at %s", server_bin)

    base_url = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory(prefix="pixlstash-smoke-") as tmp:
        # Deliberately NOT resolved. On macOS this path runs through
        # /var -> /private/var, so the unresolved spelling is exactly the
        # symlinked-ancestor case that used to stop the server starting; the
        # smoke test is where that stays proven on a real macOS runner.
        tmp_path = Path(tmp)
        image_root = tmp_path / "images"
        image_root.mkdir()
        config_path = tmp_path / "server-config.json"
        write_config(config_path, image_root, port)

        log_path = tmp_path / "server.log"
        # A dedicated config and HOME keep the smoke off any real vault on the
        # machine, so a local run can never touch the developer's library.
        env = dict(os.environ)
        env["HOME"] = str(tmp_path)
        env["USERPROFILE"] = str(tmp_path)
        env.pop("PIXLSTASH_INSTALL_TYPE", None)

        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
        )

        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                [str(server_bin), "--server-config", str(config_path)],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=creation_flags,
            )
            try:
                payload = wait_for_ready(base_url, process, log_path)
                assert_version_payload(payload, expected_version)
                assert_spa_served(base_url, log_path)
                stop_server(process, log_path)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=30)

        # Read back afterwards so a passing run still shows the boot log, which
        # is what makes a later regression diffable against a green run.
        logger.info(
            "Boot log:\n%s", log_path.read_text(encoding="utf-8", errors="replace")
        )


def main() -> int:
    """Parse arguments and run the smoke.

    Returns:
        int: Process exit code, 0 on success.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--venv",
        required=True,
        type=Path,
        help="Virtual environment with the pixlstash wheel already installed.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=19538,
        help="Loopback port to bind (default: 19538).",
    )
    parser.add_argument(
        "--expected-version",
        default=None,
        help="Version the wheel should report from /version, if known.",
    )
    args = parser.parse_args()

    try:
        run_smoke(args.venv, args.port, args.expected_version)
    except SmokeFailure as exc:
        logger.error("SMOKE FAILED: %s", exc)
        return 1

    logger.info("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
