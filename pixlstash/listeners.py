"""Listener orchestration for the PixlStash server.

Extracted verbatim from ``pixlstash.server`` (Phase 2, §4.1 of the backend
refactor). Owns LAN-IP discovery, the startup banner, and the Electron desktop
dual-listener launch path (a private loopback HTTP listener plus an optional
external listener). ``Server`` inherits ``ListenersMixin`` so the original
``self.``-bound call sites in ``run()``/``lifespan``/``__init__`` are unchanged.
"""

import asyncio
import signal
import socket

import uvicorn

from pixlstash.pixl_logging import get_logger, uvicorn_log_config

logger = get_logger(__name__)


def _get_lan_ip() -> str | None:
    """Return the machine's primary LAN IP by probing an outbound UDP route.

    Does not send any data. Returns None if the IP cannot be determined.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError as exc:
        # No default route / unreachable interface: we cannot determine the LAN
        # IP. Log it so a misconfigured host that can't add the LAN IP to the
        # cert SAN leaves a diagnostic instead of failing silently.
        logger.warning("Could not determine LAN IP: %s", exc)
        return None


class ListenersMixin:
    """Startup banner and Electron desktop listener orchestration for ``Server``."""

    @staticmethod
    def _print_banner(version, labelled_urls):
        """Print the startup box, one ``Label : url`` row per listener."""
        _w = 54
        _b = "═" * _w
        lines = [
            f"  ╔{_b}╗",
            f"  ║{'  PixlStash  v' + version:<{_w}}║",
            f"  ╠{_b}╣",
            f"  ║{'  GitHub : https://github.com/pikselkroken/pixlstash':<{_w}}║",
        ]
        for label, url in labelled_urls:
            lines.append(f"  ║{'  ' + f'{label:<6}' + ' : ' + url:<{_w}}║")
        lines.append(f"  ╚{_b}╝")
        print("\n" + "\n".join(lines) + "\n")

    def _external_listener_password_ready(self) -> bool:
        """Return True only if the owner account has a password set.

        The external (remote access) listener binds ``0.0.0.0`` and is reachable
        from the LAN. The desktop owner is auto-logged-in via the seeded loopback
        session and never goes through registration, so ``password_hash`` can be
        ``None``. Binding the external listener in that state lets any LAN device
        claim the empty owner account, so we refuse to expose it until a password
        exists. Fails closed: any error determining the state is treated as "not
        ready" and logged.
        """
        auth = getattr(self, "auth", None)
        if auth is None:
            # No auth service wired (e.g. a unit-test stand-in). Fail closed.
            logger.error(
                "Refusing external listener: no auth service available to verify "
                "an owner password is set."
            )
            return False
        try:
            user = auth.get_user()
        except Exception as exc:
            logger.error(
                "Refusing external listener: failed to load the owner user to "
                "verify a password is set: %s",
                exc,
            )
            return False
        if user is None or not user.password_hash:
            return False
        return True

    def _build_electron_configs(self, loop_host, loop_port):
        """Build the uvicorn configs (and banner rows) for the desktop listeners.

        Always yields the loopback listener - plain HTTP on the ephemeral
        ``loop_host``/``loop_port`` the shell forces (PIXLSTASH_HOST/PORT) - and,
        when the user has enabled remote access, a second *external* listener on
        the configured port (optionally HTTPS). Both serve the same FastAPI app;
        the external one runs with ``lifespan='off'`` so the loopback server is
        the sole owner of the app lifespan (the vault starts/stops exactly once).

        Returns ``(configs, banner)`` where ``banner`` is a list of
        ``(label, url)`` rows. Pure (no side effects beyond logging) so it can be
        unit-tested without binding sockets.
        """
        configs = [
            uvicorn.Config(
                self.api,
                host=loop_host,
                port=loop_port,
                log_config=uvicorn_log_config,
            )
        ]
        banner = [("Window", f"http://{loop_host}:{loop_port}")]

        if self._server_config.get("external_server_enabled", False):
            if not ListenersMixin._external_listener_password_ready(self):
                logger.error(
                    "Refusing to bind the external (remote access) listener: the "
                    "owner account has no password set. Set an owner password "
                    "before enabling remote access so a LAN device cannot claim "
                    "the account. Serving the loopback window only."
                )
                return configs, banner
            ext_host = "0.0.0.0"
            ext_port = int(self._server_config.get("port", 9537))
            ext_kwargs = dict(
                host=ext_host,
                port=ext_port,
                log_config=uvicorn_log_config,
                lifespan="off",
            )
            scheme = "http"
            if self._server_config.get("require_ssl", False):
                ext_kwargs["ssl_keyfile"] = self._server_config.get("ssl_keyfile")
                ext_kwargs["ssl_certfile"] = self._server_config.get("ssl_certfile")
                scheme = "https"
                print(
                    "[SSL] External listener using SSL: "
                    f"keyfile={self._server_config.get('ssl_keyfile')}, "
                    f"certfile={self._server_config.get('ssl_certfile')}"
                )
            configs.append(uvicorn.Config(self.api, **ext_kwargs))
            banner.append(("Remote", f"{scheme}://{ext_host}:{ext_port}"))

        return configs, banner

    def _run_electron_listeners(self, version, loop_host, loop_port):
        """Serve the desktop backend's loopback and (optional) external listeners.

        See :meth:`_build_electron_configs` for the listener layout. The two
        servers run in one event loop; a single combined signal handler stops
        both on SIGTERM/SIGINT.
        """
        configs, banner = self._build_electron_configs(loop_host, loop_port)
        self._print_banner(version, banner)

        servers = [uvicorn.Server(config) for config in configs]
        self._uvicorn_servers = servers
        for server in servers:
            # We install one combined signal handler below so a single
            # SIGTERM/SIGINT stops *both* listeners; uvicorn's per-server
            # handlers would only stop the server that registered last.
            server.install_signal_handlers = False

        # ``banner`` rows are produced in the same order as ``configs`` by
        # ``_build_electron_configs`` (loopback first, optional external second),
        # so the label for each server is its banner row. Fall back to the bound
        # socket if the banner is somehow shorter than the server list.
        labels = [f"{label} ({url})" for label, url in banner[: len(servers)]]
        while len(labels) < len(servers):
            cfg = servers[len(labels)].config
            labels.append(f"listener ({cfg.host}:{cfg.port})")

        async def _serve():
            running_loop = asyncio.get_running_loop()

            def _stop():
                for server in servers:
                    server.should_exit = True

            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    running_loop.add_signal_handler(sig, _stop)
                except NotImplementedError:
                    # Windows asyncio loops don't support add_signal_handler;
                    # the desktop shell force-kills the process tree there.
                    logger.debug(
                        "add_signal_handler unsupported on this event loop; "
                        "skipping handler for %s (Windows fallback: the desktop "
                        "shell force-kills the process tree).",
                        sig,
                    )

            async def _serve_one(server, label):
                # Wrap each listener so a bind failure (e.g. the external
                # 0.0.0.0 socket already in use, or the loopback ephemeral port
                # stolen between the bind-check and the bind) is logged with
                # *which* socket failed before it propagates. A bare
                # ``asyncio.gather`` surfaces the first exception with no
                # indication of which listener raised it.
                try:
                    await server.serve()
                except Exception as exc:
                    logger.error("Desktop %s failed: %s", label, exc, exc_info=True)
                    # Stop the sibling listener so we don't leave a half-running
                    # backend, then re-raise so the process exits non-zero.
                    for other in servers:
                        other.should_exit = True
                    raise

            await asyncio.gather(
                *(_serve_one(server, label) for server, label in zip(servers, labels))
            )

        try:
            asyncio.run(_serve())
        finally:
            if getattr(self, "vault", None) is not None:
                self.vault.close()
