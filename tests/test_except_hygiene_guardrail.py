"""Except-hygiene guardrail (ratchet).

Enforces the CLAUDE.md exception-handling policy across the backend: a *broad*
exception handler (``except Exception``/``except BaseException``/bare ``except``)
anywhere in ``pixlstash/`` must NOT silently swallow the error. "Silently swallow"
means the handler body is *only* control flow - ``return``/``continue``/``break``/
``pass`` - with no logging call and no re-raise, so an unexpected failure vanishes
with no trace.

This runs in AUDIT MODE with a closed allowlist of deliberate best-effort swallows
(see ``_SILENT_SWALLOW_ALLOWLIST``). The allowlist may only SHRINK: adding a log
line (the fix) removes a site from it, and the stale-entry check forbids leaving a
fixed site parked here. A NEW silent broad swallow fails immediately.

The allowlist was seeded across two stages of the B1 triage sweep
(docs/reviews/backend-except-triage-plan.md):
- Stage 1 covered ``tasks/`` and ``services/`` (17 broad swallows; 15 logged, the
  2 genuine best-effort survivors remain below).
- Stage 2 extended the scan to ``core`` (everything else in ``pixlstash/``): 29
  broad swallows, 12 logged and 17 kept as deliberate best-effort survivors (14
  distinct call sites; ``_coerce_metadata_value`` holds 4 sites under one key).

Scope covers all of ``pixlstash/`` EXCEPT two trees:
- ``authz/`` - security-sensitive; its swallows are reviewed under the separate
  authz sign-off process, not this ratchet.
- ``migrations/`` - one-shot Alembic scripts that are immutable once shipped
  (CLAUDE.md: a migration on main must never be modified), so a log-only fix is
  not an available remedy and the ratchet would have nothing to enforce.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PIXLSTASH_DIR = REPO_ROOT / "pixlstash"
# Top-level ``pixlstash/`` subtrees excluded from the scan (see module docstring).
_EXCLUDED_TOP_DIRS = frozenset({"authz", "migrations"})

# Statements that count as pure control flow. A handler whose body is composed
# ONLY of these does no work and logs nothing - it silently swallows.
_CONTROL_FLOW = (ast.Return, ast.Continue, ast.Break, ast.Pass)

# Allowlist key: (relative_posix_path, enclosing_function_name, exception_name).
# Chosen to resist line drift (adding/removing lines above a site does not move
# the function name or exception type). Each surviving swallow lives in a distinct
# function, so the key is unambiguous - with one deliberate exception:
# ``_coerce_metadata_value`` holds four identical coercion swallows that share a
# single key (all four are the same best-effort pattern, so one entry covers them).
#
# {key: justification}. deny-by-default: each entry is a decision someone owns.
# This list may only shrink - log the swallow and delete its entry.
_SILENT_SWALLOW_ALLOWLIST = {
    # --- Stage 1: tasks/ and services/ survivors ---
    (
        "pixlstash/services/comfyui_service.py",
        "_extract_text_from_value",
        "Exception",
    ): (
        "best-effort JSON serialisation of arbitrary payloads; a non-serialisable "
        "value is normal and str(value) IS the intended result, not an error path"
    ),
    (
        "pixlstash/tasks/face_extraction_task.py",
        "_get_loaded_relationship",
        "Exception",
    ): (
        "best-effort ORM inspection; a non-inspectable object simply is not a "
        "loaded relationship, so (False, None) IS the answer, not an error"
    ),
    # --- Stage 2: core survivors ---
    (
        "pixlstash/database.py",
        "character_face_likeness",
        "Exception",
    ): (
        "per-row SQLite scalar: any decode/maths failure means 'no likeness' and "
        "0.0 IS the ranking answer; logging would fire once per candidate row"
    ),
    (
        "pixlstash/routes/characters_faces.py",
        "face_area",
        "Exception",
    ): (
        "sort-key guard: a face missing usable dimensions sorts as zero area, so "
        "0 IS the answer, not an error"
    ),
    (
        "pixlstash/startup_checks.py",
        "_detect_gpu_arch_note",
        "Exception",
    ): (
        "best-effort startup diagnostic: when GPU capability cannot be queried the "
        "empty note ('' = no extra hint) IS the answer, and we must not fail startup"
    ),
    (
        "pixlstash/startup_checks.py",
        "_detect_onnxruntime_package",
        "Exception",
    ): (
        "best-effort package probe for a diagnostic hint; an unreadable distribution "
        "just means 'try the next name / unknown'"
    ),
    (
        "pixlstash/tagger_plugins/joycaption.py",
        "needs_download",
        "Exception",
    ): (
        "best-effort HF cache probe: if we cannot tell, assume the model needs "
        "downloading; True IS the safe default and download() surfaces real errors"
    ),
    (
        "pixlstash/task_runner.py",
        "_get_total_vram_mb",
        "Exception",
    ): (
        "nvidia-smi absent/failing is normal on CPU-only hosts; 0 (no VRAM) IS the "
        "documented answer, so logging it would be routine noise"
    ),
    (
        "pixlstash/task_runner.py",
        "_get_process_vram_mb",
        "Exception",
    ): (
        "per-line parse guard around nvidia-smi output: an unparseable row is "
        "skipped, which IS the correct handling"
    ),
    (
        "pixlstash/utils/image_processing/image_utils.py",
        "_coerce_metadata_value",
        "Exception",
    ): (
        "best-effort coercion (4 sites, one key): when a numeric/bytes value cannot "
        "be converted the str()/repr() fallback IS the JSON-serialisable result"
    ),
    (
        "pixlstash/utils/quality/smart_score_utils.py",
        "smart_score_penalised_tags",
        "Exception",
    ): (
        "documented JSON parse-reject: unparseable settings fall back to the "
        "caller's default weights (see docstring); the fallback IS the answer"
    ),
    (
        "pixlstash/utils/service/caption_utils.py",
        "normalize_hidden_tags",
        "Exception",
    ): (
        "documented JSON parse-reject: unparseable input yields None (see "
        "docstring), which IS the answer"
    ),
    (
        "pixlstash/utils/service/export_utils.py",
        "generate_zip",
        "Exception",
    ): (
        "skip a non-UTF-8 PNG text chunk during export; dropping an undecodable "
        "metadata key IS correct, not an error"
    ),
    (
        "pixlstash/utils/vram_utils.py",
        "query_total_vram_mb",
        "Exception",
    ): (
        "nvidia-smi absent/failing is normal on CPU-only hosts; 0 (no VRAM) IS the "
        "documented answer, so logging it would be routine noise"
    ),
    (
        "pixlstash/vault.py",
        "fetch",
        "Exception",
    ): (
        "relationship-length guard: a non-lenable value is simply 'not populated', "
        "so (False, value) IS the answer"
    ),
}


def _broad_exception_name(handler: ast.ExceptHandler) -> str | None:
    """Return the broad-handler name, or None if the handler is narrow.

    'bare' for a bare ``except:``; 'Exception'/'BaseException' for those catch-all
    types (including a tuple that contains one). Narrow handlers return None and
    are ignored - a typed ``except ValueError`` return IS a valid reject signal.
    """
    node_type = handler.type
    if node_type is None:
        return "bare"
    names = []
    if isinstance(node_type, ast.Tuple):
        names = [e for e in node_type.elts if isinstance(e, ast.Name)]
    elif isinstance(node_type, ast.Name):
        names = [node_type]
    for name in names:
        if name.id in ("Exception", "BaseException"):
            return name.id
    return None


def _is_silent_swallow(handler: ast.ExceptHandler) -> bool:
    """True if the handler body is only control flow (no logging, no re-raise)."""
    return bool(handler.body) and all(
        isinstance(stmt, _CONTROL_FLOW) for stmt in handler.body
    )


def _enclosing_function_name(tree: ast.AST, lineno: int) -> str:
    """Name of the innermost function enclosing ``lineno`` ('<module>' if none)."""
    chain = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= lineno <= (node.end_lineno or node.lineno)
    ]
    chain.sort(key=lambda n: n.lineno)
    return chain[-1].name if chain else "<module>"


def _iter_silent_broad_swallows():
    """Yield (rel_path, lineno, func_name, exc_name) for each silent broad swallow.

    Scans all of ``pixlstash/`` except the ``authz/`` and ``migrations/`` subtrees
    (see the module docstring for why those are excluded).
    """
    for path in sorted(PIXLSTASH_DIR.rglob("*.py")):
        if path.relative_to(PIXLSTASH_DIR).parts[0] in _EXCLUDED_TOP_DIRS:
            continue
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            exc_name = _broad_exception_name(node)
            if exc_name is None or not _is_silent_swallow(node):
                continue
            func = _enclosing_function_name(tree, node.lineno)
            yield rel, node.lineno, func, exc_name


def test_no_unlisted_silent_broad_swallow_in_backend():
    """A broad handler that only returns/continues/breaks/passes must log (or be
    a justified allowlist entry). Empty cells are the bug list."""
    violations = []
    for rel, lineno, func, exc_name in _iter_silent_broad_swallows():
        if (rel, func, exc_name) in _SILENT_SWALLOW_ALLOWLIST:
            continue
        violations.append(
            f"{rel}:{lineno} in '{func}': except {exc_name} → silent swallow"
        )

    assert not violations, (
        "Broad exception handler(s) in pixlstash/ swallow the error with "
        "no logging and no re-raise (CLAUDE.md: swallowed exceptions must be logged "
        "with context). Add a `logger.debug/warning/exception(...)` naming the "
        "operation and item identity (do NOT change the return/continue), or, if "
        "the swallow is genuinely deliberate, add a justified entry to "
        "_SILENT_SWALLOW_ALLOWLIST with a one-line comment at the site:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_silent_swallow_allowlist_has_no_stale_entries():
    """Keep the ratchet honest: a fixed/moved site must be pruned, so the list can
    only shrink. Every allowlist key must still match a current silent swallow."""
    live_keys = {
        (rel, func, exc_name)
        for rel, _lineno, func, exc_name in _iter_silent_broad_swallows()
    }
    stale = sorted(set(_SILENT_SWALLOW_ALLOWLIST) - live_keys)
    assert not stale, (
        "Stale _SILENT_SWALLOW_ALLOWLIST entr(y/ies) no longer match any silent "
        "broad swallow (the site was logged, narrowed, moved, or removed). Prune "
        "them so the allowlist keeps shrinking honestly:\n"
        + "\n".join(f"  {r} :: {f} :: {e}" for r, f, e in stale)
    )


def test_detector_has_teeth():
    """Meta-check: the detector must FLAG a silent broad swallow and must NOT flag
    a handler that logs or one that is narrowly typed. Proves the guardrail would
    catch a regression rather than passing vacuously."""
    silent = (
        ast.parse("try:\n    f()\nexcept Exception:\n    return None\n")
        .body[0]
        .handlers[0]
    )
    bare = ast.parse("try:\n    f()\nexcept:\n    continue\n").body[0].handlers[0]
    logged = (
        ast.parse(
            "try:\n    f()\nexcept Exception as exc:\n    log.debug(exc)\n    return None\n"
        )
        .body[0]
        .handlers[0]
    )
    narrow = (
        ast.parse("try:\n    f()\nexcept ValueError:\n    return None\n")
        .body[0]
        .handlers[0]
    )

    assert _broad_exception_name(silent) == "Exception" and _is_silent_swallow(silent)
    assert _broad_exception_name(bare) == "bare" and _is_silent_swallow(bare)
    # Logged: still broad, but the log call means the body is not control-flow-only.
    assert _broad_exception_name(logged) == "Exception"
    assert not _is_silent_swallow(logged)
    # Narrow typed handler is never a broad swallow.
    assert _broad_exception_name(narrow) is None
