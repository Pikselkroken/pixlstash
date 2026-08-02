"""Sending the anonymous install ping.

This is the only part of PixlStash that transmits telemetry, and it is gated on
the user having turned it on. Nothing here runs unless
``user.telemetry_send_install_id`` is true.

**Server-side, not in the browser, deliberately.** The existing update check runs
in the frontend (``useVersionCheck.js``) keyed on ``localStorage``, which means a
headless install only ever checks when somebody happens to open the web UI. The
whole retention design assumes Docker installs ping daily because they are
persistent services; that is only true if the ping comes from the server process.
Sending from here also keeps the install ID off the browser entirely: no CORS
exposure, and the identifier never leaves the machine's own process.

Every failure mode here is non-fatal by construction. A telemetry send that can
delay startup, raise into a request, or retry in a tight loop is a worse bug than
the missing measurement it was trying to collect.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

from pixlstash.pixl_logging import get_logger
from pixlstash.telemetry.install_id import read_install_identity
from pixlstash.utils.atomic_write import write_json_atomic

logger = get_logger(__name__)

TELEMETRY_ENDPOINT = "https://t.pixlstash.dev/v1/ping"

#: Records the last send attempt so a restart loop cannot ping repeatedly.
#: Sits beside the install ID rather than in the library database, for the same
#: reason: a snapshot restore must not change how often an install reports.
SEND_STATE_FILENAME = "telemetry-state.json"

#: One ping per day. Docker installs would otherwise report on every restart.
SEND_INTERVAL_SECONDS = 24 * 60 * 60

#: Short and unretried. The measurement is not worth holding a thread open.
REQUEST_TIMEOUT_SECONDS = 10

#: How often the loop wakes to ask whether a ping is due. Well under a day, so a
#: long-running container sends promptly after the 24h window opens rather than
#: waiting for the next restart. ``is_due`` enforces the actual spacing.
CHECK_INTERVAL_SECONDS = 60 * 60

#: Environment markers that suppress sending outright. Our own CI, test runs and
#: the public demo would otherwise manufacture installs that do not exist and
#: pollute exactly the cohorts this exists to measure.
_SUPPRESS_ENV_VARS = ("PYTEST_CURRENT_TEST", "CI", "PIXLSTASH_DEMO_MODE")


def _state_path(server_config_path: str) -> str:
    """Path of the last-sent marker, beside the server config."""
    directory = os.path.dirname(os.path.abspath(server_config_path))
    return os.path.join(directory, SEND_STATE_FILENAME)


def suppressed_reason() -> str | None:
    """Return why sending is suppressed in this environment, or None.

    Checked at send time rather than import time so a test that sets the marker
    after import is still honoured.
    """
    for name in _SUPPRESS_ENV_VARS:
        if os.environ.get(name, "").strip():
            return name
    return None


def _read_last_attempt(server_config_path: str) -> float | None:
    """Return the last send attempt as a POSIX timestamp, or None."""
    path = _state_path(server_config_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
        # `last_sent_at` is the original field name from pre-release builds.
        # Read it as a fallback so upgrading does not immediately send again.
        value = record.get("last_attempt_at", record.get("last_sent_at"))
        return float(value) if value is not None else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        # A corrupt marker means we do not know when we last attempted. Treat that as
        # "never": at worst one extra ping, which the server deduplicates by id.
        logger.warning(
            "Could not read telemetry send state %s (%s); treating as never sent.",
            path,
            exc,
        )
        return None


def _write_last_attempt(server_config_path: str, when: float) -> bool:
    """Durably reserve one send attempt. Return False if that is impossible."""
    path = _state_path(server_config_path)
    try:
        write_json_atomic(path, {"last_attempt_at": when})
    except OSError as exc:
        logger.warning(
            "Could not write telemetry send state %s (%s); refusing to send "
            "because the once-per-day limit cannot be enforced.",
            path,
            exc,
        )
        return False
    return True


def is_due(server_config_path: str, now: float | None = None) -> bool:
    """True if a ping is due, i.e. none was attempted in the last 24 hours."""
    moment = now if now is not None else datetime.now(timezone.utc).timestamp()
    last = _read_last_attempt(server_config_path)
    if last is None:
        return True
    # A clock moved backwards would otherwise suppress sending until real time
    # caught up, which on a badly-set clock could be years.
    if moment < last:
        logger.warning(
            "Telemetry send state is in the future (last=%s, now=%s); sending.",
            last,
            moment,
        )
        return True
    return (moment - last) >= SEND_INTERVAL_SECONDS


def send_install_ping(
    server_config_path: str,
    install_type: str,
    *,
    endpoint: str = TELEMETRY_ENDPOINT,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> bool:
    """POST one install ping. Returns True only on a confirmed 2xx.

    Never raises. The caller runs on a background thread and has nothing useful
    to do with an exception.

    Args:
        server_config_path: Path to the server config, which locates the ID.
        install_type: One of docker, pip, electron, other.
        endpoint: Override, for tests.
        timeout: Socket timeout in seconds.

    Returns:
        True if the endpoint accepted the ping.
    """
    identity = read_install_identity(server_config_path)
    if identity is None:
        logger.warning(
            "Telemetry is enabled but no install ID is stored; skipping the "
            "ping. The config directory may not be writable."
        )
        return False

    # Exactly the three keys the ingestion Worker accepts. It rejects any
    # unrecognised key outright, so an extra field here makes every ping a 400.
    body = json.dumps(
        {
            "install_id": identity.install_id,
            "is_new_install": identity.is_new_install,
            "install_type": install_type,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    # Reserve the attempt BEFORE opening the connection. If this marker cannot
    # be persisted, sending would make the hourly loop transmit every hour once
    # the previous marker is stale, violating the user-facing "once a day, at
    # most" promise. A failed network attempt is deliberately not retried until
    # tomorrow; telemetry is never important enough to trade privacy guarantees
    # for delivery.
    attempted_at = datetime.now(timezone.utc).timestamp()
    if not _write_last_attempt(server_config_path, attempted_at):
        return False

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            ok = 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        # A 4xx means we are sending something the endpoint refuses, which is a
        # bug on our side and worth saying loudly. A 5xx is theirs and transient.
        logger.warning(
            "Telemetry ping rejected with HTTP %s from %s. No retry.",
            exc.code,
            endpoint,
        )
        return False
    except Exception as exc:
        # Offline, DNS failure, TLS failure, timeout. Expected on any machine
        # without internet access, so this is not a warning-level event.
        logger.debug("Telemetry ping to %s did not complete (%s).", endpoint, exc)
        return False

    if ok:
        logger.debug("Sent the anonymous install ping.")
    return ok


def maybe_send_in_background(
    server_config_path: str,
    install_type: str,
    consent_enabled: bool,
) -> threading.Thread | None:
    """Send a ping on a daemon thread if consent is on and one is due.

    Returns the thread (for tests) or None when nothing was started. Startup
    never waits on it: the thread is a daemon, so a hung socket cannot keep the
    process alive at shutdown either.
    """
    if not consent_enabled:
        return None

    reason = suppressed_reason()
    if reason:
        logger.debug("Telemetry send suppressed by %s.", reason)
        return None

    if not is_due(server_config_path):
        return None

    thread = threading.Thread(
        target=send_install_ping,
        args=(server_config_path, install_type),
        name="telemetry-ping",
        daemon=True,
    )
    thread.start()
    return thread


def run_periodic_sender(
    server_config_path: str,
    install_type: str,
    consent_check,
    *,
    interval: float = CHECK_INTERVAL_SECONDS,
    stop_event: threading.Event | None = None,
    max_cycles: int | None = None,
) -> None:
    """Loop until stopped, sending at most one ping a day.

    Startup-only dispatch was not enough: a Docker container that stays up for
    six weeks would ping once, which is precisely the population the retention
    design assumes reports daily.

    ``consent_check`` is a callable re-evaluated on every cycle rather than a
    boolean captured once. A cached value keeps transmitting after the user has
    opted out, and the window between opt-out and the next restart is exactly
    when honouring it matters.

    Args:
        server_config_path: Locates the install ID and the send-state marker.
        install_type: One of docker, pip, electron, other.
        consent_check: Zero-arg callable returning the CURRENT consent state.
        interval: Seconds between checks.
        stop_event: Set to end the loop.
        max_cycles: Stop after this many cycles. Tests only.
    """
    stopper = stop_event or threading.Event()
    cycles = 0
    while not stopper.is_set():
        try:
            if (
                consent_check()
                and not suppressed_reason()
                and is_due(server_config_path)
            ):
                send_install_ping(server_config_path, install_type)
        except Exception as exc:
            # The loop must outlive any single failure, including a consent
            # check that raises. Never let telemetry take a thread down.
            logger.warning("Telemetry cycle failed (%s); continuing.", exc)

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return
        stopper.wait(interval)


def start_periodic_sender(
    server_config_path: str,
    install_type: str,
    consent_check,
    *,
    interval: float = CHECK_INTERVAL_SECONDS,
) -> threading.Thread | None:
    """Start the daily send loop on a daemon thread.

    Returns None when this environment suppresses sending outright, so a demo
    or CI process never even starts the thread.
    """
    reason = suppressed_reason()
    if reason:
        logger.debug("Telemetry loop not started, suppressed by %s.", reason)
        return None

    thread = threading.Thread(
        target=run_periodic_sender,
        args=(server_config_path, install_type, consent_check),
        kwargs={"interval": interval},
        name="telemetry-ping-loop",
        daemon=True,
    )
    thread.start()
    return thread
