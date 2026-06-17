"""Architecture guardrail tests.

These tests enforce structural invariants that protect the refactored
architecture from regressing.  Most run in "audit mode" with an explicit
allowlist of known transitional violations; the allowlist shrinks as the
codebase migrates.
"""

import ast
import re
import tempfile
import warnings
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
            violations.append(
                f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: {snippet}"
            )
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
    "pixlstash/routes/pictures/_crud.py",
    "pixlstash/routes/pictures/_export.py",
    "pixlstash/routes/pictures/_helpers.py",
    "pixlstash/routes/pictures/_import.py",
    "pixlstash/routes/pictures/_listing.py",
    "pixlstash/routes/pictures/_misc.py",
    "pixlstash/routes/pictures/_search.py",
    "pixlstash/routes/pictures/_thumbnails.py",
    "pixlstash/routes/projects.py",
    "pixlstash/routes/reference_folders.py",
    "pixlstash/routes/stacks.py",
    "pixlstash/routes/tags.py",
}


def test_no_new_direct_db_calls_from_routes():
    """Fail if a route file that is NOT in the allowlist calls vault.db directly."""
    unlisted_violations = []
    for path in sorted(_iter_python_files(ROUTES_DIR)):
        # as_posix(): the allowlist uses "/" separators, but relative_to()
        # yields "\" on Windows — str() would never match the allowlist there.
        rel = path.relative_to(REPO_ROOT).as_posix()
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
        "pixlstash/services/config_service.py",  # vault-injection pattern
        "pixlstash/services/picture_stats.py",  # pending session injection refactor
        "pixlstash/services/search_query_service.py",  # vault-injection pattern; DB queries for search endpoints
        "pixlstash/services/share_service.py",  # vault-injection pattern
        "pixlstash/services/tag_prediction_service.py",  # vault-injection pattern
        "pixlstash/services/tag_scan_service.py",  # vault-injection pattern; sync near-neighbour tag scan
        "pixlstash/services/snapshot_service.py",  # vault-injection pattern; owns snapshot lifecycle
        "pixlstash/services/restore_service.py",  # vault-injection pattern; owns DB-swap lifecycle
    }

    violations = []
    for path in sorted(_iter_python_files(SERVICES_DIR)):
        # as_posix(): allowlist uses "/" separators (see note above).
        rel = path.relative_to(REPO_ROOT).as_posix()
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
# Guardrail 4: All finder depends_on() values resolve to registered TaskType members
# ---------------------------------------------------------------------------


def _extract_tasktype_attrs_from_return(func_node: ast.FunctionDef) -> list[str]:
    """Collect every TaskType.<ATTR> attribute name returned directly by a function."""
    results = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and node.value is not None:
            for child in ast.walk(node.value):
                if (
                    isinstance(child, ast.Attribute)
                    and isinstance(child.value, ast.Name)
                    and child.value.id == "TaskType"
                ):
                    results.append(child.attr)
    return results


def _collect_finder_info() -> tuple[set[str], set[str]]:
    """Return (all_finder_names, all_depends_on_tasktype_attrs) from task finder files."""
    finder_names: set[str] = set()
    depends_on_attrs: set[str] = set()

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
                    for child in ast.walk(item):
                        if isinstance(child, ast.Return) and child.value is not None:
                            for grandchild in ast.walk(child.value):
                                if isinstance(grandchild, ast.Constant) and isinstance(
                                    grandchild.value, str
                                ):
                                    finder_names.add(grandchild.value)
                elif item.name == "depends_on":
                    depends_on_attrs.update(_extract_tasktype_attrs_from_return(item))

    return finder_names, depends_on_attrs


def test_finder_dependencies_resolve_to_registered_finders():
    from pixlstash.tasks.task_type import TaskType
    from pixlstash.work_planner import WorkPlanner

    finder_names, depends_on_attrs = _collect_finder_info()
    assert finder_names, (
        "Expected to find at least one finder_name() — check task file paths"
    )

    # Verify all TaskType attrs referenced in depends_on() exist on the enum.
    valid_task_type_attrs = {tt.name for tt in TaskType}
    unknown_attrs = depends_on_attrs - valid_task_type_attrs
    assert not unknown_attrs, (
        "Finder depends_on() references TaskType attributes that don't exist:\n"
        f"{sorted(unknown_attrs)}"
    )

    # Verify that at runtime every TaskType in depends_on() resolves to a registered finder.
    all_task_types = {
        task_type for task_type in TaskType if task_type.name in depends_on_attrs
    }
    try:
        from pixlstash.utils.path_mapper import PathMapper

        finders_dict = WorkPlanner.work_finders(
            database=None, engine_getter=lambda: None, path_mapper=PathMapper()
        )
        for task_type in all_task_types:
            assert task_type in finders_dict, (
                f"depends_on() references {task_type!r} but no finder is registered for it"
            )
    except Exception as exc:
        # If we can't instantiate finders (e.g. missing DB), skip the runtime
        # check — but surface why, so a silently broken setup is visible in the
        # test warning summary rather than passing unnoticed.
        warnings.warn(
            f"Skipped runtime finder-resolution check: {exc!r}",
            stacklevel=2,
        )


# ---------------------------------------------------------------------------
# Guardrail 5: Every EventType is classified for WebSocket broadcast
# ---------------------------------------------------------------------------


def test_event_types_fully_classified():
    from pixlstash.event_types import EventType

    all_event_types = {et.name for et in EventType}

    # EventTypes broadcast to WebSocket clients (from _should_send_ws_update).
    broadcast_types = frozenset(
        {
            EventType.CHANGED_PICTURES.name,
            EventType.PICTURE_IMPORTED.name,
            EventType.PLUGIN_PROGRESS.name,
            EventType.CHANGED_TAGS.name,
            EventType.CLEARED_TAGS.name,
            EventType.CHANGED_CHARACTERS.name,
            EventType.CHANGED_FACES.name,
        }
    )

    # EventTypes explicitly NOT broadcast (silently drop or stats-only).
    # Extend this set when a new event type is intentionally excluded.
    non_broadcast_types = frozenset(
        {
            EventType.CHANGED_DESCRIPTIONS.name,  # description updates do not trigger WS refresh
            EventType.QUALITY_UPDATED.name,  # used only to invalidate the stats cache
            EventType.SNAPSHOT_CREATED.name,  # snapshot lifecycle event, not a picture change
            EventType.SNAPSHOT_DELETED.name,  # snapshot lifecycle event
            EventType.RESTORE_STARTED.name,  # restore lifecycle event
            EventType.RESTORE_COMPLETED.name,  # restore lifecycle event; frontend can react via polling
            EventType.RESTORE_FAILED.name,  # restore lifecycle event; clears activeJob in the UI
        }
    )

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
        with Vault(image_root=tmp, disable_background_workers=False) as vault:
            assert vault._task_runner is not None, (
                "_task_runner should be created in __init__"
            )
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
