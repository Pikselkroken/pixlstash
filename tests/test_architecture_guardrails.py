"""Architecture guardrail tests.

These tests enforce structural invariants that protect the refactored
architecture from regressing.  Most run in "audit mode" with an explicit
allowlist of known transitional violations; the allowlist shrinks as the
codebase migrates.
"""

import ast
import re
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ROUTES_DIR = REPO_ROOT / "pixlstash" / "routes"
TASKS_DIR = REPO_ROOT / "pixlstash" / "tasks"
SERVICES_DIR = REPO_ROOT / "pixlstash" / "services"
SERVER_PY = REPO_ROOT / "pixlstash" / "server.py"


# ---------------------------------------------------------------------------
# Guardrail 1: No private vault access from route handlers
# ---------------------------------------------------------------------------

def _iter_python_files(directory: Path):
    return directory.rglob("*.py")


def _has_private_vault_access(source: str) -> list[tuple[int, str]]:
    """Return (lineno, snippet) for any private attribute access on vault.

    Detects patterns like ``vault._attr`` or ``server.vault._attr``.
    """
    hits = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        if re.search(r"vault\._[a-zA-Z]", line):
            hits.append((lineno, line.strip()))
    return hits


def test_no_private_vault_access_from_routes():
    violations = []
    for path in sorted(_iter_python_files(ROUTES_DIR)):
        source = path.read_text()
        hits = _has_private_vault_access(source)
        for lineno, snippet in hits:
            violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {snippet}")
    assert not violations, (
        "Private vault attribute access detected in route handlers.\n"
        "Add a public method to Vault instead:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Guardrail 2: Direct DB calls from routes (audit mode with allowlist)
#
# The allowed set shrinks as routes are migrated to service functions.
# Remove a file from the allowlist once its direct db calls are refactored.
# ---------------------------------------------------------------------------

_DB_CALL_PATTERN = re.compile(r"vault\.db\.run_(task|immediate_read_task)")

# Known transitional files that still call vault.db.run_* directly.
# Remove each file from this set once it is migrated to a service function.
_DIRECT_DB_CALL_ALLOWLIST = {
    "pixlstash/routes/characters.py",
    "pixlstash/routes/comfyui.py",
    "pixlstash/routes/config.py",
    "pixlstash/routes/guest_scores.py",
    "pixlstash/routes/import_folders.py",
    "pixlstash/routes/picture_sets.py",
    "pixlstash/routes/pictures.py",
    "pixlstash/routes/projects.py",
    "pixlstash/routes/reference_folders.py",
    "pixlstash/routes/share.py",
    "pixlstash/routes/stacks.py",
    "pixlstash/routes/tag_predictions.py",
    "pixlstash/routes/tags.py",
}


def test_no_new_direct_db_calls_from_routes():
    """Fail if a route file that is NOT in the allowlist calls vault.db directly."""
    unlisted_violations = []
    for path in sorted(_iter_python_files(ROUTES_DIR)):
        rel = str(path.relative_to(REPO_ROOT))
        if not _DB_CALL_PATTERN.search(path.read_text()):
            continue
        if rel not in _DIRECT_DB_CALL_ALLOWLIST:
            unlisted_violations.append(rel)
    assert not unlisted_violations, (
        "New direct vault.db calls found in route file(s) not in the allowlist.\n"
        "Add a service function in pixlstash/services/ instead:\n"
        + "\n".join(unlisted_violations)
    )


# ---------------------------------------------------------------------------
# Guardrail 3: Services must not call vault.db directly
# ---------------------------------------------------------------------------

def test_services_no_direct_db_calls():
    # Known transitional service files that still call vault.db.run_* directly.
    # Remove each file from this set once it is migrated to accept a Session.
    _direct_db_call_service_allowlist = {
        "pixlstash/services/picture_stats.py",  # pending session injection refactor
    }

    violations = []
    for path in sorted(_iter_python_files(SERVICES_DIR)):
        rel = str(path.relative_to(REPO_ROOT))
        source = path.read_text()
        if not _DB_CALL_PATTERN.search(source):
            continue
        if rel in _direct_db_call_service_allowlist:
            continue
        for lineno, line in enumerate(source.splitlines(), start=1):
            if _DB_CALL_PATTERN.search(line):
                violations.append(f"{rel}:{lineno}: {line.strip()}")
    assert not violations, (
        "Service files must receive a pre-opened session, not call vault.db directly:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Guardrail 4: All finder depends_on() strings resolve to registered finders
# ---------------------------------------------------------------------------

def _extract_string_literals_from_return(func_node: ast.FunctionDef) -> list[str]:
    """Collect every string constant returned directly by a function."""
    results = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and node.value is not None:
            for child in ast.walk(node.value):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    results.append(child.value)
    return results


def _collect_finder_info() -> tuple[set[str], set[str]]:
    """Return (all_finder_names, all_depends_on_strings) from task finder files."""
    finder_names: set[str] = set()
    depends_on_strings: set[str] = set()

    for path in sorted(TASKS_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                if item.name == "finder_name":
                    finder_names.update(
                        _extract_string_literals_from_return(item)
                    )
                elif item.name == "depends_on":
                    depends_on_strings.update(
                        _extract_string_literals_from_return(item)
                    )

    return finder_names, depends_on_strings


def test_finder_dependencies_resolve_to_registered_finders():
    finder_names, depends_on_strings = _collect_finder_info()
    assert finder_names, "Expected to find at least one finder_name() — check task file paths"

    unresolved = depends_on_strings - finder_names
    assert not unresolved, (
        "Finder depends_on() references names that don't match any registered "
        f"finder_name():\n{sorted(unresolved)}"
    )


# ---------------------------------------------------------------------------
# Guardrail 5: Every EventType is classified for WebSocket broadcast
# ---------------------------------------------------------------------------

def test_event_types_fully_classified():
    from pixlstash.event_types import EventType

    all_event_types = {et.name for et in EventType}

    # EventTypes broadcast to WebSocket clients (from _should_send_ws_update).
    broadcast_types = {
        "CHANGED_PICTURES",
        "PICTURE_IMPORTED",
        "PLUGIN_PROGRESS",
        "CHANGED_TAGS",
        "CLEARED_TAGS",
        "CHANGED_CHARACTERS",
        "CHANGED_FACES",
    }

    # EventTypes explicitly NOT broadcast (silently drop or stats-only).
    # Extend this set when a new event type is intentionally excluded.
    non_broadcast_types = {
        "CHANGED_DESCRIPTIONS",  # description updates do not trigger WS refresh
        "QUALITY_UPDATED",       # used only to invalidate the stats cache
    }

    classified = broadcast_types | non_broadcast_types
    unclassified = all_event_types - classified

    assert not unclassified, (
        "New EventType member(s) added without broadcast classification.\n"
        "Add each to broadcast_types in _should_send_ws_update (server.py) OR "
        "to non_broadcast_types in this test with an explanatory comment:\n"
        + str(sorted(unclassified))
    )

    unknown_in_broadcast = broadcast_types - all_event_types
    assert not unknown_in_broadcast, (
        f"broadcast_types references EventType(s) that no longer exist: {unknown_in_broadcast}"
    )

    unknown_in_non_broadcast = non_broadcast_types - all_event_types
    assert not unknown_in_non_broadcast, (
        f"non_broadcast_types references EventType(s) that no longer exist: {unknown_in_non_broadcast}"
    )


# ---------------------------------------------------------------------------
# Guardrail 6: Workers start via lifecycle, not at import / __init__ time
# ---------------------------------------------------------------------------

def test_workers_not_started_at_vault_init():
    """Vault.__init__ must not start worker threads; Vault.start() must."""
    from pixlstash.vault import Vault

    with tempfile.TemporaryDirectory() as tmp:
        vault = Vault(image_root=tmp, disable_background_workers=False)
        try:
            assert vault._task_runner is not None, "_task_runner should be created in __init__"
            assert not vault._task_runner.is_running(), (
                "TaskRunner must NOT be running after Vault.__init__() — "
                "workers should only start when Vault.start() is called"
            )
            assert not vault._work_planner.is_running(), (
                "WorkPlanner must NOT be running after Vault.__init__()"
            )

            vault.start()

            assert vault._task_runner.is_running(), (
                "TaskRunner must be running after Vault.start()"
            )
            assert vault._work_planner.is_running(), (
                "WorkPlanner must be running after Vault.start()"
            )
        finally:
            vault.close()
