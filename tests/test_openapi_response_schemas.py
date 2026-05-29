"""Guards on the generated OpenAPI schema (what the Scalar reference renders).

Two invariants, both regression-prone as new endpoints are added:

* Every 2xx JSON response declares a schema — otherwise Scalar shows a bare
  ``null`` for the response body.
* No query parameter carries a ``default`` in its schema — otherwise Scalar
  pre-fills the "try it" example URL with redundant default values
  (``?limit=<MAXINT>&offset=0&...``).
"""

import functools
import gc
import json
import os
import tempfile

from pixlstash.server import Server

# Media types that legitimately carry no JSON schema (file/binary downloads).
_BINARY_MEDIA_PREFIXES = ("image/", "application/zip", "application/octet-stream")
_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@functools.lru_cache(maxsize=1)
def _build_schema():
    """Build the live OpenAPI schema once and reuse it across tests."""
    temp_dir = tempfile.TemporaryDirectory()
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000}))
    Server.DEFAULT_FORCE_CPU = True
    server = Server(server_config_path)
    try:
        return server.api.openapi()
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_no_endpoint_has_null_success_response():
    schema = _build_schema()
    offenders = []
    for path, item in schema.get("paths", {}).items():
        for method, operation in item.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            responses = operation.get("responses", {})
            success_codes = [c for c in responses if c.startswith("2")]
            for code in success_codes:
                if code == "204":  # No Content — legitimately empty.
                    continue
                content = responses[code].get("content")
                if not content:
                    offenders.append(f"{method.upper()} {path} [{code}] has no content")
                    continue
                for media, media_obj in content.items():
                    if media_obj.get("schema"):
                        continue
                    # An empty schema is only acceptable for binary downloads.
                    if not media.startswith(_BINARY_MEDIA_PREFIXES):
                        offenders.append(
                            f"{method.upper()} {path} [{code}] {media}: empty schema"
                        )

    assert not offenders, "Endpoints render a null/empty success body in Scalar:\n" + "\n".join(
        offenders
    )


def test_no_query_param_declares_a_default():
    schema = _build_schema()
    offenders = []
    for path, item in schema.get("paths", {}).items():
        for method, operation in item.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            for param in operation.get("parameters", []):
                if param.get("in") != "query":
                    continue
                param_schema = param.get("schema") or {}
                branches = [param_schema, *param_schema.get("anyOf", [])]
                if any(isinstance(b, dict) and "default" in b for b in branches):
                    offenders.append(f"{method.upper()} {path} ?{param.get('name')}")

    assert not offenders, (
        "Query params carry a schema default, so Scalar pre-fills the example "
        "URL with redundant default values:\n" + "\n".join(offenders)
    )


def test_no_operation_has_duplicate_tags():
    schema = _build_schema()
    offenders = []
    for path, item in schema.get("paths", {}).items():
        for method, operation in item.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            tags = operation.get("tags") or []
            if len(tags) != len(set(tags)):
                offenders.append(f"{method.upper()} {path} {tags}")

    assert not offenders, (
        "Operations carry a duplicated tag (usually a per-decorator tag on top "
        "of the router-level one), so Scalar lists them twice in the sidebar:\n"
        + "\n".join(offenders)
    )
