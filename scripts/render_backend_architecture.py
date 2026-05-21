"""Regenerate the auto-generated sections of docs/backend_architecture.md.

Usage
-----
Regenerate in-place:
    python scripts/render_backend_architecture.py

Check (CI mode — exit 1 if the file would change):
    python scripts/render_backend_architecture.py --check

The script injects content between ``<!-- AUTOGEN:start -->`` /
``<!-- AUTOGEN:end -->`` marker pairs.  Multiple named pairs are supported via
``<!-- AUTOGEN:start name="<key>" -->`` / ``<!-- AUTOGEN:end name="<key>" -->``.
Each section is regenerated independently.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOCS_FILE = os.path.join(REPO_ROOT, "docs", "backend_architecture.md")
SERVER_PY = os.path.join(REPO_ROOT, "pixlstash", "server.py")


# ---------------------------------------------------------------------------
# Helpers: AUTOGEN marker injection
# ---------------------------------------------------------------------------

_START_RE = re.compile(r"<!--\s*AUTOGEN:start(?:\s+name=\"(?P<name>[^\"]+)\")?\s*-->")


def _inject(original: str, sections: dict[str | None, str]) -> str:
    """Replace the content between every AUTOGEN marker pair with new content.

    Args:
        original: The full original document text.
        sections: Mapping of marker name (or ``None`` for unnamed) → new body.
    """
    result = []
    pos = 0
    while pos < len(original):
        m = _START_RE.search(original, pos)
        if not m:
            result.append(original[pos:])
            break
        name = m.group("name")
        end_pat = re.compile(
            r"<!--\s*AUTOGEN:end"
            + (
                r'\s+name="' + re.escape(name) + r'"'
                if name
                else r"(?:\s+name=\"[^\"]+\")?"
            )
            + r"\s*-->"
        )
        end_m = end_pat.search(original, m.end())
        if not end_m:
            raise ValueError(
                f"AUTOGEN start marker with name={name!r} has no matching end marker."
            )

        result.append(original[pos : m.end()])  # include start marker itself
        result.append("\n")
        if name in sections:
            result.append(sections[name])
        elif None in sections and name is None:
            result.append(sections[None])
        result.append(end_m.group())  # include end marker itself
        pos = end_m.end()

    return "".join(result)


# ---------------------------------------------------------------------------
# Route table extraction
# ---------------------------------------------------------------------------


def _build_minimal_server_config(config_path: str, image_root: str) -> None:
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    os.makedirs(image_root, exist_ok=True)
    config = {
        "host": "127.0.0.1",
        "port": 0,
        "log_level": "warning",
        "log_file": os.path.join(os.path.dirname(config_path), "server.log"),
        "require_ssl": False,
        "cookie_samesite": "Lax",
        "cookie_secure": False,
        "image_root": image_root,
        "default_device": "cpu",
        "min_free_disk_gb": 0.0,
        "min_free_vram_mb": 0.0,
        "cors_origins": [],
        "max_attachment_size_mb": 50,
        "generate_thumbnails_on_startup": False,
    }
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)


def _generate_route_table() -> str:
    """Return a markdown table of all HTTP routes + the WebSocket endpoint."""
    sys.path.insert(0, REPO_ROOT)
    from pixlstash.server import Server  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="pixlstash-renderdoc-") as tmp:
        config_path = os.path.join(tmp, "server-config.json")
        image_root = os.path.join(tmp, "images")
        _build_minimal_server_config(config_path, image_root)
        Server.DEFAULT_FORCE_CPU = True

        with Server(config_path) as server:
            schema = server.api.openapi()

    paths = schema.get("paths", {})

    rows: list[tuple[str, str, str, str]] = []  # (method, path, tags, summary)

    for path, path_item in sorted(paths.items()):
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            http_method = method.upper()
            tags = ", ".join(operation.get("tags", []))
            summary = operation.get("summary", "")
            rows.append((http_method, path, tags, summary))

    # WebSocket routes don't appear in OpenAPI — add manually from schema info comment.
    rows.append(("WS", "/api/v1/ws/updates", "config", "Real-time event stream"))
    rows.append(("WS", "/api/v1/ws/comfyui", "comfyui", "ComfyUI workflow progress"))

    col_w = [
        max(len("Method"), max(len(r[0]) for r in rows)),
        max(len("Path"), max(len(r[1]) for r in rows)),
        max(len("Tags"), max(len(r[2]) for r in rows)),
        max(len("Summary"), max(len(r[3]) for r in rows)),
    ]

    def row_str(cells: tuple[str, ...]) -> str:
        return "| " + " | ".join(c.ljust(col_w[i]) for i, c in enumerate(cells)) + " |"

    sep = "| " + " | ".join("-" * w for w in col_w) + " |"
    lines = [
        row_str(("Method", "Path", "Tags", "Summary")),
        sep,
        *[row_str(r) for r in rows],
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Event classification extraction
# ---------------------------------------------------------------------------


def _extract_broadcast_set() -> frozenset[str]:
    """Parse server.py with AST and return the EventType names broadcast to WS clients."""
    with open(SERVER_PY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=SERVER_PY)

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_should_send_ws_update":
            continue
        # Look for `event_type in (EventType.X, EventType.Y, ...)`
        for child in ast.walk(node):
            if not isinstance(child, ast.Compare):
                continue
            if len(child.ops) != 1 or not isinstance(child.ops[0], ast.In):
                continue
            comparator = child.comparators[0]
            if not isinstance(comparator, (ast.Tuple, ast.Set)):
                continue
            names: list[str] = []
            for elt in comparator.elts:
                if isinstance(elt, ast.Attribute) and isinstance(elt.value, ast.Name):
                    if elt.value.id == "EventType":
                        names.append(elt.attr)
            if names:
                return frozenset(names)

    return frozenset()


def _generate_event_table() -> str:
    """Return a markdown table classifying every EventType as broadcast or internal."""
    sys.path.insert(0, REPO_ROOT)
    from pixlstash.event_types import EventType  # noqa: PLC0415

    broadcast = _extract_broadcast_set()
    rows = []
    for et in EventType:
        is_broadcast = et.name in broadcast
        rows.append(
            (
                f"`{et.name}`",
                "✓ broadcast" if is_broadcast else "✗ internal",
            )
        )

    col_w = [
        max(len("Event"), max(len(r[0]) for r in rows)),
        max(len("WebSocket"), max(len(r[1]) for r in rows)),
    ]

    def row_str(cells: tuple[str, ...]) -> str:
        return "| " + " | ".join(c.ljust(col_w[i]) for i, c in enumerate(cells)) + " |"

    sep = "| " + " | ".join("-" * w for w in col_w) + " |"
    lines = [
        row_str(("Event", "WebSocket")),
        sep,
        *[row_str(r) for r in rows],
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    check_mode = "--check" in sys.argv

    with open(DOCS_FILE, encoding="utf-8") as fh:
        original = fh.read()

    # Verify the required markers exist before doing expensive server startup.
    if 'AUTOGEN:start name="routes"' not in original:
        print(
            f"ERROR: Missing AUTOGEN marker 'routes' in {DOCS_FILE}.\n"
            'Add <!-- AUTOGEN:start name="routes" --> / <!-- AUTOGEN:end name="routes" --> markers.',
            file=sys.stderr,
        )
        sys.exit(2)
    if 'AUTOGEN:start name="events"' not in original:
        print(
            f"ERROR: Missing AUTOGEN marker 'events' in {DOCS_FILE}.\n"
            'Add <!-- AUTOGEN:start name="events" --> / <!-- AUTOGEN:end name="events" --> markers.',
            file=sys.stderr,
        )
        sys.exit(2)

    print("Generating route table from server.api.routes …", flush=True)
    routes_md = _generate_route_table()

    print(
        "Generating event classification from EventType + _should_send_ws_update …",
        flush=True,
    )
    events_md = _generate_event_table()

    updated = _inject(original, {"routes": routes_md, "events": events_md})

    if check_mode:
        if updated != original:
            print(
                "FAIL: docs/backend_architecture.md is stale.\n"
                "Run `python scripts/render_backend_architecture.py` to regenerate.",
                file=sys.stderr,
            )
            sys.exit(1)
        print("OK: docs/backend_architecture.md is up to date.")
        return

    with open(DOCS_FILE, "w", encoding="utf-8") as fh:
        fh.write(updated)

    print(f"Updated {DOCS_FILE}")


if __name__ == "__main__":
    main()
