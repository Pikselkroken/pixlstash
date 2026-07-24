"""Except-hygiene guardrail (ratchet).

Enforces the CLAUDE.md exception-handling policy for background code: a *broad*
exception handler (``except Exception``/``except BaseException``/bare ``except``)
inside ``pixlstash/tasks`` or ``pixlstash/services`` must NOT silently swallow the
error. "Silently swallow" means the handler body is *only* control flow —
``return``/``continue``/``break``/``pass`` — with no logging call and no re-raise,
so an unexpected failure vanishes with no trace.

This runs in AUDIT MODE with a closed allowlist of deliberate best-effort swallows
(see ``_SILENT_SWALLOW_ALLOWLIST``). The allowlist may only SHRINK: adding a log
line (the fix) removes a site from it, and the stale-entry check forbids leaving a
fixed site parked here. A NEW silent broad swallow fails immediately.

The allowlist was seeded from the 17 broad task/service swallows found by the
B1 triage sweep (docs/reviews/backend-except-triage-plan.md, Stage 0). Stage 1
logged 15 of them and burned those out of the list; the 2 survivors below are
genuinely intentional and carry a one-line justification at the call site.

Scope is deliberately limited to ``tasks/`` and ``services/`` for now; ``core``
(database.py, task_runner.py, ws/broadcaster.py, …) is Stage 2 and will extend
this guardrail once triaged.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TASKS_DIR = REPO_ROOT / "pixlstash" / "tasks"
SERVICES_DIR = REPO_ROOT / "pixlstash" / "services"
_SCAN_DIRS = (TASKS_DIR, SERVICES_DIR)

# Statements that count as pure control flow. A handler whose body is composed
# ONLY of these does no work and logs nothing — it silently swallows.
_CONTROL_FLOW = (ast.Return, ast.Continue, ast.Break, ast.Pass)

# Allowlist key: (relative_posix_path, enclosing_function_name, exception_name).
# Chosen to resist line drift (adding/removing lines above a site does not move
# the function name or exception type). The two survivors live in distinct
# functions, so the key is unambiguous for every current entry.
#
# {key: justification}. deny-by-default: each entry is a decision someone owns.
# This list may only shrink — log the swallow and delete its entry.
_SILENT_SWALLOW_ALLOWLIST = {
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
}


def _broad_exception_name(handler: ast.ExceptHandler) -> str | None:
    """Return the broad-handler name, or None if the handler is narrow.

    'bare' for a bare ``except:``; 'Exception'/'BaseException' for those catch-all
    types (including a tuple that contains one). Narrow handlers return None and
    are ignored — a typed ``except ValueError`` return IS a valid reject signal.
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
    """Yield (rel_path, lineno, func_name, exc_name) for each silent broad swallow."""
    for directory in _SCAN_DIRS:
        for path in sorted(directory.rglob("*.py")):
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


def test_no_unlisted_silent_broad_swallow_in_tasks_or_services():
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
        "Broad exception handler(s) in tasks/ or services/ swallow the error with "
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
