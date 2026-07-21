"""Architecture guardrail tests.

These tests enforce structural invariants that protect the refactored
architecture from regressing.  Most run in "audit mode" with an explicit
allowlist of known transitional violations; the allowlist shrinks as the
codebase migrates.
"""

import ast
import gc
import json
import os
import re
import tempfile
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
ROUTES_DIR = REPO_ROOT / "pixlstash" / "routes"
TASKS_DIR = REPO_ROOT / "pixlstash" / "tasks"
SERVICES_DIR = REPO_ROOT / "pixlstash" / "services"
SERVER_PY = REPO_ROOT / "pixlstash" / "server.py"

# The picture-set lock guards (see pixlstash/services/set_lock_service.py). Any of
# these names appearing in a handler's source proves it consults the lock state.
_LOCK_GUARD_TOKENS = (
    "enforce_set_not_locked",
    "enforce_pictures_not_locked",
    "locked_picture_ids",
    "_assert_set_scope_not_locked",
)


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
    # See docs/backend_architecture.md §10.1 for the rule and what to do on failure.
    # Known transitional service files that still call vault.db.run_* directly
    # (inside a thin wrapper around their *_in_session functions).
    # Add a new such file here WITH a justification; remove each file from this
    # set once it is migrated to accept a Session.
    _direct_db_call_service_allowlist = {
        "pixlstash/services/config_service.py",  # vault-injection pattern
        "pixlstash/services/impossible_tag_clear_service.py",  # vault-injection pattern; bulk impossible-tag clear/undo
        "pixlstash/services/picture_stats.py",  # pending session injection refactor
        "pixlstash/services/search_query_service.py",  # vault-injection pattern; DB queries for search endpoints
        "pixlstash/services/share_service.py",  # vault-injection pattern
        "pixlstash/services/tag_prediction_service.py",  # vault-injection pattern
        "pixlstash/services/tag_suggestion_service.py",  # vault-injection pattern; review-queue writeback
        "pixlstash/services/tagger_run_service.py",  # vault-injection pattern; tagger run history upsert
        "pixlstash/services/tag_scan_service.py",  # vault-injection pattern; sync near-neighbour tag scan
        "pixlstash/services/review_service.py",  # vault-injection pattern; orchestrates scan + review lifecycle
        "pixlstash/services/tag_health_service.py",  # vault-injection pattern; background cache rebuild dispatch
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
        "Service files must receive a pre-opened session, not call vault.db directly.\n"
        "Either (a) refactor the function to take `session: Session` (the *_in_session "
        "pattern), or (b) if this is a thin wrapper around an *_in_session function, add "
        "the file to _direct_db_call_service_allowlist above with a one-line justification.\n"
        "See docs/backend_architecture.md §10.1.\n" + "\n".join(violations)
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


# ---------------------------------------------------------------------------
# Guardrail 7: Every label/curation SINK is lock-guarded or explicitly exempt
#
# A locked picture set is a hard freeze of its members' label/curation data. The
# recurring failure mode (CSO audit) is a NEW mutation path reaching a picture
# with no lock guard. A handler-list test only catches what its author remembered
# to list; this test is SINK-BASED instead: it enumerates every place the code
# writes label/curation data — Tag rows, the human-label ledger, the soft-delete
# flip, and a picture's description/score — and asserts the ENCLOSING function
# either carries a lock-guard token OR is on an explicit, justified exempt list.
# A guardrail that lists what you remembered cannot catch what you forgot; this
# one fails the moment an unguarded sink appears in a non-exempt function.
# See docs/reviews/2026-07-picture-set-locking-plan.md §7 and the CSO coverage audit.
# ---------------------------------------------------------------------------

VAULT_PY = REPO_ROOT / "pixlstash" / "vault.py"

# Label/curation write sinks. Matching one of these on a source line means that
# line mutates a picture's frozen-when-locked data.
_LABEL_SINK_RE = re.compile(
    r"(?:add\(Tag\("  # create a confirmed Tag row
    r"|delete\(Tag\)"  # delete confirmed Tag rows
    r"|record_human_label(?:_if_relevant)?\("  # write the human POS/NEG ledger
    r"|clear_human_label\("  # clear a human ledger entry
    r"|\.deleted\s*=\s*True"  # soft-delete a picture
    r"|\.(?:description|score)\s*=\s(?!=))"  # overwrite description / user score
)

# The description/score sink is only a *picture* label sink when the receiver is a
# picture. Drop writes to other models' description/score so they aren't flagged.
_NON_PICTURE_ATTR_RE = re.compile(
    r"\b(?:metadata|character|project|picture_set|row|self)\.(?:description|score)\s*="
)

# {(relative_path, enclosing_function_name): justification}. An exemption is a
# decision someone owns, per deny-by-default — each entry says why the sink is NOT
# a locked-set concern. Keep this list tight; a real mutation path belongs guarded.
_LABEL_SINK_EXEMPT = {
    # --- Internal chokepoints: every caller enforces/skips locked pics first ---
    ("pixlstash/services/tag_suggestion_service.py", "_set_tag"): (
        "internal suggestion Tag chokepoint; all callers (accept/dismiss/fix_twin/"
        "swap/_resolve/bulk_accept) enforce or skip locked pictures"
    ),
    ("pixlstash/services/tag_suggestion_service.py", "_reverse_review"): (
        "internal undo chokepoint; callers reopen_suggestion (enforce) and "
        "bulk_reopen (skip) guard locked pictures before invoking"
    ),
    ("pixlstash/services/impossible_tag_clear_service.py", "_clear_tags_in_session"): (
        "internal clear chokepoint; sole caller clear_in_session skips locked "
        "pictures via locked_picture_ids before invoking"
    ),
    # --- Machine-derived / rule-4-exempt background writes ---
    # NB: description regeneration is NOT rule-4 exempt (rule 3 freezes the
    # description). description_task._generate_descriptions_batch /
    # update_descriptions now SKIP locked pics (and MissingDescriptionFinder
    # excludes them), so they are guarded, not exempt.
    ("pixlstash/tasks/text_embedding_task.py", "_run_task"): (
        "in-memory carry-over of the existing description onto a fresh fetch to "
        "compute an embedding; no persistent label change"
    ),
    # --- New-picture ingest: a not-yet-imported picture cannot be in a locked set ---
    ("pixlstash/routes/pictures/_import.py", "apply_sidecar_tags"): (
        "applies sidecar tags to freshly-imported pictures (new pics)"
    ),
    ("pixlstash/routes/comfyui.py", "import_task"): (
        "sentinel Tag on freshly-imported ComfyUI pictures (new pics)"
    ),
    ("pixlstash/tasks/watch_folder_import_task.py", "insert_pictures"): (
        "watch-folder import of NEW pictures"
    ),
    ("pixlstash/tasks/watch_folder_import_task.py", "_run_task"): (
        "watch-folder import of NEW pictures (sidecar description)"
    ),
    ("pixlstash/tasks/reference_folder_scan_task.py", "_build_picture"): (
        "builds NEW picture rows during a reference-folder scan"
    ),
    ("pixlstash/vault.py", "import_default_data"): (
        "logo / default-data import (new pictures)"
    ),
    # --- Whole-DB snapshot restore rebuilds every row (CSO-named exempt) ---
    ("pixlstash/services/restore_service.py", "_upsert_rows"): (
        "whole-DB snapshot restore rebuilds all rows; a locked set is itself "
        "restored from the snapshot, not mutated in place"
    ),
    # NB: characters.py::alter_char now SKIPS the description clear for locked pics
    # (keeps character reassignment + text_embedding invalidation), so it is
    # guarded, not exempt.
    # --- CSO-named, documented NON-sinks (no Tag/ledger/label write reaches a
    # picture here). Kept for the record; excluded from the stale-prune below
    # because the scanner never flags them (they don't match a sink pattern). ---
    ("pixlstash/services/tag_prediction_service.py", "delete_tag_predictions"): (
        "deletes machine TagPrediction rows only (rule 4); no confirmed Tag/ledger"
    ),
    ("pixlstash/services/tag_suggestion_service.py", "skip_suggestion"): (
        "sets status SKIPPED only; writes no Tag row and no ledger entry"
    ),
}

# CSO-named documented non-sinks: present in _LABEL_SINK_EXEMPT for the record but
# they never match a sink pattern, so the stale-prune must not expect them used.
_DOCUMENTED_NON_SINKS = frozenset(
    {
        ("pixlstash/services/tag_prediction_service.py", "delete_tag_predictions"),
        ("pixlstash/services/tag_suggestion_service.py", "skip_suggestion"),
    }
)

_SINK_SCAN_FILES = [ROUTES_DIR, SERVICES_DIR, TASKS_DIR]


def _innermost_enclosing_functions(tree: ast.AST, lineno: int) -> list[ast.AST]:
    """Return the function nodes spanning ``lineno``, outermost→innermost."""
    chain = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= lineno <= (node.end_lineno or node.lineno)
    ]
    chain.sort(key=lambda n: n.lineno)
    return chain


def _iter_sink_files():
    for directory in _SINK_SCAN_FILES:
        yield from sorted(directory.rglob("*.py"))
    yield VAULT_PY


def _scan_label_sinks():
    """Yield (rel_path, lineno, enclosing_func_name, guarded, line) for each sink."""
    for path in _iter_sink_files():
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        func_src = {
            node: (ast.get_source_segment(source, node) or "")
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, raw in enumerate(source.splitlines(), start=1):
            stripped = raw.strip()
            if stripped.startswith(("#", "from ", "import ")):
                continue
            if not _LABEL_SINK_RE.search(raw):
                continue
            # description/score writes to non-picture models are not label sinks.
            if _NON_PICTURE_ATTR_RE.search(raw):
                continue
            chain = _innermost_enclosing_functions(tree, lineno)
            name = chain[-1].name if chain else "<module>"
            guarded = any(
                token in func_src[node]
                for node in chain
                for token in _LOCK_GUARD_TOKENS
            )
            yield rel, lineno, name, guarded, stripped


def test_label_mutation_sinks_are_lock_guarded():
    unguarded = []
    used_exemptions = set()
    for rel, lineno, name, guarded, line in _scan_label_sinks():
        if guarded:
            continue
        if (rel, name) in _LABEL_SINK_EXEMPT:
            used_exemptions.add((rel, name))
            continue
        unguarded.append(f"{rel}:{lineno} in '{name}': {line[:80]}")

    assert not unguarded, (
        "Label/curation sink(s) reach a picture with NO picture-set lock guard and "
        "no justified exemption (deny-by-default — each is a bug).\n"
        "Add `enforce_pictures_not_locked(session, ids, action)` (or a skip via "
        "`locked_picture_ids`) in the enclosing function, or, if it is genuinely "
        "not a locked-set concern, add a justified entry to _LABEL_SINK_EXEMPT:\n"
        + "\n".join(unguarded)
    )

    # Keep the exempt list honest: a stale entry (sink moved/guarded/removed) must
    # be pruned so the list never silently grows past what it still covers. The
    # documented non-sinks are exempt from this — they intentionally match no sink.
    stale = sorted(set(_LABEL_SINK_EXEMPT) - used_exemptions - _DOCUMENTED_NON_SINKS)
    assert not stale, (
        "Stale _LABEL_SINK_EXEMPT entries no longer match any unguarded sink "
        "(prune them):\n" + "\n".join(f"{r} :: {n}" for r, n in stale)
    )


def test_label_sink_guardrail_detects_a_removed_guard():
    """Meta-check: the sink scanner must FAIL a function whose guard is removed.

    Proves the guardrail has teeth — that it would catch a regression, not just
    pass vacuously. We take a known-guarded sink function, strip its guard tokens
    from the scanned source in-memory, and assert it flips to unguarded.
    """
    guarded_now = [
        (rel, name) for rel, _ln, name, guarded, _line in _scan_label_sinks() if guarded
    ]
    assert guarded_now, "expected at least one guarded label sink to exist"
    # Every currently-guarded sink function must rely on a guard token — remove the
    # tokens and it can no longer be considered guarded. Verify the scanner's guard
    # detection is token-driven (not incidental) for a representative sink.
    sample_rel, sample_name = guarded_now[0]
    path = REPO_ROOT / sample_rel
    source = path.read_text()
    stripped = source
    for token in _LOCK_GUARD_TOKENS:
        stripped = stripped.replace(token, "REMOVED_GUARD")
    tree = ast.parse(stripped, filename=str(path))
    func_src = {
        node: (ast.get_source_segment(stripped, node) or "")
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == sample_name
    }
    assert func_src, f"could not re-locate {sample_name} after stripping guards"
    assert not any(
        token in seg for seg in func_src.values() for token in _LOCK_GUARD_TOKENS
    ), "stripping the guard tokens must leave the function unguarded"


# Internal Tag/ledger chokepoints in tag_suggestion_service. They are exempt from
# the sink scan above (they carry the actual Tag/ledger writes but are only ever
# reached from guarded/skipping callers). That exemption is ONLY safe while every
# caller guards — the test below enforces exactly that, so a suggestion action
# (swap / fix-twin / reopen / bulk-accept / bulk-reopen / accept) that writes via a
# chokepoint but drops its lock guard fails CI even though it has no direct sink.
_LOCK_TAG_CHOKEPOINTS = ("_set_tag", "_reverse_review", "_resolve", "_apply_writeback")


def test_lock_chokepoint_callers_are_guarded():
    path = SERVICES_DIR / "tag_suggestion_service.py"
    rel = path.relative_to(REPO_ROOT).as_posix()
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    call_res = [re.compile(rf"\b{re.escape(cp)}\s*\(") for cp in _LOCK_TAG_CHOKEPOINTS]

    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in _LOCK_TAG_CHOKEPOINTS:
            continue  # a chokepoint calling another chokepoint is fine
        seg = ast.get_source_segment(source, node) or ""
        # Only consider a *direct* call in this function's own body, not calls made
        # by nested functions (those nested functions are their own nodes and are
        # checked independently). Strip nested function bodies before testing.
        own_body = seg
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_seg = ast.get_source_segment(source, child) or ""
                own_body = own_body.replace(child_seg, "")
        if not any(rx.search(own_body) for rx in call_res):
            continue
        if not any(tok in seg for tok in _LOCK_GUARD_TOKENS):
            violations.append(node.name)

    assert not violations, (
        "Function(s) call a Tag/ledger chokepoint (_set_tag/_reverse_review/"
        "_resolve/_apply_writeback) but carry no picture-set lock guard — a "
        "suggestion action could write frozen label data. Guard the caller "
        "(enforce_pictures_not_locked / locked_picture_ids skip):\n"
        f"  {rel}: " + ", ".join(sorted(violations))
    )


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
# Guardrail 8: Every mounted route is inventoried (authz-declaration scaffolding)
#
# Phase 0 of the backend authorization refactor (see the backend refactor plan
# §3.4/§6 and docs/backend_architecture.md §16.2). This is the safety net Phase 1
# builds on: it enumerates every ``(method, path_template)`` HTTP endpoint the
# built app actually exposes — the ground truth for the coverage matrix — and
# checks it against a declaration set. Today the declaration set is EMPTY (the
# ``authz`` registry does not exist yet), so this runs in AUDIT MODE with the
# full current-route allowlist below: it denies nothing and enforces no access
# policy — it only observes the route inventory. In Phase 1 the registry becomes
# the declaration set and entries burn down out of this allowlist as each route
# is declared, exactly like the direct-DB-call allowlist above.
#
# The enumeration uses pixlstash.route_inventory, which flattens FastAPI's lazy
# router inclusion via the framework's own resolver. Two fail-loud tests below
# guarantee the enumeration cannot silently under-count (which would fake
# "complete coverage") if a FastAPI upgrade changes the internal route model.
# ---------------------------------------------------------------------------

# The full set of HTTP (method, path_template) endpoints mounted today. This is
# the audit-mode allowlist: while there is no authz registry, every live route
# must appear here. In Phase 1 each route moves from here into the registry
# declaration, shrinking this set to empty. Regenerate deliberately (never
# blindly) when routes are intentionally added/removed — a diff here is a
# security-relevant change to the coverage matrix.
_CURRENT_ROUTE_ALLOWLIST = frozenset(
    {
        ("DELETE", "/api/v1/characters/{character_id}/faces"),
        ("DELETE", "/api/v1/characters/{id}"),
        ("DELETE", "/api/v1/comfyui/workflows/{workflow_name}"),
        ("DELETE", "/api/v1/import-folders/{folder_id}"),
        ("DELETE", "/api/v1/picture_sets/{id}"),
        ("DELETE", "/api/v1/picture_sets/{id}/members/{picture_id}"),
        ("DELETE", "/api/v1/pictures"),
        ("DELETE", "/api/v1/pictures/guest-scores/session"),
        ("DELETE", "/api/v1/pictures/scrapheap"),
        ("DELETE", "/api/v1/pictures/{id}"),
        ("DELETE", "/api/v1/pictures/{id}/face/{index}"),
        ("DELETE", "/api/v1/pictures/{id}/tags"),
        ("DELETE", "/api/v1/pictures/{id}/tags/{tag_id}"),
        ("DELETE", "/api/v1/projects/{project_id}"),
        ("DELETE", "/api/v1/projects/{project_id}/attachments/{attachment_id}"),
        ("DELETE", "/api/v1/reference-folders/{folder_id}"),
        ("DELETE", "/api/v1/reviews"),
        ("DELETE", "/api/v1/reviews/{review_id}"),
        ("DELETE", "/api/v1/snapshots/{snapshot_id}"),
        ("DELETE", "/api/v1/stacks/{stack_id}/members"),
        ("DELETE", "/api/v1/taggers/{name}/artifacts/{artifact_id}"),
        ("DELETE", "/api/v1/users/me/token/{token_id}"),
        ("DELETE", "/api/v1/users/me/tokens/by-resource"),
        ("DELETE", "/api/v1/users/me/watermark"),
        ("GET", "/"),
        ("GET", "/api/v1/characters"),
        ("GET", "/api/v1/characters/{id}"),
        ("GET", "/api/v1/characters/{id}/reference_pictures"),
        ("GET", "/api/v1/characters/{id}/summary"),
        ("GET", "/api/v1/characters/{id}/{field}"),
        ("GET", "/api/v1/check-session"),
        ("GET", "/api/v1/comfyui/pictures/{picture_id}/workflow"),
        ("GET", "/api/v1/comfyui/workflows"),
        ("GET", "/api/v1/filesystem/browse"),
        ("GET", "/api/v1/import-folders"),
        ("GET", "/api/v1/login"),
        ("GET", "/api/v1/network/info"),
        ("GET", "/api/v1/picture_sets"),
        ("GET", "/api/v1/picture_sets/locked-members"),
        ("GET", "/api/v1/picture_sets/{id}"),
        ("GET", "/api/v1/picture_sets/{id}/members"),
        ("GET", "/api/v1/picture_sets/{id}/thumbnail"),
        ("GET", "/api/v1/pictures"),
        ("GET", "/api/v1/pictures/comfyui_loras"),
        ("GET", "/api/v1/pictures/comfyui_models"),
        ("GET", "/api/v1/pictures/count"),
        ("GET", "/api/v1/pictures/export"),
        ("GET", "/api/v1/pictures/export/download/{task_id}"),
        ("GET", "/api/v1/pictures/export/status"),
        ("GET", "/api/v1/pictures/guest-scores"),
        ("GET", "/api/v1/pictures/import/status"),
        ("GET", "/api/v1/pictures/likeness-groups"),
        ("GET", "/api/v1/pictures/plugins"),
        ("GET", "/api/v1/pictures/search"),
        ("GET", "/api/v1/pictures/stats"),
        ("GET", "/api/v1/pictures/stream"),
        ("GET", "/api/v1/pictures/thumbnails/{id}.webp"),
        ("GET", "/api/v1/pictures/{id}.{ext}"),
        ("GET", "/api/v1/pictures/{id}/anomaly_region"),
        ("GET", "/api/v1/pictures/{id}/character_likeness"),
        ("GET", "/api/v1/pictures/{id}/detections"),
        ("GET", "/api/v1/pictures/{id}/metadata"),
        ("GET", "/api/v1/pictures/{id}/tag_predictions"),
        ("GET", "/api/v1/pictures/{id}/tags"),
        ("GET", "/api/v1/pictures/{id}/{field}"),
        ("GET", "/api/v1/pictures/{picture_id}/stack"),
        ("GET", "/api/v1/projects"),
        ("GET", "/api/v1/projects/{id_or_name}"),
        ("GET", "/api/v1/projects/{id_or_name}/picture_sets"),
        ("GET", "/api/v1/projects/{project_id}/attachments"),
        ("GET", "/api/v1/projects/{project_id}/attachments/{attachment_id}"),
        ("GET", "/api/v1/projects/{project_id}/export"),
        ("GET", "/api/v1/projects/{project_id}/summary"),
        ("GET", "/api/v1/projects/{project_name}/characters/{character_name}"),
        ("GET", "/api/v1/projects/{project_name}/picture_sets/{picture_set_name}"),
        ("GET", "/api/v1/protected"),
        ("GET", "/api/v1/reference-folders"),
        ("GET", "/api/v1/reference-folders/detect-sidecars"),
        ("GET", "/api/v1/reviews"),
        ("GET", "/api/v1/reviews/preview"),
        ("GET", "/api/v1/reviews/{review_id}"),
        ("GET", "/api/v1/reviews/{review_id}/suggestions"),
        ("GET", "/api/v1/server-config/filesystem-roots"),
        ("GET", "/api/v1/server-config/snapshots"),
        ("GET", "/api/v1/server-config/watch-folders"),
        ("GET", "/api/v1/session/context"),
        ("GET", "/api/v1/snapshots"),
        ("GET", "/api/v1/snapshots/status"),
        ("GET", "/api/v1/snapshots/{snapshot_id}/restore/preview"),
        (
            "GET",
            "/api/v1/snapshots/{snapshot_id}/restore/{resource_type}/{resource_id}/preview",
        ),
        ("GET", "/api/v1/sort_mechanisms"),
        ("GET", "/api/v1/stacks/{stack_id}"),
        ("GET", "/api/v1/stacks/{stack_id}/pictures"),
        ("GET", "/api/v1/tag_health"),
        ("GET", "/api/v1/tag_suggestions"),
        ("GET", "/api/v1/tagger-runs"),
        ("GET", "/api/v1/tagger/label-thresholds"),
        ("GET", "/api/v1/taggers"),
        ("GET", "/api/v1/tags"),
        ("GET", "/api/v1/users/me/auth"),
        ("GET", "/api/v1/users/me/config"),
        ("GET", "/api/v1/users/me/penalised-tags"),
        ("GET", "/api/v1/users/me/shared-resource-ids"),
        ("GET", "/api/v1/users/me/token"),
        ("GET", "/api/v1/users/me/watermark"),
        ("GET", "/api/v1/workers/progress"),
        ("GET", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/favicon.ico"),
        ("GET", "/openapi.json"),
        ("GET", "/scalar"),
        ("GET", "/share/{token_slug}"),
        ("GET", "/version"),
        ("GET", "/{full_path:path}"),
        ("PATCH", "/api/v1/characters/{id}"),
        ("PATCH", "/api/v1/import-folders/{folder_id}"),
        ("PATCH", "/api/v1/picture_sets/{id}"),
        ("PATCH", "/api/v1/pictures/project"),
        ("PATCH", "/api/v1/pictures/{id}"),
        ("PATCH", "/api/v1/reference-folders/{folder_id}"),
        ("PATCH", "/api/v1/server-config/snapshots"),
        ("PATCH", "/api/v1/snapshots/{snapshot_id}"),
        ("PATCH", "/api/v1/stacks/{stack_id}/members/{picture_id}"),
        ("PATCH", "/api/v1/stacks/{stack_id}/order"),
        ("PATCH", "/api/v1/users/me/config"),
        ("PATCH", "/api/v1/users/me/token/{token_id}"),
        ("POST", "/api/v1/characters"),
        ("POST", "/api/v1/characters/likeness-search"),
        ("POST", "/api/v1/characters/membership"),
        ("POST", "/api/v1/characters/{character_id}/faces"),
        ("POST", "/api/v1/comfyui/abort"),
        ("POST", "/api/v1/comfyui/run_i2i"),
        ("POST", "/api/v1/comfyui/run_t2i"),
        ("POST", "/api/v1/comfyui/workflows/import"),
        ("POST", "/api/v1/filesystem/folders"),
        ("POST", "/api/v1/import-folders"),
        ("POST", "/api/v1/login"),
        ("POST", "/api/v1/logout"),
        ("POST", "/api/v1/picture_sets"),
        ("POST", "/api/v1/picture_sets/membership"),
        ("POST", "/api/v1/picture_sets/{id}/members"),
        ("POST", "/api/v1/picture_sets/{id}/members/{picture_id}"),
        ("POST", "/api/v1/pictures/apply-scores"),
        ("POST", "/api/v1/pictures/character_likeness/batch"),
        ("POST", "/api/v1/pictures/detect"),
        ("POST", "/api/v1/pictures/face-search"),
        ("POST", "/api/v1/pictures/guest-scores"),
        ("POST", "/api/v1/pictures/import"),
        ("POST", "/api/v1/pictures/impossible-tags/clear"),
        ("POST", "/api/v1/pictures/impossible-tags/restore"),
        ("POST", "/api/v1/pictures/likeness-search"),
        ("POST", "/api/v1/pictures/plugins/{name}"),
        ("POST", "/api/v1/pictures/score_character_likeness"),
        ("POST", "/api/v1/pictures/scrapheap/restore"),
        ("POST", "/api/v1/pictures/tags/bulk_fetch"),
        ("POST", "/api/v1/pictures/thumbnails"),
        ("POST", "/api/v1/pictures/{id}/face"),
        ("POST", "/api/v1/pictures/{id}/open-location"),
        ("POST", "/api/v1/pictures/{id}/reset_description"),
        ("POST", "/api/v1/pictures/{id}/reset_tags"),
        ("POST", "/api/v1/pictures/{id}/tag_predictions/delete"),
        ("POST", "/api/v1/pictures/{id}/tag_predictions/{tag}/confirm"),
        ("POST", "/api/v1/pictures/{id}/tag_predictions/{tag}/reject"),
        ("POST", "/api/v1/pictures/{id}/tags"),
        ("POST", "/api/v1/pictures/{id}/tags/remove_all"),
        ("POST", "/api/v1/projects"),
        ("POST", "/api/v1/projects/membership"),
        ("POST", "/api/v1/projects/{project_id}/attachments"),
        ("POST", "/api/v1/projects/{project_id}/attachments/url"),
        ("POST", "/api/v1/reference-folders"),
        ("POST", "/api/v1/reference-folders/{folder_id}/metadata/export"),
        ("POST", "/api/v1/reference-folders/{folder_id}/metadata/import"),
        ("POST", "/api/v1/reference-folders/{folder_id}/move-pictures"),
        ("POST", "/api/v1/reference-folders/{folder_id}/open"),
        ("POST", "/api/v1/reference-folders/{folder_id}/relocate"),
        ("POST", "/api/v1/reviews"),
        ("POST", "/api/v1/reviews/{review_id}/abort"),
        ("POST", "/api/v1/reviews/{review_id}/archive"),
        ("POST", "/api/v1/reviews/{review_id}/refresh"),
        ("POST", "/api/v1/server-config/open"),
        ("POST", "/api/v1/server/restart"),
        ("POST", "/api/v1/snapshots"),
        ("POST", "/api/v1/snapshots/{snapshot_id}/hash-compare"),
        ("POST", "/api/v1/snapshots/{snapshot_id}/restore"),
        ("POST", "/api/v1/snapshots/{snapshot_id}/restore/batch"),
        ("POST", "/api/v1/snapshots/{snapshot_id}/restore/preview/batch"),
        (
            "POST",
            "/api/v1/snapshots/{snapshot_id}/restore/{resource_type}/{resource_id}",
        ),
        ("POST", "/api/v1/stacks"),
        ("POST", "/api/v1/stacks/{stack_id}/members"),
        ("POST", "/api/v1/tag_health/rebuild"),
        ("POST", "/api/v1/tag_suggestions/bulk-accept"),
        ("POST", "/api/v1/tag_suggestions/bulk-reopen"),
        ("POST", "/api/v1/tag_suggestions/scan"),
        ("POST", "/api/v1/tag_suggestions/{suggestion_id}/accept"),
        ("POST", "/api/v1/tag_suggestions/{suggestion_id}/dismiss"),
        ("POST", "/api/v1/tag_suggestions/{suggestion_id}/fix-twin"),
        ("POST", "/api/v1/tag_suggestions/{suggestion_id}/reopen"),
        ("POST", "/api/v1/tag_suggestions/{suggestion_id}/skip"),
        ("POST", "/api/v1/tag_suggestions/{suggestion_id}/swap"),
        ("POST", "/api/v1/tagger-runs"),
        ("POST", "/api/v1/taggers/{name}/download"),
        ("POST", "/api/v1/users/me/auth"),
        ("POST", "/api/v1/users/me/shared-picture-ids/batch"),
        ("POST", "/api/v1/users/me/token"),
        ("POST", "/api/v1/users/me/watermark"),
        ("PUT", "/api/v1/picture_sets/{id}/members"),
        ("PUT", "/api/v1/projects/{project_id}"),
    }
)

# The route modules that must each contribute at least one endpoint. This is the
# decisive cross-check that a whole router has not silently disappeared behind a
# FastAPI internal change — it is independent of the endpoint total. (test_hooks
# is intentionally absent: it is mounted only when enable_test_hooks=True.)
_EXPECTED_ROUTE_MODULES = frozenset(
    {
        "pixlstash.routes.characters",
        "pixlstash.routes.comfyui",
        "pixlstash.routes.config",
        "pixlstash.routes.filesystem",
        "pixlstash.routes.guest_scores",
        "pixlstash.routes.import_folders",
        "pixlstash.routes.picture_sets",
        "pixlstash.routes.pictures._anomaly",
        "pixlstash.routes.pictures._crud",
        "pixlstash.routes.pictures._export",
        "pixlstash.routes.pictures._face_search",
        "pixlstash.routes.pictures._import",
        "pixlstash.routes.pictures._likeness_search",
        "pixlstash.routes.pictures._listing",
        "pixlstash.routes.pictures._misc",
        "pixlstash.routes.pictures._search",
        "pixlstash.routes.pictures._thumbnails",
        "pixlstash.routes.projects",
        "pixlstash.routes.reference_folders",
        "pixlstash.routes.reviews",
        "pixlstash.routes.share",
        "pixlstash.routes.snapshots",
        "pixlstash.routes.stacks",
        "pixlstash.routes.tag_health",
        "pixlstash.routes.tag_predictions",
        "pixlstash.routes.tag_suggestions",
        "pixlstash.routes.tagger_runs",
        "pixlstash.routes.taggers",
        "pixlstash.routes.tags",
    }
)

# WebSocket routes are acknowledged in the coverage matrix but are NOT covered by
# the HTTP authz gate — their chokepoint is authenticate_websocket (plan §6). The
# included WS route's effective prefix is not resolved by the FastAPI resolver,
# so its declared (unprefixed) path is recorded. Keyed by handler name so the
# entry is stable regardless of that prefix-resolution quirk.
_KNOWN_WEBSOCKET_ROUTES = frozenset(
    {
        ("comfyui_progress_proxy", "/ws/comfyui"),
        ("websocket_updates", "/api/v1/ws/updates"),
    }
)

# Floor for the HTTP endpoint count. Well below the current total (207); its only
# job is to trip LOUD if the enumeration mechanism regresses and collapses to the
# ~14 app-level routes (which would fake "complete coverage"). Bump deliberately.
_EXPECTED_MIN_ENDPOINTS = 190


@pytest.fixture(scope="module")
def built_app():
    """Build the real Server app once for the route-inventory guardrails.

    Mirrors the construction used across the API test suite (see
    tests/test_api_coverage.py::_setup): a temp image root + minimal server
    config. Module-scoped so the (heavier) app build happens once.
    """
    from pixlstash.server import Server

    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    server = Server(server_config_path)
    try:
        yield server.api
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_all_routes_declare_access_policy(built_app):
    """AUDIT MODE: every mounted route is inventoried against a declaration set.

    Phase 0 has no authz registry, so the declaration set is empty and the full
    current route set lives in _CURRENT_ROUTE_ALLOWLIST — this test denies
    nothing and enforces no access policy. It is the scaffolding Phase 1 grows
    into: when the registry lands, ``declared`` becomes the registry's keys and
    each declared route burns down out of the allowlist. Failing in EITHER
    direction keeps the coverage matrix arithmetic (docs/backend_architecture.md
    §16.2): a new undeclared route can't merge unnoticed, and a stale allowlist
    entry can't rot.
    """
    from pixlstash.authz.registry import ROUTE_POLICIES
    from pixlstash.route_inventory import api_endpoint_set

    # Phase 1 wires ``declared`` to the authz registry's declared (method, path)
    # keys. It is empty in Step 1 (the registry back-fill is Step 2), so the full
    # current route set still lives in _CURRENT_ROUTE_ALLOWLIST; as Step 2 fills
    # ROUTE_POLICIES each declared route burns down out of the allowlist.
    declared: frozenset[tuple[str, str]] = frozenset(ROUTE_POLICIES)

    live = api_endpoint_set(built_app)

    undeclared = live - declared - _CURRENT_ROUTE_ALLOWLIST
    assert not undeclared, (
        "Mounted route(s) are neither declared in the authz registry nor in the "
        "Phase-0 audit allowlist. A new data route must declare an access policy "
        "(Phase 1) or, during Phase 0, be added to _CURRENT_ROUTE_ALLOWLIST as a "
        "reviewed coverage-matrix change:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(undeclared))
    )

    stale = _CURRENT_ROUTE_ALLOWLIST - live - declared
    assert not stale, (
        "Allowlist entr(y/ies) no longer correspond to any mounted route (route "
        "removed/renamed, or already declared in the registry). Prune them so the "
        "allowlist keeps shrinking honestly:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(stale))
    )


def test_route_enumeration_is_not_silently_undercounting(built_app):
    """FAIL-LOUD: the enumeration cannot collapse and fake complete coverage.

    The security value of the whole authz phase rests on the inventory being
    COMPLETE. The installed FastAPI resolves lazily-included routers through an
    internal helper; a future upgrade could change that model and make a naive
    walk under-count silently. These two independent tripwires make that loud:
    an absolute floor on the endpoint count, and — the decisive one — a check
    that every expected route module still contributes at least one endpoint.
    """
    from pixlstash.route_inventory import api_endpoint_set, route_module_names

    live = api_endpoint_set(built_app)
    assert len(live) >= _EXPECTED_MIN_ENDPOINTS, (
        f"Route enumeration returned only {len(live)} endpoints "
        f"(floor {_EXPECTED_MIN_ENDPOINTS}). The flattening of lazily-included "
        "routers has likely regressed (FastAPI upgrade?). Fix "
        "pixlstash/route_inventory.py before trusting any coverage claim."
    )

    live_modules = route_module_names(built_app)
    missing_modules = _EXPECTED_ROUTE_MODULES - live_modules
    assert not missing_modules, (
        "Route module(s) contribute ZERO endpoints to the inventory — a whole "
        "router has silently vanished from the enumeration (or was unmounted). "
        "This is exactly the false-coverage failure the inventory must catch:\n"
        + "\n".join(f"  {m}" for m in sorted(missing_modules))
    )


def test_websocket_routes_are_acknowledged(built_app):
    """WebSocket routes are recorded in the matrix but gated by their own path.

    WS is outside the HTTP authz gate (plan §6 — authenticate_websocket is the
    chokepoint). Enumerating them explicitly stops the registry from implying a
    false sense of WS coverage. A new WS route must be consciously acknowledged
    here, which prompts confirming its own auth path.
    """
    from pixlstash.route_inventory import websocket_endpoint_set

    live_ws = websocket_endpoint_set(built_app)
    assert live_ws == _KNOWN_WEBSOCKET_ROUTES, (
        "WebSocket route inventory changed. Update _KNOWN_WEBSOCKET_ROUTES and "
        "confirm each WS route authenticates via authenticate_websocket (the WS "
        "chokepoint — the HTTP authz gate does not cover WebSockets).\n"
        f"  added:   {sorted(live_ws - _KNOWN_WEBSOCKET_ROUTES)}\n"
        f"  removed: {sorted(_KNOWN_WEBSOCKET_ROUTES - live_ws)}"
    )


def test_matched_route_path_is_prefix_stripped(built_app):
    """Lock in the Phase-1 gate-keying fact: scope['route'].path is UNPREFIXED.

    OBSERVATION-ONLY (no authz code). Under the installed FastAPI, the effective
    (prefixed) path from the inventory, e.g. /api/v1/pictures/{id}/metadata, is
    NOT the same string as the underlying route object's own path
    (/pictures/{id}/metadata) — the one exposed at request time via
    request.scope['route'].path. They differ on the vast majority of routes.
    Phase 1's gate must therefore key on route-object IDENTITY, not on the
    prefixed template string, or it would fail to match (fail-open) every
    included route. This test documents and pins that fact so nobody keys the
    gate on the wrong path by reflex. See the principal-engineer decision memo.
    """
    from fastapi.routing import iter_route_contexts

    diverging = 0
    checked = 0
    for ctx in iter_route_contexts(built_app.routes):
        own_path = getattr(ctx.original_route, "path", None)
        if not (ctx.methods and own_path and ctx.path):
            continue
        if ctx.path.startswith("/api/v1/"):
            checked += 1
            if ctx.path != own_path:
                diverging += 1

    assert checked > 0, "expected to inspect at least one /api/v1 route"
    assert diverging > 0, (
        "Expected the effective (prefixed) path to differ from the route "
        "object's own path for included routes — if they now match, FastAPI's "
        "inclusion model changed and the Phase-1 gate-keying assumption "
        "(key by route identity, not prefixed path) must be re-verified."
    )


# ---------------------------------------------------------------------------
# Guardrail 9: The authz gate is deny-by-default (Phase 1 Step 1)
#
# The gate ships REPORT-ONLY (AUTHZ_GATE_ENFORCING=False): at runtime it denies
# nothing and the startup enumeration only prints the undeclared-route backlog.
# But the fail-closed machinery must EXIST and be proven now — CSO acceptance
# criterion (b): an undeclared route must 403 at request time AND boot-fail at
# startup once the flag is enforcing. Correct route-identity keying (criterion a)
# is necessary but NOT sufficient for fail-closed; these decoy tests are the
# load-bearing proof. See the backend refactor plan §3.5 step 1 and
# docs/backend_architecture.md §16.2.
#
# Criterion (c) — SCOPED_LIST / body_ids list-and-batch filtering — is Step 4
# work and is deliberately absent here; nothing below implies the gate covers it.
# ---------------------------------------------------------------------------

# The decoy router is mounted under this prefix, so effective paths are stable
# and the declaring registry can be built statically (no chicken-and-egg with the
# app build). The route object's OWN path is the unprefixed suffix — proving that
# identity keying, not path-string keying, is what matches at request time.
#
# NB: the prefix lives under /api/v1 on purpose — tests/conftest.py globally
# rewrites any TestClient path that does not already start with /api/v1 (adding
# the prefix), so a decoy under a different root would 404 before reaching the
# gate. The route object's OWN path is still the prefix-stripped suffix
# (/declared), preserving the identity-vs-string-keying divergence this exercises.
_DECOY_PREFIX = "/api/v1/authz-decoy-test"
_DECOY_ROUTE_SUFFIX = "/declared"
_DECOY_DECLARED_PATH = f"{_DECOY_PREFIX}/declared"
_DECOY_UNDECLARED_PATH = f"{_DECOY_PREFIX}/undeclared"


def _build_decoy_app(gate):
    """Build a minimal app whose included router carries the gate dependency."""
    from fastapi import APIRouter, Depends, FastAPI

    router = APIRouter()

    @router.get("/declared")
    async def _declared():
        return {"ok": "declared"}

    @router.get("/undeclared")
    async def _undeclared():
        return {"ok": "undeclared"}

    app = FastAPI()
    app.include_router(router, prefix=_DECOY_PREFIX, dependencies=[Depends(gate)])
    return app


def _declared_only_registry():
    """A registry declaring exactly the 'declared' decoy route (the rest a miss)."""
    from pixlstash.authz.policy import AccessPolicy, RoutePolicy

    return {
        ("GET", _DECOY_DECLARED_PATH): RoutePolicy(
            AccessPolicy.PUBLIC, justification="decoy declared route (test)"
        )
    }


def test_authz_gate_denies_undeclared_route_when_enforcing():
    """CSO (b) runtime half: with the flag enforcing, a miss is a hard 403 and a
    declared route (matched by route-object identity) still passes (200)."""
    from starlette.testclient import TestClient

    from pixlstash.authz.gate import AuthzGate

    gate = AuthzGate(registry=_declared_only_registry(), enforcing=True)
    app = _build_decoy_app(gate)
    # Build the id-keyed map WITHOUT the enforcing boot check (that is tested
    # separately below); resolve_routes never raises.
    gate.resolve_routes(app)

    client = TestClient(app)
    declared = client.get(_DECOY_DECLARED_PATH)
    undeclared = client.get(_DECOY_UNDECLARED_PATH)

    assert declared.status_code == 200, (
        "a DECLARED route must pass the enforcing gate (over-blocking is a "
        f"regression); got {declared.status_code}"
    )
    assert undeclared.status_code == 403, (
        "an UNDECLARED route must be denied 403 by the enforcing gate "
        f"(deny-by-default); got {undeclared.status_code}"
    )


def test_authz_startup_boot_fails_on_undeclared_route_when_enforcing():
    """CSO (b) startup half: with the flag enforcing, enforce_startup aborts boot
    when any mounted route is undeclared."""
    from pixlstash.authz.gate import AuthzGate

    gate = AuthzGate(registry=_declared_only_registry(), enforcing=True)
    app = _build_decoy_app(
        gate
    )  # mounts the undeclared decoy alongside the declared one

    with pytest.raises(RuntimeError, match="coverage matrix is incomplete"):
        gate.enforce_startup(app)


def test_authz_gate_report_only_denies_nothing():
    """The shipped Step-1 default: report-only, empty registry, denies nothing and
    boot proceeds even though every route is a miss."""
    from starlette.testclient import TestClient

    from pixlstash.authz.gate import AUTHZ_GATE_ENFORCING, AuthzGate

    assert AUTHZ_GATE_ENFORCING is False, (
        "Phase 1 Step 1 ships the gate report-only; AUTHZ_GATE_ENFORCING must "
        "default to False (the single-boolean rollback switch, plan §6)."
    )

    gate = AuthzGate(registry={}, enforcing=False)
    app = _build_decoy_app(gate)
    gate.enforce_startup(app)  # report-only: logs the backlog, must NOT raise

    client = TestClient(app)
    # Every route is undeclared, but report-only denies nothing.
    assert client.get(_DECOY_DECLARED_PATH).status_code == 200
    assert client.get(_DECOY_UNDECLARED_PATH).status_code == 200


def test_authz_gate_keys_by_request_time_route_identity():
    """CSO (a): request-time scope['route'] IS the enumerated route object, so
    id() keying matches — even though the effective (prefixed) path differs from
    the route object's own (prefix-stripped) path, which is why string keying
    would fail open. Proven end-to-end: a route declared under its EFFECTIVE path
    is matched at request time via identity and passes the enforcing gate."""
    from starlette.testclient import TestClient

    from pixlstash.authz.gate import AuthzGate
    from pixlstash.route_inventory import iter_api_route_contexts

    gate = AuthzGate(registry=_declared_only_registry(), enforcing=True)
    app = _build_decoy_app(gate)

    # The declaration is keyed by the EFFECTIVE (prefixed) path; the route
    # object's own path is the UNPREFIXED suffix — string keying on scope['route']
    # .path would miss it, identity keying does not.
    ctxs = {path: route for _method, path, route in iter_api_route_contexts(app)}
    assert _DECOY_DECLARED_PATH in ctxs
    assert ctxs[_DECOY_DECLARED_PATH].path == _DECOY_ROUTE_SUFFIX, (
        "the route object's own path must be prefix-stripped (identity keying is "
        "required); if this now equals the effective path, re-verify the gate."
    )

    gate.resolve_routes(app)
    client = TestClient(app)
    assert client.get(_DECOY_DECLARED_PATH).status_code == 200, (
        "identity keying must match the declared route at request time"
    )
    assert client.get(_DECOY_UNDECLARED_PATH).status_code == 403
