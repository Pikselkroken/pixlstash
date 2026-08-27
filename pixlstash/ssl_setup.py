"""Self-signed SSL certificate generation for the PixlStash server.

Extracted verbatim from ``pixlstash.server`` (Phase 2, §4.1 of the backend
refactor). ``SslSetupMixin`` generates a self-signed key/cert pair on demand for
the optional HTTPS listener. ``Server`` inherits the mixin so the original
``self._ensure_ssl_certificates()`` call site in ``__init__`` is unchanged.
"""

import os

from pixlstash.listeners import _get_lan_ip
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class SslSetupMixin:
    """On-demand self-signed certificate generation for ``Server``."""

    def _ensure_ssl_certificates(self):
        import datetime
        import ipaddress

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        keyfile = self._server_config.get("ssl_keyfile")
        certfile = self._server_config.get("ssl_certfile")
        # If either file is missing, generate self-signed cert
        if not (os.path.exists(keyfile) and os.path.exists(certfile)):
            os.makedirs(os.path.dirname(keyfile), exist_ok=True)
            os.makedirs(os.path.dirname(certfile), exist_ok=True)
            print(f"[SSL] Generating self-signed certificate: {certfile}, {keyfile}")
            try:
                private_key = rsa.generate_private_key(
                    public_exponent=65537,
                    key_size=2048,
                )
                subject = issuer = x509.Name(
                    [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
                )
                # The external listener binds 0.0.0.0 and is reached by LAN IP,
                # so cover localhost AND the loopback/LAN addresses in the SAN -
                # otherwise every remote HTTPS connection is a hostname *mismatch*
                # (worse than a plain untrusted-CA warning).
                san = [x509.DNSName("localhost")]
                for ip_text in ("127.0.0.1", _get_lan_ip()):
                    if not ip_text:
                        continue
                    try:
                        san.append(x509.IPAddress(ipaddress.ip_address(ip_text)))
                    except ValueError:
                        logger.debug("Skipping unparseable SAN address %r.", ip_text)
                now = datetime.datetime.now(datetime.timezone.utc)
                cert = (
                    x509.CertificateBuilder()
                    .subject_name(subject)
                    .issuer_name(issuer)
                    .public_key(private_key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(now)
                    .not_valid_after(now + datetime.timedelta(days=365))
                    .add_extension(
                        x509.SubjectAlternativeName(san),
                        critical=False,
                    )
                    .sign(private_key, hashes.SHA256())
                )
                with open(keyfile, "wb") as f:
                    f.write(
                        private_key.private_bytes(
                            serialization.Encoding.PEM,
                            serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption(),
                        )
                    )
                with open(certfile, "wb") as f:
                    f.write(cert.public_bytes(serialization.Encoding.PEM))
            except Exception as e:
                print(f"[SSL] Failed to generate self-signed certificate: {e}")
                raise
