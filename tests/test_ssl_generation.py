"""Tests for automatic self-signed SSL certificate generation."""

import datetime
import os


from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID

from pixlstash.server import Server


def _make_ssl_config(tmpdir, keyfile_name="key.pem", certfile_name="cert.pem"):
    return {
        "require_ssl": True,
        "ssl_keyfile": os.path.join(tmpdir, keyfile_name),
        "ssl_certfile": os.path.join(tmpdir, certfile_name),
    }


def _call_ensure_ssl(server_config):
    """Invoke _ensure_ssl_certificates without constructing a full Server."""
    instance = object.__new__(Server)
    instance._server_config = server_config
    instance._ensure_ssl_certificates()


class TestEnsureSslCertificates:
    def test_generates_key_and_cert_files(self, tmp_path):
        config = _make_ssl_config(str(tmp_path))
        _call_ensure_ssl(config)

        assert os.path.exists(config["ssl_keyfile"]), "Key file was not created"
        assert os.path.exists(config["ssl_certfile"]), "Cert file was not created"

    def test_generated_cert_is_valid_pem(self, tmp_path):
        config = _make_ssl_config(str(tmp_path))
        _call_ensure_ssl(config)

        with open(config["ssl_certfile"], "rb") as f:
            cert_pem = f.read()

        cert = x509.load_pem_x509_certificate(cert_pem)
        assert cert is not None

    def test_generated_key_is_valid_pem(self, tmp_path):
        config = _make_ssl_config(str(tmp_path))
        _call_ensure_ssl(config)

        with open(config["ssl_keyfile"], "rb") as f:
            key_pem = f.read()

        key = serialization.load_pem_private_key(key_pem, password=None)
        assert key is not None

    def test_cert_common_name_is_localhost(self, tmp_path):
        config = _make_ssl_config(str(tmp_path))
        _call_ensure_ssl(config)

        with open(config["ssl_certfile"], "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())

        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        assert len(cn) == 1
        assert cn[0].value == "localhost"

    def test_cert_has_localhost_san(self, tmp_path):
        config = _make_ssl_config(str(tmp_path))
        _call_ensure_ssl(config)

        with open(config["ssl_certfile"], "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())

        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        dns_names = san.value.get_values_for_type(x509.DNSName)
        assert "localhost" in dns_names

    def test_cert_validity_is_approximately_one_year(self, tmp_path):
        config = _make_ssl_config(str(tmp_path))
        _call_ensure_ssl(config)

        with open(config["ssl_certfile"], "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())

        validity = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert datetime.timedelta(days=364) <= validity <= datetime.timedelta(days=366)

    def test_cert_is_not_yet_expired(self, tmp_path):
        config = _make_ssl_config(str(tmp_path))
        _call_ensure_ssl(config)

        with open(config["ssl_certfile"], "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())

        now = datetime.datetime.now(datetime.timezone.utc)
        assert cert.not_valid_before_utc <= now <= cert.not_valid_after_utc

    def test_does_not_overwrite_existing_files(self, tmp_path):
        config = _make_ssl_config(str(tmp_path))
        _call_ensure_ssl(config)

        keyfile_mtime = os.path.getmtime(config["ssl_keyfile"])
        certfile_mtime = os.path.getmtime(config["ssl_certfile"])

        _call_ensure_ssl(config)

        assert os.path.getmtime(config["ssl_keyfile"]) == keyfile_mtime
        assert os.path.getmtime(config["ssl_certfile"]) == certfile_mtime

    def test_creates_intermediate_directories(self, tmp_path):
        nested_dir = str(tmp_path / "a" / "b" / "c")
        config = _make_ssl_config(nested_dir)
        _call_ensure_ssl(config)

        assert os.path.exists(config["ssl_keyfile"])
        assert os.path.exists(config["ssl_certfile"])

    def test_key_matches_cert_public_key(self, tmp_path):
        config = _make_ssl_config(str(tmp_path))
        _call_ensure_ssl(config)

        with open(config["ssl_keyfile"], "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        with open(config["ssl_certfile"], "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())

        key_pub = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cert_pub = cert.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        assert key_pub == cert_pub
