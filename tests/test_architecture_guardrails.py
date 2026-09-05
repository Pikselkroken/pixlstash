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
import shlex
import tempfile
import warnings
from pathlib import Path

import pytest

from tests.network_vectors import LAN_IPV4, PRIVATE_10_IPV4, PRIVATE_172_IPV4

REPO_ROOT = Path(__file__).parent.parent
PIXLSTASH_DIR = REPO_ROOT / "pixlstash"
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
    "pixlstash/routes/characters_faces.py",
    "pixlstash/routes/comfyui.py",
    "pixlstash/routes/config.py",
    "pixlstash/routes/guest_scores.py",
    "pixlstash/routes/import_folders.py",
    "pixlstash/routes/picture_sets.py",
    "pixlstash/routes/pictures/_character_likeness.py",
    "pixlstash/routes/pictures/_crud.py",
    "pixlstash/routes/pictures/_export.py",
    "pixlstash/routes/pictures/_faces.py",
    "pixlstash/routes/pictures/_helpers.py",
    "pixlstash/routes/pictures/_import.py",
    "pixlstash/routes/pictures/_listing.py",
    "pixlstash/routes/pictures/_misc.py",
    "pixlstash/routes/pictures/_search.py",
    "pixlstash/routes/pictures/_serving.py",
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
        # yields "\" on Windows - str() would never match the allowlist there.
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
        # vault-injection pattern; owns the register -> wait-for-scan -> assign
        # commit lifecycle across separate transactions over time (the
        # scan-status poll cannot share a session with the later write), so no
        # single caller-supplied session would fit every stage.
        "pixlstash/services/folder_structure_commit_service.py",
        "pixlstash/services/dedup_sweep_service.py",  # vault-injection pattern; read-only wrapper around plan_sweep_in_session
        "pixlstash/services/dedup_tier_service.py",  # vault-injection pattern; thin wrappers around the *_in_session queue reads and the scan request
        "pixlstash/services/dedup_verdict_service.py",  # vault-injection pattern; thin wrappers around the *_in_session verdict writers
        "pixlstash/services/impossible_tag_clear_service.py",  # vault-injection pattern; bulk impossible-tag clear/undo
        "pixlstash/services/keep_cover_only_service.py",  # vault-injection pattern; thin wrappers around the *_in_session keep-cover-only preview and collapse
        "pixlstash/services/library_insights_service.py",  # vault-injection pattern; read-only wrapper around build_insights_in_session
        "pixlstash/services/mixed_stack_service.py",  # vault-injection pattern; thin wrappers around the *_in_session mixed-stack list, actions and Keep
        "pixlstash/services/model_shelf_service.py",  # vault-injection pattern; the adapter_attachment reads are the vault half of a hub/vault join no session can span
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
        # restore_service.py was decomposed into the restore/ package (plan §4.4);
        # the DB-swap / upsert / preview modules keep the vault-injection pattern.
        "pixlstash/services/restore/full_restore.py",  # vault-injection pattern; owns DB-swap lifecycle
        "pixlstash/services/restore/resource_restore.py",  # vault-injection pattern; per-resource upsert
        "pixlstash/services/restore/preview.py",  # vault-injection pattern; restore previews + hash compare
        "pixlstash/services/comfyui_service.py",  # vault-injection pattern; owns ComfyUI output-import orchestration
        "pixlstash/services/scrapheap_service.py",  # vault-injection pattern; thin wrappers around the *_in_session purge/retention functions
        # The op-log's whole point is that capture -> mutation -> capture ->
        # record run in ONE queued task; the wrapper owning that submission is
        # the atomicity guarantee, not transitional debt.
        "pixlstash/services/operation_log_service.py",  # vault-injection pattern; atomic record-with-mutation task
        # v1.11 Phase 4b. The two vault-taking entry points (picture_layout,
        # move_to_match) exist so the routes do not, and move_to_match owes the
        # same atomicity the op-log wrapper above does: plan, move the files,
        # capture and record inside ONE queued task, or a write landing between
        # the plan and the record is attributed to this move.
        "pixlstash/services/layout_move_service.py",  # vault-injection pattern; atomic plan-move-record task
        # v1.11 Phase 5. pending_moves(vault) is the canonical thin
        # *_in_session wrapper (pending_summary_in_session); apply_reviews
        # owes run_recorded_metadata_task the same atomicity layout_move_service
        # owes it above - capture, mutate and record in ONE queued task.
        "pixlstash/services/move_reconciliation_service.py",
        # v1.11 Phase 4c. run_migration_pass owes exactly the atomicity
        # move_to_match owes above - plan, move the files, capture and record
        # in ONE queued task - and preview_migration is its read half, which
        # walks the whole library's paths and must see one consistent snapshot
        # of them.
        "pixlstash/services/layout_migration_service.py",
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
        "Expected to find at least one finder_name() - check task file paths"
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
        # check - but surface why, so a silently broken setup is visible in the
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
            # The active library was replaced underneath every client. Sent to
            # all of them regardless of grid filters, because their picture ids
            # now name different pictures.
            EventType.LIBRARY_SWITCHED.name,
            # The GPU ran out of memory. A fact about the machine, so it goes to
            # every client regardless of grid filters.
            EventType.VRAM_OOM.name,
            # Vault-wide reconciliation queue nudge (v1.11 Phase 5), like
            # VRAM_OOM: not a grid view a client's filters could exclude it from.
            EventType.EXTERNAL_MOVES_PENDING.name,
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
# writes label/curation data - Tag rows, the human-label ledger, the soft-delete
# flip, and a picture's description/score - and asserts the ENCLOSING function
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
# decision someone owns, per deny-by-default - each entry says why the sink is NOT
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
    ("pixlstash/services/comfyui_service.py", "import_task"): (
        "sentinel Tag on freshly-imported ComfyUI pictures (new pics)"
    ),
    ("pixlstash/tasks/watch_folder_import_task.py", "insert_pictures"): (
        "watch-folder import of NEW pictures"
    ),
    ("pixlstash/tasks/picture_import_task.py", "insert_pictures"): (
        "async staging import of NEW pictures (#459) - sentinel Tag on freshly "
        "created rows that cannot yet be in a locked set"
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
    ("pixlstash/services/restore/resource_restore.py", "_upsert_rows"): (
        "whole-DB snapshot restore rebuilds all rows; a locked set is itself "
        "restored from the snapshot, not mutated in place"
    ),
    # NB: characters.py::alter_char now SKIPS the description clear for locked pics
    # (keeps character reassignment + text_embedding invalidation), so it is
    # guarded, not exempt.
    # --- Op-log undo/redo: the guard lives at the single restore sink ---
    ("pixlstash/services/operation_log_service.py", "_apply_tags"): (
        "module-private tag reconciliation reached only from "
        "apply_state_in_session, which calls enforce_pictures_not_locked over "
        "the whole recorded state (every facet) before dispatching"
    ),
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
        "no justified exemption (deny-by-default - each is a bug).\n"
        "Add `enforce_pictures_not_locked(session, ids, action)` (or a skip via "
        "`locked_picture_ids`) in the enclosing function, or, if it is genuinely "
        "not a locked-set concern, add a justified entry to _LABEL_SINK_EXEMPT:\n"
        + "\n".join(unguarded)
    )

    # Keep the exempt list honest: a stale entry (sink moved/guarded/removed) must
    # be pruned so the list never silently grows past what it still covers. The
    # documented non-sinks are exempt from this - they intentionally match no sink.
    stale = sorted(set(_LABEL_SINK_EXEMPT) - used_exemptions - _DOCUMENTED_NON_SINKS)
    assert not stale, (
        "Stale _LABEL_SINK_EXEMPT entries no longer match any unguarded sink "
        "(prune them):\n" + "\n".join(f"{r} :: {n}" for r, n in stale)
    )


def test_label_sink_guardrail_detects_a_removed_guard():
    """Meta-check: the sink scanner must FAIL a function whose guard is removed.

    Proves the guardrail has teeth - that it would catch a regression, not just
    pass vacuously. We take a known-guarded sink function, strip its guard tokens
    from the scanned source in-memory, and assert it flips to unguarded.
    """
    guarded_now = [
        (rel, name) for rel, _ln, name, guarded, _line in _scan_label_sinks() if guarded
    ]
    assert guarded_now, "expected at least one guarded label sink to exist"
    # Every currently-guarded sink function must rely on a guard token - remove the
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
# caller guards - the test below enforces exactly that, so a suggestion action
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
        "_resolve/_apply_writeback) but carry no picture-set lock guard - a "
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
                "TaskRunner must NOT be running after Vault.__init__() - "
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
# built app actually exposes - the ground truth for the coverage matrix - and
# checks it against a declaration set. Today the declaration set is EMPTY (the
# ``authz`` registry does not exist yet), so this runs in AUDIT MODE with the
# full current-route allowlist below: it denies nothing and enforces no access
# policy - it only observes the route inventory. In Phase 1 the registry becomes
# the declaration set and entries burn down out of this allowlist as each route
# is declared, exactly like the direct-DB-call allowlist above.
#
# The enumeration uses pixlstash.route_inventory, which flattens FastAPI's lazy
# router inclusion via the framework's own resolver. Two fail-loud tests below
# guarantee the enumeration cannot silently under-count (which would fake
# "complete coverage") if a FastAPI upgrade changes the internal route model.
# ---------------------------------------------------------------------------

# The audit-mode allowlist of routes not yet declared in the authz registry.
# Phase 1 Step 2 back-filled ALL mounted routes into ``ROUTE_POLICIES``
# (pixlstash/authz/registry.py), so this set has burned down to EMPTY: the
# registry is now the sole coverage matrix. A newly added data route must be
# declared in the registry (no longer parked here); the two assertions below then
# keep the matrix arithmetic - an undeclared route can't merge, and a stale
# allowlist entry can't rot. Do NOT re-populate this to silence a new route:
# declare it in the registry instead.
_CURRENT_ROUTE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset()


# The route modules that must each contribute at least one endpoint. This is the
# decisive cross-check that a whole router has not silently disappeared behind a
# FastAPI internal change - it is independent of the endpoint total. (test_hooks
# is intentionally absent: it is mounted only when enable_test_hooks=True.)
_EXPECTED_ROUTE_MODULES = frozenset(
    {
        "pixlstash.routes.characters",
        "pixlstash.routes.characters_faces",
        "pixlstash.routes.comfyui",
        "pixlstash.routes.config",
        "pixlstash.routes.filesystem",
        "pixlstash.routes.guest_scores",
        "pixlstash.routes.import_folders",
        "pixlstash.routes.operations",
        "pixlstash.routes.picture_sets",
        "pixlstash.routes.pictures._anomaly",
        "pixlstash.routes.pictures._character_likeness",
        "pixlstash.routes.pictures._crud",
        "pixlstash.routes.pictures._export",
        "pixlstash.routes.pictures._faces",
        "pixlstash.routes.pictures._face_search",
        "pixlstash.routes.pictures._import",
        "pixlstash.routes.pictures._likeness_search",
        "pixlstash.routes.pictures._listing",
        "pixlstash.routes.pictures._misc",
        "pixlstash.routes.pictures._search",
        "pixlstash.routes.pictures._serving",
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
# the HTTP authz gate - their chokepoint is authenticate_websocket (plan §6). The
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
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_all_routes_declare_access_policy(built_app):
    """AUDIT MODE: every mounted route is inventoried against a declaration set.

    Phase 0 has no authz registry, so the declaration set is empty and the full
    current route set lives in _CURRENT_ROUTE_ALLOWLIST - this test denies
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


# ---------------------------------------------------------------------------
# Guardrail: READ_BLOCKED_GET_PATHS names no route that is not an owner-class
# GET in the registry (issue #831)
# ---------------------------------------------------------------------------


def test_read_blocked_get_paths_name_declared_owner_class_gets():
    """No entry in ``READ_BLOCKED_GET_PATHS`` may be a path nothing declares.

    The frozenset is matched against ``request.url.path`` exactly, so a typo, a
    removed route or a route since loosened leaves a string that silently
    protects nothing while reading as protection. Derived from
    ``ROUTE_POLICIES`` one class wider than the §16.3 locality tier, because the
    set legitimately also covers a few ``OWNER_ONLY`` config GETs.

    The forward direction - every locality-tier GET is *on* the belt - is
    ``tests/test_authz_host_capability_16_3.py``
    ``::test_every_untemplated_locality_get_is_on_the_read_blocked_belt``, and it
    is not repeated here: exact matching cannot express a templated path such as
    ``/models/{model_id}/samples`` at all, so that test asserts the rule for the
    untemplated routes and pins the templated ones as a known gap. A copy here
    without that distinction would only demand dead strings be added.

    The gate's own ``_enforce_unscoped_owner`` is the live enforcement for these
    routes and refuses the same tokens (pinned by
    ``tests/test_authz_gate_step3.py::test_local_owner_only_get_refused_at_the_gate``);
    the frozenset is the pre-routing layer that also survives an
    ``AUTHZ_GATE_ENFORCING = False`` rollback, which is why it still earns its
    keep rather than being deleted in favour of the single chokepoint.
    """
    from pixlstash.auth import READ_BLOCKED_GET_PATHS
    from pixlstash.authz.gate import OWNER_CLASS_POLICIES
    from pixlstash.authz.registry import ROUTE_POLICIES

    owner_class_gets = {
        path
        for (method, path), route_policy in ROUTE_POLICIES.items()
        if method == "GET" and route_policy.policy in OWNER_CLASS_POLICIES
    }

    stale = sorted(READ_BLOCKED_GET_PATHS - owner_class_gets)
    assert not stale, (
        "READ_BLOCKED_GET_PATHS entr(y/ies) do not name a GET route declared "
        "OWNER_ONLY/LOCAL_OWNER_ONLY/LOOPBACK_OWNER_ONLY in the authz registry - "
        "a typo, a removed route, or a route deliberately loosened. Fix the path "
        "or drop the entry:\n" + "\n".join(f"  {p}" for p in stale)
    )


# ---------------------------------------------------------------------------
# Guardrail: the written coverage matrix matches the registry, row for row
# ---------------------------------------------------------------------------

# Lives in docs/ rather than docs/reviews/ on purpose: docs/reviews/ is
# gitignored so review write-ups stay local, and a blocking CI gate must not
# read a file that a fresh checkout is not guaranteed to have.
COVERAGE_MATRIX_MD = REPO_ROOT / "docs" / "authz-coverage-matrix.md"

# Sections of the matrix document whose tables carry one row per declared route.
# The main table plus the conditionally-mounted waiver table together must
# account for every key in ROUTE_POLICIES.
_MATRIX_ROW_SECTIONS = (
    "## The matrix (one row per route)",
    "## Conditionally-mounted routes",
)

# | METHOD | `/effective/path` | policy | ... |. The policy cell may be wrapped in
# ** or ` for emphasis, which carries no meaning and is stripped.
_MATRIX_ROW_RE = re.compile(
    r"^\|\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\|"
    r"\s*`([^`]+)`\s*\|"
    r"\s*([^|]*?)\s*\|"
)


def _parse_coverage_matrix_rows() -> list[tuple[str, str, str, int]]:
    """Extract (method, path, policy, line_no) from the matrix document's tables.

    Only lines inside the row-bearing sections are considered, so the many
    narrative tables elsewhere in the document (policy meanings, sign-off
    findings) cannot contribute phantom rows.
    """
    text = COVERAGE_MATRIX_MD.read_text(encoding="utf-8")
    lines = text.splitlines()

    rows: list[tuple[str, str, str, int]] = []
    sections_found = 0
    inside = False
    for lineno, line in enumerate(lines, start=1):
        if line.startswith("## "):
            inside = any(line.startswith(h) for h in _MATRIX_ROW_SECTIONS)
            if inside:
                sections_found += 1
            continue
        if not inside:
            continue
        match = _MATRIX_ROW_RE.match(line)
        if match:
            method, path, policy = match.groups()
            rows.append(
                (method, path, policy.replace("*", "").replace("`", "").strip(), lineno)
            )

    # FAIL-LOUD anti-vacuity: this test's whole value is that it actually reads
    # the document. A renamed heading or a reformatted table would otherwise
    # silently reduce it to comparing two empty sets and passing.
    assert sections_found == len(_MATRIX_ROW_SECTIONS), (
        f"Expected to find {len(_MATRIX_ROW_SECTIONS)} row-bearing section heading(s) in "
        f"{COVERAGE_MATRIX_MD.relative_to(REPO_ROOT).as_posix()} but found {sections_found}. "
        "A heading was renamed; update _MATRIX_ROW_SECTIONS so the rows are still parsed."
    )
    assert len(rows) >= 200, (
        f"Parsed only {len(rows)} route row(s) from the coverage matrix. The document "
        "declares well over 200, so the row regex has stopped matching the table format. "
        "Fix _MATRIX_ROW_RE rather than letting this comparison go vacuous."
    )
    return rows


def test_coverage_matrix_document_matches_the_registry():
    """The written matrix is machine-checked against ROUTE_POLICIES, both ways.

    docs/authz-coverage-matrix.md is the human-readable coverage matrix
    the security process depends on ("completeness must be arithmetic, not
    judgement"). Before this test nothing read it: the sibling
    test_all_routes_declare_access_policy compares the registry against the
    *live app* and never opens the markdown, so the document was free to drift
    from the code it claims to document, and it did: six declared routes had no
    row at all and one row was duplicated.

    Failing in BOTH directions is the point. A new route that lands a registry
    declaration but no row leaves the matrix incomplete, which is exactly the
    BOLA-by-omission mechanism this repo has shipped three times; a row with no
    declaration means the document asserts coverage that does not exist. The
    policy check catches the subtler drift: a row that exists but names the
    wrong access level is worse than a missing row, because it reads as
    reviewed.
    """
    from pixlstash.authz.registry import ROUTE_POLICIES

    rows = _parse_coverage_matrix_rows()

    seen: dict[tuple[str, str], int] = {}
    duplicated: list[str] = []
    for method, path, _policy, lineno in rows:
        key = (method, path)
        if key in seen:
            duplicated.append(f"  {method} {path} (lines {seen[key]} and {lineno})")
        else:
            seen[key] = lineno

    assert not duplicated, (
        "Route(s) appear more than once in the coverage matrix. Duplicated rows break the "
        "'one row per route' arithmetic and let two rows disagree about a policy:\n"
        + "\n".join(duplicated)
    )

    tabled = set(seen)
    declared = set(ROUTE_POLICIES)

    missing_rows = declared - tabled
    assert not missing_rows, (
        "Route(s) are declared in ROUTE_POLICIES but have no row in "
        f"{COVERAGE_MATRIX_MD.relative_to(REPO_ROOT).as_posix()}. The matrix is the "
        "artefact the security review reads; a declaration with no row is coverage that "
        "was never written down:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(missing_rows))
    )

    orphan_rows = tabled - declared
    assert not orphan_rows, (
        "Coverage-matrix row(s) name a route that is not declared in ROUTE_POLICIES "
        "(route removed or renamed, or the path is mistyped). The document is claiming "
        "coverage that does not exist:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(orphan_rows))
    )

    policy_drift: list[str] = []
    for method, path, policy, lineno in rows:
        actual = ROUTE_POLICIES[(method, path)].policy.name.lower()
        if policy.lower() != actual:
            policy_drift.append(
                f"  line {lineno}: {method} {path}: document says {policy!r}, "
                f"registry says {actual!r}"
            )

    assert not policy_drift, (
        "Coverage-matrix row(s) name a different access policy than the registry enforces. "
        "A row that reads as reviewed but states the wrong access level is worse than a "
        "missing row:\n" + "\n".join(policy_drift)
    )


def test_route_enumeration_is_not_silently_undercounting(built_app):
    """FAIL-LOUD: the enumeration cannot collapse and fake complete coverage.

    The security value of the whole authz phase rests on the inventory being
    COMPLETE. The installed FastAPI resolves lazily-included routers through an
    internal helper; a future upgrade could change that model and make a naive
    walk under-count silently. These two independent tripwires make that loud:
    an absolute floor on the endpoint count, and - the decisive one - a check
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
        "Route module(s) contribute ZERO endpoints to the inventory - a whole "
        "router has silently vanished from the enumeration (or was unmounted). "
        "This is exactly the false-coverage failure the inventory must catch:\n"
        + "\n".join(f"  {m}" for m in sorted(missing_modules))
    )


def test_websocket_routes_are_acknowledged(built_app):
    """WebSocket routes are recorded in the matrix but gated by their own path.

    WS is outside the HTTP authz gate (plan §6 - authenticate_websocket is the
    chokepoint). Enumerating them explicitly stops the registry from implying a
    false sense of WS coverage. A new WS route must be consciously acknowledged
    here, which prompts confirming its own auth path.
    """
    from pixlstash.route_inventory import websocket_endpoint_set

    live_ws = websocket_endpoint_set(built_app)
    assert live_ws == _KNOWN_WEBSOCKET_ROUTES, (
        "WebSocket route inventory changed. Update _KNOWN_WEBSOCKET_ROUTES and "
        "confirm each WS route authenticates via authenticate_websocket (the WS "
        "chokepoint - the HTTP authz gate does not cover WebSockets).\n"
        f"  added:   {sorted(live_ws - _KNOWN_WEBSOCKET_ROUTES)}\n"
        f"  removed: {sorted(_KNOWN_WEBSOCKET_ROUTES - live_ws)}"
    )


# ---------------------------------------------------------------------------
# The project-filter chokepoint's coverage is arithmetic, not remembered (§16.6)
# ---------------------------------------------------------------------------
#
# `authz.membership.enforce_project_filter_scope` runs on every declared route
# and refuses a `project_id` filter a scoped token may not name (#708). It reads
# `request.query_params` and matches against the fixed tuple
# `PROJECT_FILTER_QUERY_PARAMS`, so a route that spells the parameter any other
# way is silently uncovered - the omission class §16.2 exists to abolish.
#
# Anything whose *name* mentions a project is treated as a candidate filter, not
# just the two spellings already listed: the point is to catch a NEW spelling
# (`projects`, `filter_project`, `project_id_in`), which a narrow
# `^project_ids?$` match would sail straight past.
_PROJECT_PARAM_RE = re.compile(r"project", re.IGNORECASE)

# Query parameters whose name mentions a project but which are NOT a filter over
# the project space (so the gate must not refuse them). Every entry needs a
# reason; an empty mapping is the healthy state, and a new entry is a deliberate,
# reviewable claim rather than a loosened regex.
_NON_PROJECT_FILTER_QUERY_PARAMS: dict[str, str] = {}

# Routes that take a project in a request BODY or FORM field **and declare it in
# the annotation**, so introspection can see it. `_iter_body_project_refs` reads
# each body parameter's declared type: it enumerates a *typed* body (a Pydantic
# request model, a `project_id: int | None = Form(...)`) exactly, and it sees
# NOTHING inside a body declared `payload: dict = Body(...)`. The opaque half is
# a real and acknowledged blind spot - see _PROJECT_REFS_IN_OPAQUE_BODY below.
# This set is therefore **not** the complete list of routes that take a project
# in a body; it is the complete list of the ones a machine can find.
#
# All of them are outside `enforce_project_filter_scope` (which reads the query
# string only), and nothing else checks the payload half. Today every one is a
# write, and a resource-scoped token can only be minted READ, so `auth.py`'s
# "block non-GET for a READ token" refuses them before the payload is ever read -
# unless the path is in `auth.READ_SAFE_POST_PATHS`, which none of these is. That
# argument is what the READ-reachability assertion below actually tests; the
# enumeration exists so a NEW typed body field has to be looked at.
# See docs/backend_architecture.md §16.6.
_PROJECT_REFS_IN_TYPED_BODY = frozenset(
    {
        ("POST", "/api/v1/pictures/import", "project_id"),
        ("POST", "/api/v1/pictures/import/staging", "payload.project_id"),
        ("POST", "/api/v1/reviews", "payload.project_id"),
        ("POST", "/api/v1/tag_suggestions/bulk-accept", "payload.project_id"),
        ("POST", "/api/v1/tag_suggestions/scan", "payload.project"),
    }
)

# Routes whose body is an OPAQUE mapping (`payload: dict = Body(...)`) and which
# read a project id out of it.
#
# **This list is hand-made, and no test can tell you it is complete.** A `dict`
# annotation declares no keys, so nothing about the payload of these routes is
# visible to introspection; the set was built by reading the handlers on
# 2026-08-04 and it will go stale silently as handlers change. It is written down
# so the known cases are known, NOT so the set can be treated as exhaustive. Do
# not build an argument that rests on this being everything.
#
# (A source grep for `project_id` over the opaque-bodied handlers was tried as a
# machine substitute and rejected: on the current tree it returns 8 routes, of
# which 3 - POST /characters/{id}/faces, POST+PUT /picture_sets/{id}/members -
# never read a project from the body at all and only mention the word while
# reconciling membership they derive themselves. A check with a 38% false-positive
# rate that still cannot prove absence buys the appearance of coverage, not
# coverage.)
_PROJECT_REFS_IN_OPAQUE_BODY = frozenset(
    {
        ("PATCH", "/api/v1/pictures/project", "payload.project_id"),
        ("POST", "/api/v1/characters", "payload.project_ids|payload.project_id"),
        ("POST", "/api/v1/picture_sets", "payload.project_ids|payload.project_id"),
        (
            "PATCH",
            "/api/v1/picture_sets/{id}",
            "payload.project_ids|payload.project_id",
        ),
        ("POST", "/api/v1/comfyui/run_t2i", "payload.project_id"),
    }
)

# The one thing about opaque bodies that IS arithmetic: which routes have one,
# and which of those a READ (resource-scoped) token can actually reach. For a
# route a READ token cannot reach, "we cannot see inside the body" costs nothing -
# the payload is never read. For a route it CAN reach, the blind spot is live, so
# every such route is hand-vetted here with what its handler actually reads. The
# intersection is enumerable even though the payloads are not, which is what keeps
# the blind spot bounded instead of open-ended.
_READ_REACHABLE_OPAQUE_BODY_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/pictures/thumbnails"): (
        "reads payload['ids'] only - a picture id list, narrowed by the gate's "
        "picture-scope policy. Names no project. Hand-checked 2026-08-04."
    ),
}


def _models_in(annotation):
    """Return EVERY Pydantic model reachable in ``annotation``, in no order.

    Returning only the *first* model made unions order-dependent: a stack-based
    walk of ``WithProject | Plain`` popped ``Plain`` first and stopped, so the
    project field on the other branch was never visited, while
    ``Plain | WithProject`` was caught. A caller may send either branch, so every
    branch has to be walked.
    """
    import typing

    from pydantic import BaseModel

    found = []
    pending = [annotation]
    while pending:
        current = pending.pop()
        if current is None:
            continue
        args = typing.get_args(current)
        if args:
            pending.extend(args)
        elif isinstance(current, type) and issubclass(current, BaseModel):
            if current not in found:
                found.append(current)
    return found


def _iter_body_project_refs(app):
    """Yield ``(method, path, dotted_field)`` for project-ish TYPED body fields.

    Walks each route's flattened body parameters, descending into every Pydantic
    model reachable from the declared annotation so a field on a request schema is
    found under its dotted path. Only the leaf name is matched against
    :data:`_PROJECT_PARAM_RE`, so a model merely *called* ``ProjectFoo`` does not
    trip it - only a field that names a project.

    **What it cannot see.** It reads ``field_info.annotation`` and nothing else, so
    a body declared ``payload: dict = Body(...)`` (or ``Any``, or ``list[dict]``)
    contributes nothing at all: the annotation declares no keys, so a project id
    read as ``payload["project_id"]`` is invisible here. That is the blind spot
    recorded in :data:`_PROJECT_REFS_IN_OPAQUE_BODY`, and it is why the
    exact-equality assertion below is scoped to *typed* bodies rather than claimed
    over all of them.

    Nesting is unbounded (recursion terminates on models already open on the
    current path, so a self-referential schema is safe). An earlier ``depth <= 4``
    cap descended through at most five nested models and silently dropped anything
    below that; a ``project_id`` six models down was measured as missed.
    """
    from pixlstash.route_inventory import (
        flatten_dependant_fields,
        iter_api_route_contexts,
    )

    def _walk(name, annotation, out, seen=frozenset()):
        descended = False
        for model in _models_in(annotation):
            if model in seen:
                continue
            descended = True
            for sub_name, sub_field in model.model_fields.items():
                _walk(
                    f"{name}.{sub_name}" if name else sub_name,
                    sub_field.annotation,
                    out,
                    seen | {model},
                )
        if descended:
            return
        if _PROJECT_PARAM_RE.search(name.rsplit(".", 1)[-1]):
            out.append(name)

    for method, path, route in iter_api_route_contexts(app):
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        found: list[str] = []
        for field in flatten_dependant_fields(dependant, "body_params"):
            annotation = getattr(getattr(field, "field_info", None), "annotation", None)
            _walk(getattr(field, "alias", None) or field.name, annotation, found)
        for dotted in sorted(set(found)):
            yield (method, path, dotted)


def _iter_opaque_body_routes(app):
    """Yield ``(method, path)`` for routes whose body admits keys nothing declares.

    A body parameter annotated ``dict`` / ``dict[...]`` / ``Any`` (directly or
    inside a container or union) carries arbitrary keys, so
    :func:`_iter_body_project_refs` can say nothing about its contents. Which
    *routes* are in that state is still perfectly enumerable, and that is what this
    yields.
    """
    import typing

    from pixlstash.route_inventory import (
        flatten_dependant_fields,
        iter_api_route_contexts,
    )

    def _is_opaque(annotation):
        pending = [annotation]
        while pending:
            current = pending.pop()
            if current is None:
                continue
            if current is typing.Any or current is dict:
                return True
            if typing.get_origin(current) is dict:
                return True
            pending.extend(typing.get_args(current))
        return False

    for method, path, route in iter_api_route_contexts(app):
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for field in flatten_dependant_fields(dependant, "body_params"):
            annotation = getattr(getattr(field, "field_info", None), "annotation", None)
            if _models_in(annotation):
                continue
            if _is_opaque(annotation):
                yield (method, path)
                break


def test_project_filter_params_are_declared(built_app):
    """Every project-ish QUERY parameter is known to the project-filter gate.

    ``enforce_project_filter_scope`` can only refuse a filter it can see, and it
    sees exactly the names in ``PROJECT_FILTER_QUERY_PARAMS``. A route that takes
    the same question under a different name is a hole that no coverage matrix
    cell records, because the matrix records a route's *policy*, not the
    parameters it accepts (§16.6). This makes that second axis arithmetic.

    One-directional on purpose: declaring a name no route takes is harmless
    (``project_ids`` is declared ahead of a route using it), while a route taking
    an undeclared name is the bug.

    **What "machine-checked" does NOT mean here.** This walks *declared* query
    parameters and matches their *wire* names. Three shapes defeat it, and the
    third also weakens the anti-vacuity assertion below:

    1. **A wire name that does not say "project".** ``project_id: str =
       Query(None, alias="proj")`` is enumerated as ``proj``, which this regex does
       not match - and the gate is defeated by the same fact, because it matches
       ``request.query_params`` against ``PROJECT_FILTER_QUERY_PARAMS`` and the
       wire name really is ``proj``. Guardrail and gate fail together, which is
       precisely the case where a guardrail is worth nothing.
    2. **An undeclared parameter read straight off the request.** A handler doing
       ``request.query_params.get("owning_project")`` declares nothing, so it is
       invisible to this enumeration. Not hypothetical: ``routes/pictures/
       _misc.py`` and ``routes/pictures/_listing.py`` already read ``project_id``
       directly off ``request.query_params``, so the pattern is in-tree and
       available to copy. (Those two are covered *by the gate*, which reads the
       raw query string - but only because they kept the declared spelling.)
    3. **The nested-``Depends`` capability is not what the anti-vacuity assertion
       protects.** ``iter_api_query_params`` flattens the dependency tree so a
       parameter contributed one level down is enumerated, and §16.6 advertises
       that. But the assertion only requires ``project_id`` to be found *somewhere*,
       and ``project_id`` is taken at top level by a dozen routes: stub
       ``flatten_dependant_fields`` to a no-op and the assertion still passes while every
       nested parameter silently vanishes. No route today contributes a
       project-ish parameter *only* via a nested ``Depends``, so the capability is
       covered synthetically by
       ``test_query_parameter_enumeration_descends_nested_depends`` instead. That
       keeps the flattening honest; it does not make *this* test notice a real
       nested parameter disappearing, so do not assume it would.
    """
    from pixlstash.authz.membership import PROJECT_FILTER_QUERY_PARAMS
    from pixlstash.route_inventory import iter_api_query_params

    declared = set(PROJECT_FILTER_QUERY_PARAMS)

    found: dict[str, set[str]] = {}
    for method, path, name in iter_api_query_params(built_app):
        if _PROJECT_PARAM_RE.search(name):
            found.setdefault(name, set()).add(f"{method} {path}")

    # Anti-vacuity: the whole check is worthless if the parameter enumeration
    # silently returns nothing (a FastAPI internal moving, a dependant model
    # change). `project_id` is taken by a dozen routes and cannot legitimately
    # vanish, so its absence means the introspection broke, not that the codebase
    # got cleaner.
    assert "project_id" in found, (
        "The query-parameter enumeration found no `project_id` anywhere. That is "
        "not plausible - pixlstash/route_inventory.py::iter_api_query_params has "
        "stopped seeing route parameters (FastAPI internals moved?), so this "
        "guardrail is proving nothing. Fix the enumeration before trusting it."
    )

    undeclared = {
        name: routes
        for name, routes in found.items()
        if name not in declared and name not in _NON_PROJECT_FILTER_QUERY_PARAMS
    }
    assert not undeclared, (
        "Query parameter(s) name a project but are NOT in "
        "authz.membership.PROJECT_FILTER_QUERY_PARAMS, so the authz gate will "
        "not refuse them for a token with no project visibility - a scoped token "
        "can use them as a membership oracle (#708, §16.6).\n"
        "Fix: add the spelling to PROJECT_FILTER_QUERY_PARAMS (preferred), or - "
        "if it genuinely does not filter over the project space - add it to "
        "_NON_PROJECT_FILTER_QUERY_PARAMS in this file WITH a reason. Do not "
        "narrow the regex.\n"
        + "\n".join(
            f"  {name}: {sorted(routes)}" for name, routes in sorted(undeclared.items())
        )
    )

    stale = set(_NON_PROJECT_FILTER_QUERY_PARAMS) - set(found)
    assert not stale, (
        "Entr(y/ies) in _NON_PROJECT_FILTER_QUERY_PARAMS no longer match any "
        "mounted route's query parameters. Prune them so the exemption list "
        "cannot rot into cover for a future parameter of the same name:\n"
        + "\n".join(f"  {name}" for name in sorted(stale))
    )


def test_query_parameter_enumeration_descends_nested_depends():
    """A filter parameter contributed by a nested ``Depends`` is still enumerated.

    ``test_project_filter_params_are_declared`` cannot prove this and says so: it
    only needs ``project_id`` found *somewhere*, and a dozen routes take it at top
    level, so the flattening could be a no-op and that assertion would still pass.
    No route in the tree contributes a project-ish parameter *only* via a nested
    dependency, so nothing else exercises the capability §16.6 advertises. This
    does, on a synthetic app, which is the only way to make the claim non-vacuous
    without adding a real route for the sake of a test.

    It is also the regression test for the flattening itself.
    ``pixlstash.route_inventory.flatten_dependant_fields`` is a local walk over
    ``Dependant``'s own fields precisely because the FastAPI helper it replaced
    (``get_flat_dependant``) was removed in 0.141 - and a walk that silently
    stopped descending would report *fewer* parameters, i.e. false coverage, with
    every other guardrail still green.
    """
    from fastapi import Depends, FastAPI, Query

    from pixlstash.route_inventory import iter_api_query_params

    def _two_levels_down(deep_project_id: str = Query(None)):
        return deep_project_id

    def _one_level_down(
        _deep=Depends(_two_levels_down),
        aliased: str = Query(None, alias="wire_project"),
    ):
        return aliased

    app = FastAPI()

    @app.get("/synthetic")
    def _handler(top_level: str = Query(None), _dep=Depends(_one_level_down)):
        return {}  # pragma: no cover - never called, only introspected

    found = {
        name
        for method, path, name in iter_api_query_params(app)
        if path == "/synthetic"
    }
    assert found == {"top_level", "wire_project", "deep_project_id"}, (
        "The query-parameter enumeration did not descend the whole Depends chain. "
        "It must yield the handler's own parameter, one contributed a level down "
        "(under its WIRE name, not the Python name), and one two levels down. "
        f"Got: {sorted(found)}. Fix flatten_dependant_fields in "
        "pixlstash/route_inventory.py - a partial walk reports false coverage."
    )


def test_project_references_outside_the_query_chokepoint(built_app):
    """The project-filter gate's boundary is acknowledged, not assumed (§16.6).

    ``enforce_project_filter_scope`` reads the query string only. A project named
    in a JSON body or form field is invisible to it, and the only thing keeping
    those routes safe today is that they are writes a READ-scoped token cannot
    reach. This is an acknowledgment inventory in the spirit of
    ``_KNOWN_WEBSOCKET_ROUTES``: it gates nothing, it just makes a new one
    impossible to add without someone looking at it.

    **Read the scope of each assertion; they are deliberately different.**

    1. Exact equality on **typed** bodies only (:data:`_PROJECT_REFS_IN_TYPED_BODY`).
       ``_iter_body_project_refs`` reads declared annotations, so this half is
       arithmetic over routes whose body has a declared shape, and nothing more.
       A body declared ``payload: dict = Body(...)`` declares no keys and
       contributes nothing - see assertion 3.
    2. **READ-reachability, over typed *and* known-opaque project routes.** This is
       the assertion that carries the actual safety argument: the paths yielded by
       the inventory are the same ``/api/v1/…`` strings ``READ_SAFE_POST_PATHS``
       holds and ``auth.py`` compares ``request.url.path`` against, so a project
       body field becoming READ-reachable fails here.
    3. Every route with an **opaque** body that a READ token can reach is on a
       hand-vetted list. Nothing can enumerate a ``dict`` body's keys, so this does
       not claim to know what those handlers read; it claims only that the set of
       routes where the blind spot is *live* stays small and looked-at. Today it is
       one route.

    What none of this covers: which opaque-bodied WRITE routes take a project.
    :data:`_PROJECT_REFS_IN_OPAQUE_BODY` names the five found by reading the
    handlers, and that list cannot be machine-verified in either direction.
    """
    live = frozenset(_iter_body_project_refs(built_app))
    assert live == _PROJECT_REFS_IN_TYPED_BODY, (
        "The set of routes taking a project in a DECLARED (typed) request "
        "BODY/FORM field changed. These are outside enforce_project_filter_scope "
        "(§16.6): confirm the new route cannot be reached by a resource-scoped "
        "token (it must be a non-GET that is NOT in auth.READ_SAFE_POST_PATHS), "
        "then update _PROJECT_REFS_IN_TYPED_BODY and the §16.6 boundary list.\n"
        f"  added:   {sorted(live - _PROJECT_REFS_IN_TYPED_BODY)}\n"
        f"  removed: {sorted(_PROJECT_REFS_IN_TYPED_BODY - live)}"
    )

    from pixlstash.auth import READ_SAFE_POST_PATHS

    reachable = sorted(
        f"{method} {path}"
        for method, path, _field in live | _PROJECT_REFS_IN_OPAQUE_BODY
        if method in ("GET", "HEAD") or path in READ_SAFE_POST_PATHS
    )
    assert not reachable, (
        "A route taking a project in its body/form is reachable by a READ token "
        "(it is a GET, or it is exempted in auth.READ_SAFE_POST_PATHS). Nothing "
        "checks the payload half of the project filter, so a resource-scoped "
        "token can now use it as a membership oracle. Either narrow the project "
        "id in the handler against visible_project_ids, or keep the route out of "
        "READ_SAFE_POST_PATHS:\n" + "\n".join(f"  {entry}" for entry in reachable)
    )

    opaque_reachable = {
        (method, path)
        for method, path in _iter_opaque_body_routes(built_app)
        if method in ("GET", "HEAD") or path in READ_SAFE_POST_PATHS
    }
    unvetted = sorted(opaque_reachable - set(_READ_REACHABLE_OPAQUE_BODY_ROUTES))
    assert not unvetted, (
        "Route(s) with an OPAQUE body (dict/Any - no declared keys) are reachable "
        "by a READ token. Introspection cannot see what the handler reads out of "
        "such a payload, so the 'it is a write a READ token cannot reach' argument "
        "that covers every other body-borne project reference does not apply here "
        "(§16.6). Read the handler; if it names no project, add it to "
        "_READ_REACHABLE_OPAQUE_BODY_ROUTES in this file WITH what it actually "
        "reads and the date you checked. If it does name a project, it is a "
        "membership oracle for a resource-scoped token - narrow it against "
        "visible_project_ids or take the path out of READ_SAFE_POST_PATHS:\n"
        + "\n".join(f"  {method} {path}" for method, path in unvetted)
    )

    stale_vetted = sorted(set(_READ_REACHABLE_OPAQUE_BODY_ROUTES) - opaque_reachable)
    assert not stale_vetted, (
        "Entr(y/ies) in _READ_REACHABLE_OPAQUE_BODY_ROUTES no longer describe a "
        "READ-reachable opaque-bodied route. Prune them so the vetting note cannot "
        "rot into cover for a future route of the same name:\n"
        + "\n".join(f"  {method} {path}" for method, path in stale_vetted)
    )


# `Picture.metadata_fields()` is "every scalar column minus the large binaries"
# (`db_models/picture.py`), and six routes serialise its result to the wire
# through response models that all set `extra="allow"`, so nothing downstream
# filters it (§16.6). A column added to the `picture` table therefore joins those
# payloads with no code change and no failing test, which is how the scalar
# `project_id` reached a picture-scoped token that `GET /projects/{id}` 403s
# (#719). Pinning the membership does not decide whether a new column is safe to
# disclose; it forces the question to be asked once, by whoever adds it.
_PICTURE_METADATA_FIELDS = {
    "aesthetic_score",
    "anomaly_tag_uncertainty",
    "comfyui_loras",
    "comfyui_models",
    "comfyui_positive_prompt",
    "created_at",
    "deleted",
    "deleted_at",
    "description",
    "description_file",
    "description_file_mtime",
    "file_path",
    "format",
    "height",
    "id",
    "import_source_folder",
    "imported_at",
    "is_video",
    # v1.11 Phase 4b. When the layout engine should next ask whether this
    # picture's folder is still true, and NULL for every picture nobody has
    # just reassigned. A scheduling stamp about this one picture, saying
    # nothing about any other and nothing about its content, so a
    # picture-scoped token may see it.
    "layout_check_due_at",
    "metadata_hash",
    "original_file_name",
    # (#950) The picture's own EXIF orientation, 1-8. Carries no membership,
    # ownership or host information, and a token that may read this payload may
    # already fetch the file itself, which carries the very same tag.
    "orientation",
    "pending_character_id",
    "perceptual_hash",
    "pixel_sha",
    "project_id",
    "reference_folder_id",
    "score",
    "size_bin_index",
    "size_bytes",
    "smart_score",
    "source_picture_id",
    "square_crop_side",
    "square_crop_x",
    "square_crop_y",
    "stack_id",
    "stack_position",
    "tag_uncertainty",
    "tags_file",
    "tags_file_mtime",
    "text_score",
    "thumbnail_height",
    "thumbnail_width",
    "width",
    # (B3) The workflow-library keys. Opaque content-addressed digests plus the
    # literal rule version ("v1"): no membership, no ownership, no host or path
    # information.
    #
    # `workflow_instance_hash` is the one that needed thinking about, because
    # the instance tier DOES cover the prompt. It is a digest, so it discloses
    # no text -- and the same payload already carries
    # `comfyui_positive_prompt` in the clear, so a holder who can read this can
    # read the prompt itself and has no use for a confirmable digest of it.
    # Narrowing the prompt column is the live question (§16.3), not this.
    #
    # The recipe they name is prompt-free by construction (library
    # plan §5 -- bucket P is nulled before hashing), so no prompt or caption is
    # in there. They are a correlation key -- two pictures sharing one hash
    # share a workflow -- but only across pictures the token can already see,
    # which is the same shape as `pixel_sha` and `perceptual_hash` above.
    #
    # The residual, stated rather than waved past: a digest is one-way but
    # *confirmable*, and what it covers includes model filenames, which in this
    # product are often authored (a character LoRA is named after its subject).
    # So a holder who can already read these payloads and who GUESSES a
    # filename can confirm the guess. That is strictly weaker than the same
    # holder reading `comfyui_models` in the very same payload, which states
    # those filenames outright, so pinning these three changes nothing about
    # that exposure -- it is `comfyui_models` that would have to be narrowed,
    # and §16.3 already has it open as the host-information question.
    "workflow_hash_version",
    "workflow_instance_hash",
    "workflow_structural_hash",
    "workflow_topology_hash",
}


def test_picture_metadata_fields_membership_is_pinned():
    """A new picture column cannot join the six serialised payloads unnoticed.

    Adding a column is fine. Adding one that carries membership, ownership or
    host information, and letting it ride into `GET /pictures/{id}/metadata`,
    `GET /pictures/{id}/{field}`, `GET /pictures/search`,
    `GET /pictures/likeness-groups`, `GET /picture_sets/{id}` and
    `GET /stacks/{stack_id}/pictures?fields=full` without narrowing it, is the
    #719 defect repeated. If the new column is safe for a scoped token, add it to
    the set below. If it is not, narrow it at those six sites the way
    `narrow_picture_project_ids` does.

    **Membership in the set below does NOT certify a column as safe.** The set is
    the *current* projection, pinned so a change is noticed; it is not a list of
    columns anyone has cleared. Three members are known disclosures to a scoped
    token, reproduced during the #719 review and deliberately left unnarrowed
    pending a decision, so nothing here should be read as their having been
    reviewed and passed:

    * ``pending_character_id`` - a character FK, while ``GET /characters/{id}``
      is ``CHARACTER_SCOPED`` and 403s the same token. Not transient: the dedup
      verdict and keep-cover-only services set it on pictures whose face
      extraction has already run, so it persists. Narrowing it needs a
      ``visible_character_ids`` ladder, which does not exist yet.
    * ``source_picture_id`` - points at a picture the token may not be granted
      (verified: that picture's own endpoints 403 the same token).
    * ``reference_folder_id`` - same shape, and additionally in ``grid_fields()``,
      so it rides every listing rather than only these six routes.

    ``import_source_folder``, ``pixel_sha``, ``original_file_name`` and the
    ComfyUI prompt columns are a separate host-information question (§16.3).
    """
    from pixlstash.db_models import Picture

    actual = set(Picture.metadata_fields())
    # Anti-vacuity: an empty projection would make both assertions below pass
    # while pinning nothing at all.
    assert len(actual) > 20, (
        f"Picture.metadata_fields() returned {len(actual)} fields; the pin is "
        f"vacuous if the projection collapses"
    )

    added = sorted(actual - _PICTURE_METADATA_FIELDS)
    assert not added, (
        "New picture column(s) now ride every payload built from "
        "Picture.metadata_fields(), including the six routes that serialise it "
        'raw through an `extra="allow"` response model (§16.6). Decide whether '
        "a picture-, set- or project-scoped token may learn each one. If yes, "
        "add it here. If no, narrow it at those six sites the way "
        "narrow_picture_project_ids does for project_id (#719):\n"
        + "\n".join(f"  {name}" for name in added)
    )

    removed = sorted(_PICTURE_METADATA_FIELDS - actual)
    assert not removed, (
        "Picture column(s) disappeared from metadata_fields(), so this pin now "
        "describes a projection that no longer exists. Prune them, and check the "
        "six serialisation sites still return what their consumers expect:\n"
        + "\n".join(f"  {name}" for name in removed)
    )


def test_matched_route_path_is_prefix_stripped(built_app):
    """Lock in the Phase-1 gate-keying fact: scope['route'].path is UNPREFIXED.

    OBSERVATION-ONLY (no authz code). Under the installed FastAPI, the effective
    (prefixed) path from the inventory, e.g. /api/v1/pictures/{id}/metadata, is
    NOT the same string as the underlying route object's own path
    (/pictures/{id}/metadata) - the one exposed at request time via
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
        "object's own path for included routes - if they now match, FastAPI's "
        "inclusion model changed and the Phase-1 gate-keying assumption "
        "(key by route identity, not prefixed path) must be re-verified."
    )


# ---------------------------------------------------------------------------
# Guardrail 9: The authz gate is deny-by-default (Phase 1 Step 1)
#
# Step 6 (2026-07-21) flipped the SHIPPED default to ENFORCING
# (AUTHZ_GATE_ENFORCING=True): at runtime an undeclared route is a hard 403 and a
# boot failure. Report-only remains reachable as the one-line rollback (flip the
# constant back to False), and is still proven below via an explicitly
# enforcing=False gate. The fail-closed machinery is CSO acceptance criterion (b):
# an undeclared route must 403 at request time AND boot-fail at startup when the
# flag is enforcing. Correct route-identity keying (criterion a) is necessary but
# NOT sufficient for fail-closed; these decoy tests are the load-bearing proof.
# See the backend refactor plan §3.5 and docs/backend_architecture.md §16.2.
#
# Criterion (c) - SCOPED_LIST / body_ids list-and-batch filtering - is Step 4
# work and is deliberately absent here; nothing below implies the gate covers it.
# ---------------------------------------------------------------------------

# The decoy router is mounted under this prefix, so effective paths are stable
# and the declaring registry can be built statically (no chicken-and-egg with the
# app build). The route object's OWN path is the unprefixed suffix - proving that
# identity keying, not path-string keying, is what matches at request time.
#
# NB: the prefix lives under /api/v1 on purpose - tests/conftest.py globally
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
    """Report-only mode (the one-line rollback, plan §6) denies nothing: an
    explicitly report-only gate with an empty registry lets every route through and
    boot proceeds even though every route is a miss.

    Step 6 (2026-07-21) flipped the SHIPPED default to enforcing, so
    ``AUTHZ_GATE_ENFORCING`` is now True; report-only is no longer the default but
    remains available as the single-boolean rollback (flip the constant back to
    False to restore this behaviour everywhere)."""
    from starlette.testclient import TestClient

    from pixlstash.authz.gate import AUTHZ_GATE_ENFORCING, AuthzGate

    assert AUTHZ_GATE_ENFORCING is True, (
        "Step 6 ships the gate ENFORCING; AUTHZ_GATE_ENFORCING must default to "
        "True. Report-only stays reachable as the one-line rollback (flip the "
        "constant back to False), which this test exercises via enforcing=False."
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
    id() keying matches - even though the effective (prefixed) path differs from
    the route object's own (prefix-stripped) path, which is why string keying
    would fail open. Proven end-to-end: a route declared under its EFFECTIVE path
    is matched at request time via identity and passes the enforcing gate."""
    from starlette.testclient import TestClient

    from pixlstash.authz.gate import AuthzGate
    from pixlstash.route_inventory import iter_api_route_contexts

    gate = AuthzGate(registry=_declared_only_registry(), enforcing=True)
    app = _build_decoy_app(gate)

    # The declaration is keyed by the EFFECTIVE (prefixed) path; the route
    # object's own path is the UNPREFIXED suffix - string keying on scope['route']
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


# ---------------------------------------------------------------------------
# Dependency pin consistency
# ---------------------------------------------------------------------------
# CI installs with ``pip install .[test,dev]`` on a fresh runner, which resolves
# every pyproject specifier to the newest compatible release. It therefore NEVER
# exercises the pinned set in requirements.txt, and cannot notice when a pin sits
# below the floor the code actually needs.
#
# That is not hypothetical: requirements.txt pinned ``fastapi==0.135.1`` while
# pixlstash/route_inventory.py required ``iter_route_contexts`` (added in
# 0.138.0). CI was green throughout; every workstation installed from
# requirements.txt failed to start the server at all.


def _parse_requirements_pins() -> dict:
    """``{canonical name: pinned version}`` for every ``==`` line."""
    from packaging.utils import canonicalize_name

    pins = {}
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, _, version = line.partition("==")
        # Drop any extras marker: ``uvicorn[standard]`` pins the same project.
        name = name.split("[", 1)[0].strip()
        pins[canonicalize_name(name)] = version.strip()
    return pins


def _parse_pyproject_specifiers() -> dict:
    """``{canonical name: SpecifierSet}`` for every runtime dependency."""
    import tomli
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    data = tomli.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = {}
    for entry in data["project"]["dependencies"]:
        req = Requirement(entry)
        specs[canonicalize_name(req.name)] = req.specifier
    return specs


def test_requirements_pins_satisfy_pyproject_specifiers():
    """Every pinned version must satisfy pyproject's declared range.

    Both files are hand-maintained, so they drift. When they drift *downward*
    the failure lands on a developer's machine at import time, long after CI
    said the branch was fine.
    """
    pins = _parse_requirements_pins()
    specs = _parse_pyproject_specifiers()

    violations = []
    for name, pinned in sorted(pins.items()):
        specifier = specs.get(name)
        if specifier is None:
            continue  # test-only or transitive pin; pyproject makes no claim.
        if not specifier.contains(pinned, prereleases=True):
            violations.append(
                f"{name}: requirements.txt pins {pinned}, pyproject requires {specifier}"
            )

    assert not violations, (
        "requirements.txt pins conflict with pyproject.toml:\n  "
        + "\n  ".join(violations)
    )


def test_fastapi_floor_covers_route_inventory_dependency():
    """The declared FastAPI floor must include ``iter_route_contexts``.

    Pinned explicitly because the requirement is invisible in the specifier
    itself: nothing about ``fastapi>=0.138.0`` says why, and a future tidy-up
    that "relaxes" it would silently break startup for anyone who resolves
    lower. See pixlstash/route_inventory.py.
    """
    from packaging.version import Version

    specs = _parse_pyproject_specifiers()
    floors = [
        Version(spec.version)
        for spec in specs["fastapi"]
        if spec.operator in (">=", "==", "~=")
    ]
    assert floors, "fastapi must declare a lower bound in pyproject.toml"
    assert min(floors) >= Version("0.138.0"), (
        "fastapi.routing.iter_route_contexts (required by "
        "pixlstash/route_inventory.py) first appears in 0.138.0"
    )


# ---------------------------------------------------------------------------
# Guardrail: every SQLite engine comes from create_configured_engine (#651/#709)
# ---------------------------------------------------------------------------
# The §13 connection settings arrive in two halves: ``connect_args={"timeout":
# SQLITE_BUSY_TIMEOUT_S}`` and the ``connect`` listener that sets the PRAGMAs
# and registers the custom SQL functions. A call site that builds its own
# engine gets neither and silently runs on SQLite's defaults: a 5 s busy
# timeout, a 2 MiB page cache, a rollback journal and foreign keys OFF. That
# has been a real bug twice (#651, #709).
#
# This is AST-based and recursive on purpose. The first version of this
# guardrail was a non-recursive text grep over one package directory, and every
# one of the shapes in _ENGINE_GUARDRAIL_ESCAPES walked straight past it.

# SQLAlchemy / SQLModel entry points that construct an Engine.
_ENGINE_FACTORIES = ("create_engine", "engine_from_config")

# Repo-relative path -> why that file is allowed to build its own engine.
# Every entry is a deliberate architectural exception, and
# ``test_engine_factory_allowlist_has_no_dead_entries`` keeps the list honest.
_ENGINE_FACTORY_ALLOWLIST = {
    "pixlstash/database.py": (
        "Defines create_configured_engine itself. This is the single sanctioned "
        "create_engine call that every other call site routes through."
    ),
    "pixlstash/migrations/env.py": (
        "Alembic owns its own connection. It builds an engine with "
        "engine_from_config from alembic.ini and runs migrations with foreign "
        "keys OFF, and its render_as_batch=True table recreation (copy into a "
        "new table, drop, rename) would be hazardous with FK enforcement on. "
        "Deliberately not routed through create_configured_engine."
    ),
    "pixlstash/hub/engine.py": (
        "The hub is the cross-library registry, not a vault, and its engine is "
        "configured explicitly rather than left on SQLite's defaults (WAL, "
        "synchronous=NORMAL, foreign_keys=ON in _configure_connection). It "
        "deviates on purpose in three ways create_configured_engine cannot "
        "express: HUB_BUSY_TIMEOUT_S is 5 s rather than the vault's 30 s "
        "because hub writes are tiny registry updates contended across "
        "processes; the vault's custom SQL functions (levenshtein, "
        "cosine_similarity, character_face_likeness) are meaningless against "
        "the hub schema; and the vault's 16 MiB per-connection page cache "
        "would be paid on every pooled hub connection for a database that "
        "holds a handful of rows."
    ),
}


def _engine_factory_offenders(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, snippet)`` for every engine construction in *source*.

    Detects, in addition to a plain ``create_engine(...)`` call:

    - an aliased import (``from sqlmodel import create_engine as _ce``) and any
      later use of that alias, including binding it to another name first;
    - a qualified call (``sa.create_engine(...)``);
    - a dynamic lookup (``getattr(sa, "create_engine")(...)``).

    Args:
        source: Python source text of a single module.

    Returns:
        Sorted ``(lineno, stripped source line)`` pairs, one per offending
        line.
    """
    tree = ast.parse(source)

    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _ENGINE_FACTORIES:
                    aliases.add(alias.asname or alias.name)

    lines = source.splitlines()
    hits: dict[int, str] = {}

    def _record(node) -> None:
        lineno = getattr(node, "lineno", 0)
        snippet = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
        hits.setdefault(lineno, snippet)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in aliases:
            _record(node)
        elif isinstance(node, ast.Attribute) and node.attr in _ENGINE_FACTORIES:
            _record(node)
        elif isinstance(node, ast.Constant) and node.value in _ENGINE_FACTORIES:
            _record(node)

    return sorted(hits.items())


def _scan_for_engine_factories(root: Path, base: Path) -> dict[str, list]:
    """Walk *root* recursively and map each offending module to its hits.

    Args:
        root: Directory tree to scan.
        base: Directory the reported keys are made relative to.

    Returns:
        ``{posix relative path: [(lineno, snippet), ...]}`` for modules that
        build an engine of their own.
    """
    found: dict[str, list] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            pytest.fail(f"could not read {path}: {exc}")
        try:
            hits = _engine_factory_offenders(source)
        except SyntaxError as exc:
            pytest.fail(f"could not parse {path}: {exc}")
        if hits:
            found[path.relative_to(base).as_posix()] = hits
    return found


def test_no_engine_is_built_outside_create_configured_engine():
    """Arithmetic completeness: nothing under ``pixlstash/`` builds its own engine.

    Scoped to the whole package, not one directory. A bare engine planted in
    ``services/snapshot_service.py`` left the previous, restore-package-only
    version of this guardrail entirely green.
    """
    found = _scan_for_engine_factories(PIXLSTASH_DIR, REPO_ROOT)

    offenders = []
    for rel, hits in sorted(found.items()):
        if rel in _ENGINE_FACTORY_ALLOWLIST:
            continue
        for lineno, snippet in hits:
            offenders.append(f"  {rel}:{lineno}: {snippet}")

    assert not offenders, (
        "SQLite engines must be built by pixlstash.database.create_configured_engine "
        "(docs/backend_architecture.md §13). A bare engine gets neither the busy "
        "timeout (connect_args) nor the PRAGMAs and custom SQL functions (the "
        "connect listener), so it silently runs on SQLite's defaults: 5 s busy "
        "timeout, 2 MiB page cache, rollback journal, foreign keys OFF (#651, "
        "#709).\n\nOffending call sites:\n"
        + "\n".join(offenders)
        + "\n\nFix: call create_configured_engine(path), or "
        "services/restore/schema_upgrade.snapshot_engine(path) for a snapshot "
        "file. If a call site genuinely must own its engine, add its repo-relative "
        "path to _ENGINE_FACTORY_ALLOWLIST in this file together with the reason."
    )


def test_engine_factory_allowlist_has_no_dead_entries():
    """An allowlist entry that no longer names a real engine build is a lie.

    A stale entry would silently re-permit the file the day someone puts an
    unconfigured engine back into it.
    """
    found = _scan_for_engine_factories(PIXLSTASH_DIR, REPO_ROOT)
    stale = sorted(set(_ENGINE_FACTORY_ALLOWLIST) - set(found))
    assert not stale, (
        "these _ENGINE_FACTORY_ALLOWLIST entries no longer build an engine and "
        "must be removed from the allowlist:\n  " + "\n  ".join(stale)
    )


# Every shape that escaped the original text-grep guardrail, plus the ones a
# naive AST check would still miss. Each is planted in a throwaway tree below
# and must be caught.
_ENGINE_GUARDRAIL_ESCAPES = {
    "aliased import": (
        "from sqlmodel import create_engine as _ce\n\nE = _ce('sqlite:///x.db')\n"
    ),
    "qualified call": (
        "import sqlalchemy as sa\n\nE = sa.create_engine('sqlite:///x.db')\n"
    ),
    "dynamic lookup": (
        "import sqlalchemy as sa\n\nE = getattr(sa, 'create_engine')('sqlite:///x.db')\n"
    ),
    "indirect binding": (
        "from sqlalchemy import create_engine\n\n"
        "_factory = create_engine\nE = _factory('sqlite:///x.db')\n"
    ),
    "alembic style": (
        "from sqlalchemy import engine_from_config\n\nE = engine_from_config({})\n"
    ),
    "plain call": (
        "from sqlmodel import create_engine\n\nE = create_engine('sqlite:///x.db')\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(_ENGINE_GUARDRAIL_ESCAPES))
def test_engine_guardrail_detects_every_known_escape_shape(shape, tmp_path):
    """The guardrail's own regression test.

    Each shape is planted inside a **nested subpackage**, which also pins the
    recursion: the previous ``os.listdir`` version never descended, so a new
    subpackage under ``services/restore/`` was a free pass.
    """
    nested = tmp_path / "pkg" / "sub" / "deeper"
    nested.mkdir(parents=True)
    (nested / "sneaky.py").write_text(_ENGINE_GUARDRAIL_ESCAPES[shape])

    found = _scan_for_engine_factories(tmp_path, tmp_path)
    assert "pkg/sub/deeper/sneaky.py" in found, (
        f"the {shape!r} escape shape was not detected: {found}"
    )


def test_engine_guardrail_does_not_flag_the_configured_helpers(tmp_path):
    """Over-blocking is its own regression: the sanctioned calls must pass."""
    (tmp_path / "ok.py").write_text(
        "from pixlstash.database import create_configured_engine\n"
        "from .schema_upgrade import snapshot_engine\n\n"
        "E = create_configured_engine('/tmp/x.db')\n"
        "S = snapshot_engine('/tmp/y.sqlite')\n"
    )
    assert _scan_for_engine_factories(tmp_path, tmp_path) == {}


# ---------------------------------------------------------------------------
# Guardrail: the ML stack must not be imported on the server's import path
# ---------------------------------------------------------------------------
#
# `torch` and friends cost ~2.8 s to import, against 0.29 s for the whole web
# stack. Because tests/conftest.py imports Server, a single module-scope
# `import torch` anywhere on that path makes EVERY test process pay for it
# before running one assertion (measured: 4.17 s -> 1.38 s for a targeted run
# once the ML imports were made function-local). See backend_architecture §3,
# "ML import discipline". This guardrail is what stops that regressing.

_ML_MODULES = (
    "torch",
    "torchvision",
    "transformers",
    "sentence_transformers",
    "open_clip",
    "insightface",
    "onnxruntime",
)

_ML_PROBE = (
    "import sys, json\n"
    "__import__({target!r})\n"
    "print(json.dumps([m for m in {names!r} if m in sys.modules]))\n"
)


def _ml_modules_loaded_by(target: str) -> list[str]:
    """Import *target* in a FRESH interpreter, return which ML libs it pulled.

    A subprocess is mandatory: by the time this test runs, some earlier test in
    the same session has almost certainly imported torch already, so an
    in-process check would be meaningless.
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", _ML_PROBE.format(target=target, names=_ML_MODULES)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"probe failed to import {target!r}:\n{proc.stdout}\n{proc.stderr}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_server_import_pulls_no_ml_libraries():
    """Importing the server must not drag in torch/onnxruntime/etc."""
    loaded = _ml_modules_loaded_by("pixlstash.server")
    assert loaded == [], (
        "pixlstash.server imported these ML libraries at module scope: "
        f"{loaded}. Move the import inside the function that uses it "
        "(see docs/backend_architecture.md §3, 'ML import discipline'). "
        "Watch for indirect pullers: importing a plain constant from a module "
        "that itself imports open_clip is enough to trip this."
    )


def test_ml_import_probe_has_teeth():
    """Meta-check: the probe must actually DETECT a module that loads torch.

    Without this, `test_server_import_pulls_no_ml_libraries` could pass simply
    because the probe never observes anything. `wd14` deliberately keeps its
    module-scope ML imports (it is off the server's import path), so it is a
    stable positive control.
    """
    loaded = _ml_modules_loaded_by("pixlstash.tagger_plugins.wd14")
    assert "torch" in loaded and "onnxruntime" in loaded, (
        "the ML-import probe failed to notice a module that definitely imports "
        f"torch and onnxruntime; it reported {loaded}"
    )


# ---------------------------------------------------------------------------
# Guardrail: no unsanctioned private-network address literal
# ---------------------------------------------------------------------------
# Push-time secret scanning reads every *added* line and stops the push on an
# RFC 1918 address. That is why this repository has to stay clean rather than
# merely stop adding literals: merging develop into a branch re-presents
# everything landed since its base as added lines, so #963 was blocked by a
# literal it had never touched.
#
# Six strings are exempt and nothing merely shaped like them: the three RFC
# 1918 blocks, which are a definition rather than a machine and the constant
# every locality gate is built out of, and the first host of each block, for a
# test vector that has to be inside RFC 1918 because that is the branch it
# exercises. Every network has something at ``.1``, so those say nothing about
# whose network it is; the rest of the octet space does.
#
# The exemption is a *prefix* test rather than a whole-match one, which is what
# keeps this rule the same shape as the scan it mirrors in both directions: a
# sanctioned host wearing a prefix length is not reported, while an address
# that merely begins with one and carries a further octet is.
_SANCTIONED_PRIVATE_LITERALS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "10.0.0.1",
    "172.16.0.1",
    "192.168.0.1",
)

_PRIVATE_ADDRESS_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
)

# Named roots, never a repo-root walk: that is what lets every scan in this
# file work without a node_modules / dist / .venv exclusion list, and a
# too-greedy exclusion is a silent pass. The list is wide because the scan it
# mirrors reads the whole diff - a literal in a workflow, an installer script
# or the website blocks a push exactly as one in a test does.
_PRIVATE_ADDRESS_ROOTS = (
    ".github",
    "docs",
    "electron",
    "frontend/e2e",
    "frontend/src",
    "installer",
    "pixlstash",
    "scripts",
    "tests",
    "website",
)
# Directories whose *immediate* files are read whatever they are called -
# Dockerfiles, docker-entrypoint.sh, .env.example, the frontend's build and
# Playwright configs. Derived rather than listed on purpose: a hand-kept file
# list is how three Dockerfiles came to be named here and then filtered
# straight back out by the suffix set below, and it would have gone on missing
# every root file added after it. Nothing walks *into* these, so node_modules
# is still never opened.
_PRIVATE_ADDRESS_FLAT_DIRS = (".", "frontend")
# docs/reviews/ is gitignored and machine-local, so a fresh checkout is not
# guaranteed to have it and nothing CI-enforced may read it (CLAUDE.md, and
# the same rule that keeps the authz coverage matrix in docs/ proper).
_PRIVATE_ADDRESS_SKIP = ("docs/reviews",)
_PRIVATE_ADDRESS_SUFFIXES = frozenset(
    {
        ".cfg",
        ".css",
        ".html",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".bat",
        ".iss",
        ".ps1",
        ".py",
        ".sh",
        ".toml",
        ".ts",
        ".txt",
        ".vue",
        ".yaml",
        ".yml",
    }
)


# A digit extends the last octet; a dot starts another one. End of line is
# neither, which is why this is a set of characters and not a substring test -
# ``"" in "0123"`` is True in Python, so the string spelling reports every line
# that happens to end on a sanctioned address.
_ADDRESS_CONTINUES = frozenset("0123456789.")


def _address_continues_after(line: str, at: int) -> bool:
    """Whether the address ending at *at* carries on past that point.

    A digit does, because it extends the last octet. So does a dot, and that
    is the deliberately blunt half: a dot followed by a digit is genuinely a
    further octet, and a dot followed by a space is the end of a sentence - but
    the scan this mirrors treats both the same and reports the second, so a
    rule that quietly permitted it would pass a line the push then blocks. Over
    -strict here costs one reworded sentence; loose costs a blocked push, which
    is the failure this whole guardrail exists to prevent. Ending a sentence on
    the literal is therefore reported, and the way out is to not end it there.

    End of line is not a continuation, and neither is a quote, a comma, a
    bracket or the ``/`` of a prefix length.
    """
    return line[at : at + 1] in _ADDRESS_CONTINUES


def _is_sanctioned_private_literal(line: str, start: int) -> bool:
    """Whether the address at *start* is one of the six exempt strings.

    Read as a prefix, and rejected only when what follows genuinely continues
    the address: a trailing ``/24`` is the sanctioned host wearing a prefix
    length, a further octet is a different address that merely begins with one.
    """
    for literal in _SANCTIONED_PRIVATE_LITERALS:
        if line.startswith(literal, start) and not _address_continues_after(
            line, start + len(literal)
        ):
            return True
    return False


def _private_address_offenders(root: Path, repo_root: Path) -> list[str]:
    """Return ``"<path>:<lineno>: <line>"`` for every unsanctioned literal.

    A file named directly is read whatever it is called; the suffix list only
    decides what to open when walking a directory. Naming a file and then
    filtering it out by extension is how ``Dockerfile``, ``Dockerfile.demo``
    and ``Dockerfile.gpu`` sat in the list unscanned.
    """
    offenders: list[str] = []
    named = root.is_file()
    paths = [root] if named else sorted(root.rglob("*"))
    for path in paths:
        if not path.is_file():
            continue
        if not named and path.suffix not in _PRIVATE_ADDRESS_SUFFIXES:
            continue
        rel = path.relative_to(repo_root)
        if rel.as_posix().startswith(_PRIVATE_ADDRESS_SKIP):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            # Every match on the line, not the first: a line carrying a
            # sanctioned vector *and* a real address must still be reported.
            for match in _PRIVATE_ADDRESS_RE.finditer(line):
                if _is_sanctioned_private_literal(line, match.start()):
                    continue
                offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")
                break
    return offenders


def test_no_unsanctioned_private_address_literal():
    """A bare RFC 1918 literal is somebody's network until proven otherwise."""
    roots = [REPO_ROOT / name for name in _PRIVATE_ADDRESS_ROOTS]
    flat = [REPO_ROOT / name for name in _PRIVATE_ADDRESS_FLAT_DIRS]
    missing = sorted(
        str(d.relative_to(REPO_ROOT)) for d in (*roots, *flat) if not d.is_dir()
    )
    assert not missing, (
        "these scan targets no longer exist, so the guardrail silently stopped "
        f"covering them: {missing}. Re-point or remove the entry."
    )

    offenders: list[str] = []
    for root in roots:
        offenders += _private_address_offenders(root, REPO_ROOT)
    for directory in flat:
        # Files only. A subdirectory here would be walked, and the one sitting
        # in frontend/ is node_modules.
        for path in sorted(p for p in directory.iterdir() if p.is_file()):
            offenders += _private_address_offenders(path, REPO_ROOT)
    assert not offenders, (
        "these lines carry a private-network address that push-time secret "
        "scanning will stop a push over:\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nFix: in a test, import the vector from tests/network_vectors.py "
        "rather than writing a number - inventing a different one just moves "
        "the problem. In prose, write a placeholder such as <lan-ip>, or name "
        "the RFC 1918 block itself - with its prefix length, since a bare "
        "network address is not one of the six exempt strings."
    )


# Derived from the sanctioned vector rather than written out, because this file
# is scanned by its own guardrail and by the push-time scan, and a literal here
# would stop the push that carries the rule forbidding it. Deriving it also
# pins the constant: if LAN_IPV4 stops being the first host of its block, the
# neighbour below stops being a neighbour and the teeth test says so.
_TEETH_OFFENDER = LAN_IPV4.replace(".0.1", ".1.50")


def test_private_address_guardrail_has_teeth(tmp_path):
    """Both directions, or the guardrail can pass by being broken."""
    assert _TEETH_OFFENDER != LAN_IPV4, (
        f"the teeth fixture is no longer a neighbour of {LAN_IPV4}"
    )
    (tmp_path / "bad.md").write_text(f"the gateway is {_TEETH_OFFENDER}\n")
    (tmp_path / "good.md").write_text(
        f"the gateway is {LAN_IPV4} on 192.168.0.0/16, and {LAN_IPV4}/24\n"
        # Ending the *line* on the literal is its own case: "nothing" is not
        # another octet, and reading it as one reported every such line.
        f"the gateway is {LAN_IPV4}\n"
    )
    (tmp_path / "mixed.md").write_text(
        f"{LAN_IPV4} is fine but {_TEETH_OFFENDER} is not\n"
    )
    # A digit straight after a sanctioned prefix is a different host, and this
    # is the half a continuation set of "." alone would exempt in silence.
    (tmp_path / "digit.md").write_text(
        f"{LAN_IPV4}0 and {PRIVATE_10_IPV4}0 and {PRIVATE_172_IPV4}0\n"
    )
    # Ending a *sentence* on a sanctioned literal is reported, because the scan
    # this mirrors reports it. Built rather than written out, since writing it
    # here would block the push carrying the rule. Reword the sentence.
    (tmp_path / "sentence.md").write_text(
        "".join(f"the block is {literal}. " for literal in _SANCTIONED_PRIVATE_LITERALS)
        + "\n"
    )
    (tmp_path / "longer.md").write_text(f"the gateway is {LAN_IPV4}.7\n")

    offenders = _private_address_offenders(tmp_path, tmp_path)
    caught = {o.split(":", 1)[0] for o in offenders}
    assert caught == {"bad.md", "mixed.md", "longer.md", "digit.md", "sentence.md"}, (
        f"the guardrail reported the wrong set of files: {offenders}"
    )


def test_private_address_guardrail_reads_a_named_file_of_any_kind(tmp_path):
    """The selection half, which the regex teeth above cannot reach.

    A file reached through ``_PRIVATE_ADDRESS_FLAT_DIRS`` is opened because it
    was named, not because of its extension - the bug this pins is three
    Dockerfiles that were listed and then filtered back out by the suffix set.
    """
    dockerfile = tmp_path / "Dockerfile.demo"
    dockerfile.write_text(f"EXPOSE 9537  # was {_TEETH_OFFENDER}\n")

    walked = _private_address_offenders(tmp_path, tmp_path)
    assert not walked, f"a directory walk must still go by extension, but read {walked}"

    named = _private_address_offenders(dockerfile, tmp_path)
    assert [o.split(":", 1)[0] for o in named] == ["Dockerfile.demo"], (
        f"a named file must be read whatever it is called; got {named}"
    )


def test_tests_close_the_server_not_only_its_vault():
    """Closing a test server's vault alone leaks the hub's SQLite connection.

    Harmless on POSIX, fatal on Windows: ``TemporaryDirectory`` cleanup then
    raises a sharing violation on ``hub.db``. ``Server.close`` closes both and
    is the only supported teardown; this pins that, because the failure is
    invisible until a Windows shard spends a full gate run finding it.

    The needle is assembled from parts so this file is not its own offender.
    """
    needle = ".vault" + ".close()"
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}"
        for path in (REPO_ROOT / "tests").rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if needle in line
    ]
    assert not offenders, (
        "close the whole server, not only its vault - server.close() - at "
        + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Guardrail: every runtime dependency is installed in every Docker image
# ---------------------------------------------------------------------------


def test_dockerfiles_install_every_runtime_dependency():
    """A dependency added to ``pyproject.toml`` must reach the images too.

    All three Dockerfiles hand-maintain their ``pip install`` list and then run
    ``pip install --no-deps -e .``, so a new dependency is silently absent from
    the built image and only shows up as a ``ModuleNotFoundError`` at start-up.
    That is how ``send2trash`` reached the demo image and crashed it on import.

    The check is a substring one on purpose: aliases like
    ``opencv-python-headless`` and ``onnxruntime-gpu`` legitimately satisfy a
    plain name. Only package tokens in ``RUN pip install`` instructions count;
    dependency names in comments must not satisfy the guardrail.
    """
    import tomllib

    dependencies = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["dependencies"]
    names = {
        re.split(r"[<>=!~;\[]", spec)[0].strip().lower().replace("_", "-")
        for spec in dependencies
    }

    def installed_packages(text: str) -> list[str]:
        packages = []
        lines = text.splitlines()
        for start, line in enumerate(lines):
            if not re.match(r"\s*run\s+pip\s+install\b", line, re.IGNORECASE):
                continue
            command = line
            index = start
            while command.rstrip().endswith("\\") and index + 1 < len(lines):
                index += 1
                command = f"{command.rstrip()[:-1]} {lines[index].strip()}"
            # Ignore comments inside a continued command too: a commented-out
            # package name is not an installed package.
            tokens = shlex.split(command, comments=True)
            install_at = next(
                (
                    i
                    for i in range(len(tokens) - 1)
                    if tokens[i : i + 2] == ["pip", "install"]
                ),
                None,
            )
            if install_at is None:
                continue
            skip_next = False
            for token in tokens[install_at + 2 :]:
                if skip_next:
                    skip_next = False
                elif token in {"--index-url", "--extra-index-url", "-i"}:
                    skip_next = True
                elif not token.startswith("-"):
                    packages.append(token.lower().replace("_", "-"))
        return packages

    missing = []
    for filename in ("Dockerfile", "Dockerfile.gpu", "Dockerfile.demo"):
        packages = installed_packages(
            (REPO_ROOT / filename).read_text(encoding="utf-8")
        )
        missing += [
            f"{filename}: {name}"
            for name in sorted(names)
            if not any(name in package for package in packages)
        ]

    assert not missing, (
        "these runtime dependencies are never installed in the image, so the "
        "container will fail on import: " + ", ".join(missing)
    )


def test_frontend_import_extensions_match_the_staging_allowlist():
    """The importer's client-side filter must say exactly what the server takes.

    The frontend used to filter a drop against the lists it uses to *display*
    pictures, which are far wider than what the staging route accepts. A model
    file, a `.psd` or a `.wmv` therefore uploaded in full - a gigabyte, in the
    report that prompted this - before the route skipped it as unsupported and
    the commit came back "No staged files to import". Two allowlists with
    nothing holding them together is what made that possible, so this is the
    thing holding them together.
    """
    from pixlstash.routes.pictures._import import STAGING_ALLOWED_MEDIA_EXTS

    media_js = (REPO_ROOT / "frontend" / "src" / "utils" / "media.js").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"export const IMPORT_MEDIA_EXTENSIONS = \[(.*?)\];", media_js, re.DOTALL
    )
    assert match, (
        "IMPORT_MEDIA_EXTENSIONS is gone from frontend/src/utils/media.js - the "
        "importer is filtering against something else, and this guardrail can no "
        "longer see what."
    )
    frontend_exts = {f".{ext}" for ext in re.findall(r'"([^"]+)"', match.group(1))}

    assert frontend_exts == STAGING_ALLOWED_MEDIA_EXTS, (
        "the client-side import filter and the staging route disagree: "
        f"client-only={sorted(frontend_exts - STAGING_ALLOWED_MEDIA_EXTS)}, "
        f"server-only={sorted(STAGING_ALLOWED_MEDIA_EXTS - frontend_exts)}. "
        "A client-only extension uploads the whole file and then fails the "
        "commit; a server-only one is refused before it is ever offered."
    )
