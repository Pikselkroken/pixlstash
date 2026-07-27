# B1 — Silent `except → return/continue` triage plan

**Mode:** Principal DESIGN REVIEW (advise-only). **Scope:** `pixlstash/` (excl. `authz/*`).
**Governed by:** `CLAUDE.md` exception-handling policy (swallowed exceptions must be logged with context; no silent `pass`/swallow), `docs/backend_architecture.md`, and the existing guardrail-ratchet precedent (`tests/test_architecture_guardrails.py`, the closed-allowlist pattern that made authz coverage a machine fact).

## The evidence (AST sweep, authz excluded)
87 handlers whose body is only `return`/`continue`/`break` with **no** log and **no** re-raise:

| Bucket | Count | Nature | Default rule |
|---|---|---|---|
| `except Exception` (broad) | **47** | swallows *any* error — the policy target | **LOG with context** (unless documented-intentional) |
| `TypeError`/`ValueError`, `ValueError` | 19 | parse-and-reject validators — return **is** the "invalid" signal | **LEAVE**; annotate if re-flagged |
| `JSONDecodeError/*`, `KeyError/TypeError` | 5 | parse-reject | **LEAVE**; annotate |
| `OSError`, `OSError/ValueError` | 8 | filesystem best-effort | **LEAVE** or debug if diagnosable |
| `queue.Empty` | 5 | non-blocking queue drain | **LEAVE** (logging here is a bug) |
| `PackageNotFoundError` | 3 | optional-dependency probe | **LEAVE** |

The 47 broad handlers by area: **28 core** (`database.py`, `task_runner.py`, `vault.py`, `ws/broadcaster.py`, `startup_checks.py`, `auth.py`, `app.py`, plugin loaders), **9 task**, **8 service**, **2 route**.

### The 17 highest-priority broad swallows (task/service — hide real per-item processing errors)
`services/comfyui_service.py:89`; `services/config_service.py:108,139,160,204,213,220`; `services/restore/resource_restore.py:513`; `tasks/description_task.py:108`; `tasks/detection_task.py:96`; `tasks/face_extraction_task.py:277,403,867`; `tasks/image_embedding_task.py:151,240`; `tasks/missing_watch_folder_import_finder.py:71`; `tasks/tag_task.py:219`.

## Per-bucket decision rule
1. **Broad `except Exception` in task/service** → add `logger.warning/exception(...)` with the operation + the item identity in scope (picture id, path, engine name) and `exc_info=True`. **Never** change the `return`/`continue` itself — the background loop must keep surviving the error; we are only making the swallow visible.
2. **Broad `except Exception` in core** → per-site. Hot-loop / per-client / per-frame sites (`ws/broadcaster.py`, `task_runner.py` drain) log at **debug** or stay silent-by-design with a one-line comment + allowlist entry (documented intentional). Startup/one-shot sites log at warning.
3. **Narrow parse-reject / probe / drain buckets (40)** → **no code change**. Where a future sweep would re-flag them, add a terse trailing comment stating the deliberate swallow. These are correct; logging them is the regression.

## Staging (each stage = one small, single-purpose PR, well under the 600-line limit)
- **Stage 0 — ratchet first.** Add an AST guardrail test (sibling of `test_architecture_guardrails.py`): fail if a broad `except Exception` handler in `pixlstash/{tasks,services}` has a `return`/`continue` body with no logging call. Seed it with an explicit allowlist of the current 17 so CI is green on day one. The allowlist may only **shrink**. This converts the policy from "remembered" to "enforced" and is what actually prevents recurrence.
- **Stage 1 — task/service (17).** Log-only edits; delete each fixed site from the allowlist in the same PR. Split into two PRs (tasks / services) if the diff grows.
- **Stage 2 — core (28).** Per-site judgment; hot loops get debug or a documented allowlist entry. Extend the guardrail to `core` only after this stage lands, seeded with any deliberately-silent survivors.
- **Stage 3 — narrow buckets (40).** Comment-only annotations where useful; no behavior change. Optional; low value.

## Regression-avoidance & testing strategy
The danger is **not** functional test failure (these are log-only) — it is (a) accidentally altering control flow, (b) log-flooding a hot path, and (c) an unused `exc` binding.
- **Log-only discipline:** the only added lines per handler are the logging call (and `as exc` on the `except`). Reviewer diffs must show every `return`/`continue`/`break` line **unchanged**. Any control-flow delta fails review.
- **No hot-path flooding:** loop/`continue` sites use `debug` (or a rate-limited log); never `warning`/`exception` inside a tight drain or per-frame loop.
- **The guardrail test is the anti-regression mechanism**, not behavior tests — it catches both a *new* silent broad swallow and a *reverted* fix (allowlist can't grow).
- **Suite:** run `python -m pytest -s -vvv --fast-captions --force-cpu` after each stage; the diff being log-only, a green suite confirms no behavior drift. Spot-add one test only where a swallow hid a genuinely testable failure (e.g. a task that should now surface an error to its result/metrics).
- **Known baseline noise:** `test_smart_score_invalidation.py::test_penalised_tag_change_invalidates_atomically_no_second_task` fails pre-existing on this branch — not caused by this work.

## Reviewer verification (per PR)
1. Every touched handler adds **only** a log (+ `as exc`); no `return`/`continue`/`break` line changed.
2. The guardrail allowlist strictly **shrank** (or, Stage 0, was introduced with exactly the 17 seeds).
3. No `warning`/`exception`-level log added inside a tight loop; hot paths are debug/rate-limited.
4. Narrow parse-reject/drain/probe buckets are **untouched** (or comment-only).
5. `authz/*`, `vault.py` smart-score work, and migrations are untouched; full suite green; ruff clean.
