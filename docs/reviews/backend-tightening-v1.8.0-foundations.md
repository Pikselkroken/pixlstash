# Backend tightening pass — v1.8.0-foundations

**Mode:** Principal consult (advise-only). Scope: `pixlstash/` backend.
**Governed by:** `CLAUDE.md` (no-except/pass policy, import policy, class-member order), `docs/backend_architecture.md`.
**Altitude note:** This is largely *senior-lane* work — each item below is domain-local and reversible. The principal value here is the invariant clearance, the safe/unsafe split, and the priority order. Nothing below touches a load-bearing invariant (content-hash identity, closed AccessPolicy registry, push-only networking, new-tables-only migrations, WebSocket origin-in-data). No schema change, no new dependency, no PR over the 600-line limit.

## Out of scope (per brief — flag only)
- `pixlstash/authz/*` (gate/registry/membership) — security-sensitive, needs `chief-security-officer`. Not inspected for edits.
- In-flight uncommitted smart-score work (`pixlstash/vault.py` drain change, `tests/test_smart_score_invalidation.py`) — left alone.

## Counts by category
| Category | Count | Disposition |
|---|---|---|
| Redundant local import | 1 confirmed (12 other local-import sites reviewed → cleared as legit deferrals) | Greenlight |
| Exact local duplication | 1 | Greenlight |
| `except: pass` policy violations | 5 | Greenlight |
| Silent context-poor `except → return/continue` | 87 swept; majority idiomatic | **Senior triage — do NOT bulk-edit** |

---

## A. Greenlight — safe, contained, behavior-preserving

### A1. Redundant local `import numpy as np` — `pixlstash/db_models/quality.py:58`
`batch_likeness_scores` re-imports numpy inside the function; `np` is already imported at module top (`quality.py:2`).
- **Change:** delete line 58.
- **Safe because:** `np` is unconditionally available module-wide; no shadowing, no optional-dependency guard. Blast radius: the one function.
- **Behavior-preserving.** Effort: trivial.

### A2. Exact duplication of the relocation-rollback block — `pixlstash/routes/reference_folders.py:966–999`
The `except HTTPException` branch (966–982) and the `except Exception` branch (983–999) contain byte-identical rollback bodies — the `for destination, source in reversed(rollback_moves): shutil.move(...)` loop plus the `rmdir(new_root)` cleanup — differing only in the final statement (`raise` vs `raise HTTPException(...)`).
- **Change:** extract a local helper `_rollback_relocation(rollback_moves, destination_existed, new_root)` holding the shared body; each `except` branch calls it, then performs its own raise.
- **Safe because:** contained to the relocation handler; both branches already do exactly the same cleanup; the differing raise stays in the branch. Blast radius: one function.
- **Behavior-preserving.** Effort: small. (Also collapses two of the A3 `pass` sites into one.)

### A3. `except → pass` — 5 sites violate the standing no-except/pass policy
Policy (`CLAUDE.md`): a swallowed exception must be logged with context, never silently `pass`ed. All five are benign best-effort cleanups, so the fix is a contextual log, not control-flow change.

| Site | Context | Recommended log |
|---|---|---|
| `listeners.py:183` | `except NotImplementedError` — Windows asyncio has no `add_signal_handler` (comment already documents it) | `logger.debug(...)` with the platform note |
| `services/snapshot_service.py:582` | `conn.close()` in `finally` | `logger.debug("failed to close snapshot conn %s", abs_snapshot, exc_info=True)` |
| `routes/reference_folders.py:980` | best-effort `rmdir(new_root)` during rollback | `logger.debug(...)` |
| `routes/reference_folders.py:997` | same (duplicate of 980 — removed by A2) | folds into A2 |
| `routes/reference_folders.py:1003` | `rmdir(old_root)` after a *successful* relocation | `logger.warning(...)` — a leftover old root is a real, diagnosable condition, not benign |

- **Safe because:** each only adds a log line; no path changes. Blast radius: local. **Behavior-preserving.** Effort: trivial each.

---

## B. Senior triage — flag, do NOT bulk-edit

### B1. 87 silent `except → return / continue` (AST-swept, `authz/*` excluded)
The majority are **legitimate idioms**, and blanket-logging them would be noise and its own regression:
- `except queue.Empty → break/continue` — non-blocking queue drain (`task_runner.py`, `database.py`).
- `except PackageNotFoundError → continue` — optional-dependency probing (`startup_checks.py`, `server.py`).
- `except ValueError → return` — parse-and-reject validators (`auth.py:162/188/222`, several routes) where the return value *is* the "invalid" signal.

A **subset** swallow a genuine broad `except Exception: return` inside task/service code (e.g. `tasks/*_task.py`, `services/config_service.py`) where a real error should be logged with context per policy.
- **Recommendation:** a senior-backend pass that (a) logs the genuine-error swallows with context, and (b) leaves a one-line annotation on the deliberate idioms so the next sweep doesn't re-flag them. Per-site judgment required; not mechanical.

---

## C. Cleared — verified legit, no change
Local imports of heavy/optional libs are permitted by `CLAUDE.md`'s stated exceptions (startup-time reduction / clearly optional): `services/config_service.py:122` (torch), `startup_checks.py:18` (torch), `inference/engine.py:487` (torch), and the plugin/inference PIL/cv2 imports. Only `quality.py:58` (A1) was an actual redundant local import of a module-top library.
