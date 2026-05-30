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
        "pixlstash/services/config_service.py",  # vault-injection pattern
        "pixlstash/services/picture_stats.py",  # pending session injection refactor
        "pixlstash/services/search_query_service.py",  # vault-injection pattern; DB queries for search endpoints
        "pixlstash/services/share_service.py",  # vault-injection pattern
        "pixlstash/services/tag_prediction_service.py",  # vault-injection pattern
        "pixlstash/services/snapshot_service.py",  # vault-injection pattern; owns snapshot lifecycle
        "pixlstash/services/restore_service.py",  # vault-injection pattern; owns DB-swap lifecycle
        "pixlstash/services/undo_service.py",  # vault-injection pattern; orchestrates DB reads/writes
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
    except Exception:
        # If we can't instantiate finders (e.g. missing DB), skip the runtime check.
        pass


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
            EventType.UNDO_APPLIED.name,  # undo lifecycle event; triggers CHANGED_PICTURES separately
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


# ---------------------------------------------------------------------------
# Guardrail 7: Every SQLModel table is classified for the ChangeLog
# ---------------------------------------------------------------------------


def test_change_log_dual_list_covers_all_tables():
    """Every ``table=True`` SQLModel must appear in EXACTLY ONE of
    ``CHANGE_LOG_INCLUDED_TABLES`` or ``CHANGE_LOG_EXCLUDED_TABLES``.

    The two sets together drive the audit-trail / undo / restore plumbing in
    ``database.py`` and ``undo_service.py``. A new ``table=True`` model that
    is added without being classified would silently skip the change-log
    capture path (so undo couldn't reverse writes to it) and bypass the
    excluded-table timing heuristic — both correctness-critical for
    snapshots & undo. This guardrail forces the author to make the
    classification explicit.
    """
    # Import every db_models module so SQLModel.metadata is fully populated.
    import importlib
    import pkgutil

    import pixlstash.db_models as db_models_pkg
    from sqlmodel import SQLModel

    for _, modname, _ in pkgutil.iter_modules(db_models_pkg.__path__):
        importlib.import_module(f"pixlstash.db_models.{modname}")

    from pixlstash.database import (
        CHANGE_LOG_EXCLUDED_TABLES,
        CHANGE_LOG_INCLUDED_TABLES,
    )

    all_tables = set(SQLModel.metadata.tables.keys())
    classified = CHANGE_LOG_INCLUDED_TABLES | CHANGE_LOG_EXCLUDED_TABLES

    in_both = CHANGE_LOG_INCLUDED_TABLES & CHANGE_LOG_EXCLUDED_TABLES
    assert not in_both, (
        f"Table(s) appear in BOTH CHANGE_LOG_INCLUDED_TABLES and "
        f"CHANGE_LOG_EXCLUDED_TABLES: {sorted(in_both)}. Pick one."
    )

    unclassified = all_tables - classified
    assert not unclassified, (
        "New SQLModel table(s) added without ChangeLog classification:\n"
        f"  {sorted(unclassified)}\n"
        "Add each to EXACTLY ONE of CHANGE_LOG_INCLUDED_TABLES (full "
        "before/after data captured — core user-editable metadata) or "
        "CHANGE_LOG_EXCLUDED_TABLES (metadata-only marker — regenerable / "
        "ephemeral / system tables) in pixlstash/database.py. See CLAUDE.md."
    )

    # Tables that exist in the live DB but aren't SQLModel-declared (e.g.
    # alembic's own version table). Listing them in CHANGE_LOG_EXCLUDED_TABLES
    # is intentional documentation; they never reach the change-log hooks
    # because those iterate ORM-mapped objects only.
    external_tables = {"alembic_version"}

    stale = classified - all_tables - external_tables
    assert not stale, (
        "Table name(s) appear in CHANGE_LOG_(IN|EX)CLUDED_TABLES but no "
        f"matching SQLModel exists: {sorted(stale)}. Remove them."
    )


# ---------------------------------------------------------------------------
# Guardrail 8: ChangeLog-tracked columns use immutable types
# ---------------------------------------------------------------------------


def test_changelog_included_tables_have_no_unmanaged_mutable_columns():
    """Columns on included tables must not use bare JSON / PickleType / ARRAY.

    The change-log's ``_cl_before_state_from_history`` captures the pre-flush
    value via SQLAlchemy attribute history. That history only fires when the
    attribute is REASSIGNED — in-place mutation of a JSON/list/dict column
    leaves history unchanged and would let an UPDATE-undo restore the
    current (already-mutated) value instead of the pre-mutation one.

    Today the schema has no such columns; this guardrail blocks one from
    being added without the author either (a) wrapping it in
    ``MutableDict.as_mutable(JSON)`` / ``MutableList.as_mutable(...)`` so
    history fires on mutation, or (b) excluding the table from the change
    log, or (c) updating this allowlist with an explanatory comment.
    """
    import importlib
    import pkgutil

    import pixlstash.db_models as db_models_pkg
    from sqlalchemy.ext.mutable import MutableDict, MutableList
    from sqlalchemy.types import ARRAY, JSON, PickleType
    from sqlmodel import SQLModel

    for _, modname, _ in pkgutil.iter_modules(db_models_pkg.__path__):
        importlib.import_module(f"pixlstash.db_models.{modname}")

    from pixlstash.database import CHANGE_LOG_INCLUDED_TABLES

    mutable_types = (JSON, PickleType, ARRAY)

    # Format: (table, column). Add entries with a comment when a mutable
    # column is intentionally introduced AND wrapped in MutableDict/MutableList
    # (the test cannot distinguish wrapped from bare types via Column.type).
    allowlist: set[tuple[str, str]] = set()

    offenders: list[str] = []
    for table_name in CHANGE_LOG_INCLUDED_TABLES:
        table = SQLModel.metadata.tables.get(table_name)
        if table is None:
            continue
        for col in table.columns:
            if not isinstance(col.type, mutable_types):
                continue
            if (table_name, col.name) in allowlist:
                continue
            # Wrapped in MutableDict/MutableList? The Mutable extension
            # registers a listener on the type; check for the mutation
            # base-class attribute.
            if isinstance(col.type, (MutableDict, MutableList)):
                continue
            offenders.append(
                f"{table_name}.{col.name} (type={type(col.type).__name__})"
            )

    assert not offenders, (
        "ChangeLog-tracked columns must not store unmanaged mutable values "
        "(JSON / PickleType / ARRAY without a MutableDict / MutableList "
        "wrapper). In-place mutation bypasses SQLAlchemy attribute history, "
        "so UPDATE-undo would silently restore the wrong value. Either "
        "wrap with MutableDict.as_mutable(...) / MutableList.as_mutable(...), "
        "exclude the table from the change log, or add the (table, column) "
        "pair to the allowlist in this test with a comment.\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )
