# Copilot and Claude Instructions for PixlStash

## Patch Reliability Policy

- **Read before you edit.** Read enough surrounding context (at least 50 lines before and after the target) to understand structure, logic, and dependencies before generating a patch. If placement is ambiguous, read more until it is certain.
- **Don't assess what you haven't read.** Never critique, judge, or make claims about the adequacy of a file, document, or module you have not actually read. Read it first, or explicitly scope your statement to what you did read and flag the gap.
- **Reject illogical edits.** Check every patch for abrupt changes that don't fit the surrounding code — e.g. a method placed outside its class, code inserted above the top imports, or a missing blank line between top-level definitions.
- **Class member order:** imports → class definition → Google-style docstring → class-level variables → `__init__` (including property initialisation) → properties (getters/setters) → public methods → private methods. Keep everything correctly indented within the class block.

## Project Architecture

Use the following architecture documents depending on the scope of the task:

1. Frontend tasks:
   Always read and follow `/docs/frontend_architecture.md`.

2. Backend tasks:
   Always read and follow `/docs/backend_architecture.md`.

3. Full‑stack tasks (involving both frontend and backend):
   Read and follow both documents plus `/docs/integration_architecture.md` for guidance on how to integrate frontend and backend changes effectively.

Task classification rules:
- If the task involves UI, components, state management, routing, or client-side logic → treat it as a frontend task.
- If the task involves APIs, storage, indexing, ML pipelines, or server-side logic → treat it as a backend task.
- If the task touches both (e.g., API changes that require UI updates) → treat it as a full‑stack task.

When making changes to architecture or integration patterns, always update the relevant documentation to reflect the new approach. This ensures that all future work follows the updated architecture and maintains consistency across the codebase.


## Skill Delegation

This repo ships role-specific **skills** — personas with their own expertise (and, for the developer roles, their own subagents). Route work to the skill that owns the domain instead of doing everything in one generalist pass. This extends the frontend/backend/full‑stack classification above; check the available-skills list at the start of each session, because the set can grow.

### Who owns what

| Task | Skill |
|---|---|
| Backend code: Python, FastAPI, SQLModel/SQLAlchemy, Alembic, async/concurrency, data models, observability | `senior-backend-developer` |
| Routine backend work that copies an existing pattern (mirror a CRUD endpoint, add a field + migration, straightforward tests, obvious bugfix, type hints/docstrings) | `junior-backend-developer` |
| Frontend code: Vue 3, JS, HTML, CSS, state/data flow, routing, browser/CORS/CSP issues, rendering | `senior-frontend-developer` |
| Routine frontend work that mirrors an existing component (presentational component, props/emits, simple layout fix, basic route/computed, a11y attributes, copy) | `junior-frontend-developer` |
| ML: training/fine-tuning, model eval, embeddings, captioning, quality scoring, architecture/dataset choices | `machine-learning-expert` |
| ComfyUI graphs, nodes, model selection, generation/upscale/inpaint pipelines | `comfyui-workflow-wizard` |
| CI/CD, GitHub Actions, pipeline speed/flakiness, release automation, the `pixlstash-metrics` collector | `ci-expert` |
| Security review of a diff/PR/codebase, secret hunting, dependency audit, API/deploy/demo hardening, threat modeling | `chief-security-officer` |
| Product strategy, roadmap, build‑vs‑cut, metrics interpretation, monetization, investor/fundraising narrative | `chief-executive-officer` |
| Marketing & growth: Reddit/YouTube/Discord/forums, pixlstash.dev content, adoption tactics | `chief-marketing-officer` |
| Deep, multi-source, fact-checked research | `deep-research` |

Senior vs. junior: the senior decides and delegates; the junior only takes work that already has a clear pattern to copy and **escalates anything non‑trivial** instead of guessing.

### Handing a task to a skill

- **Single domain, advisory, or you'll do it inline:** invoke the skill in this conversation (the `Skill` tool, or a `/skill-name` slash command). It loads that persona's expertise into your context for the rest of the task.
- **Self-contained chunk, or a search/implementation-heavy job you don't want filling your context:** spawn a subagent (the `Agent` tool) and have it invoke the skill, then report back. Keeps the main context clean.
- Always pair the skill with its architecture doc: frontend → `docs/frontend_architecture.md`, backend → `docs/backend_architecture.md`, full‑stack → both + `docs/integration_architecture.md`.

### Splitting one task across several skills (in parallel)

Decompose by domain first, then fan out. **Independent** sub-tasks should run concurrently — issue all the subagent calls in a **single message** so they execute at once; **dependent** ones run in sequence.

- **Full‑stack feature** → split at the API boundary: `senior-backend-developer` (endpoint/model/migration) and `senior-frontend-developer` (UI/state) in parallel, then reconcile the contract against `docs/integration_architecture.md`. Each senior hands its routine sub-parts to the matching junior.
- **Honour the built-in escalation chains:**
  - Seniors spawn juniors for mechanical sub-work (`senior-backend-developer` → `junior-backend-developer`; `senior-frontend-developer` → `junior-frontend-developer`).
  - `ci-expert` must clear any workflow/CI change with `chief-security-officer` before it is pushed.
  - `chief-executive-officer` drives the execution skills — it tasks `ci-expert` (metrics/pipelines) and `chief-marketing-officer` (growth).
- **Gate, don't parallelize, the safety steps.** Anything touching auth, secrets, external exposure, dependencies, deploys, or CI must pass `chief-security-officer` review (or `/security-review`) **before merge/push** — that is a barrier *after* the implementation work, not a concurrent lane.
- When you fan out, give each skill a tightly-scoped brief and reconcile their outputs yourself. Don't let parallel agents edit the same files; split by file/area or sequence the overlap.


## Imports
- Mostly use imports at the top of the file. Local imports within functions are only acceptable if they are necessary to avoid circular dependencies, to reduce startup time for rarely used modules or if the import is *clearly* optional.
- Do not use local imports for libraries that are commonly used in the code base, like torch, numpy, PIL, cv2, etc. These should be imported at the top of the file for clarity and consistency.

## Exception handling
- Always log exceptions with as much context as possible (e.g., variable values, file paths, operation being performed) to facilitate debugging.
- Avoid silent failures. If an exception is caught, it should either be handled in a way that resolves the issue or logged with sufficient detail to understand the impact.
- Using `pass` to ignore exceptions is not acceptable. If you need to ignore an exception, you must log it with a warning or error level log message explaining why it is being ignored and what the potential implications are.

## Task System

- The TaskRunner class manages asynchronous tasks, allowing for background processing of image quality calculations and other operations without blocking the main server thread.
- Work is first found using the WorkPlanner which has multiple WorkFinders registered to find different types of work (e.g., quality calculation, metadata extraction).
- Once work is found a new Task for a batch of images is created and added to the TaskRunner's queue.
- The TaskRunner continuously processes tasks from the queue, executing the associated work function, reporting progress and handling results.

## Fixing bugs and default error resolution approach
- NEVER assume a fix without understanding the root cause.
- ALWAYS read error messages carefully and check stack traces to identify the source of the error.
- NEVER apply fallback-based fixes unless I explicitly approve them in this conversation.
- REQUIRED debugging sequence: reproduce issue → isolate root cause → implement direct fix → validate with tests/log evidence.
- Fallbacks are LAST RESORT only, not a default strategy.
- If a fallback is approved and necessary, implement it so it does not mask the underlying issue and includes clear logging for future resolution.
- If you cannot resolve the root cause, document findings, blockers, and attempted fixes, then ask for direction instead of applying an unverified workaround.

## Alembic migrations
- Give every migration a descriptive name. The baseline rule is one new migration file per schema change, but **the branch decides how strictly to apply it:**
  - **Feature branch (schema still in flux):** it's fine to amend, squash, or merge migrations rather than stacking multiple migrations for the same change. Keep the migration history tidy before it lands.
  - **`main` branch:** strict patterns apply. A migration on `main` must never be modified; all subsequent schema changes go in new migration files. (Reason: anything on `main` may already have been deployed and run, so altering an existing migration would leave those databases divergent.)
- Place schema upgrade steps in strictly increasing version order; never insert a migration out of sequence, so upgrades always apply in the correct order.
- The Alembic revision identifier variables (`revision`, `down_revision`, `branch_labels`, `depends_on`) are read by Alembic at runtime via module import, not by explicit code references. Declare them as exported by including `__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]` after the `depends_on` line. This prevents false "unused variable" warnings from static analysers (including CodeQL) without needing `# noqa` comments. The script template (`migrations/script.py.mako`) already includes this line, so new migrations will have it automatically.
- When a code change requires existing data to be regenerated (e.g. tags, embeddings, quality scores), trigger reprocessing by resetting the relevant column(s) to `NULL` in the Alembic migration script. The `Missing*Finder` classes in `pixlstash/tasks/` query for pictures with `NULL` values and will automatically pick up those rows for reprocessing when the server next runs. Alembic migrations should only contain schema changes and this kind of targeted `NULL`-reset; no application logic should be placed in migrations.
- **All `op.add_column` calls must be conditional.** Always use `sa.inspect(op.get_bind())` to fetch existing columns and skip the `add_column` if the column already exists. The baseline migration (`0001_baseline`) uses `SQLModel.metadata.create_all()`, which creates tables with all current model columns; later migrations that blindly run `ALTER TABLE … ADD COLUMN` will therefore fail on a fresh database. The standard pattern is:
  ```python
  bind = op.get_bind()
  inspector = sa.inspect(bind)
  existing_cols = {col["name"] for col in inspector.get_columns("<table>")}
  if "<column>" not in existing_cols:
      op.add_column("<table>", sa.Column(...))
  ```
## Developer Workflows

- **Install dependencies:** `pip install -e .`
- **Run server:** `python -m pixlstash.server`
- **Run tests:** `python -m pytest -s -vvv --fast-captions --force-cpu`
- **Check formatting:** `ruff check pixlstash`
- **Build frontend:** `npm run build` (in `frontend/`)
- **Dev frontend:** `npm run dev` (in `frontend/`)

## Reviews

If asked to do a review on a branch, write the review into docs/reviews/NAME_OF_BRANCH.md

## Conventions & Patterns

- **Throughput & batching:** Always think about throughput and concurrency. Evaluate whether a piece of work is best handled as a batch following ML best practices — for images this usually means sorting and grouping by size so each batch is composed of equally-sized tensors (e.g. image and face-crop quality calculation).
- **Error Handling:** Always set metrics to -1.0 if calculation fails; log detailed warnings for OpenCV errors (file path, bbox, crop shape, error).
- **Database Updates:** Log before updating metrics; ensure all metrics are set to avoid repeated selection.
- **Bounding Boxes:** Clamp to image edges before cropping/resizing.

## Integration Points

- **External:** Uses OpenCV, NumPy, PIL, FastAPI, rapidfuzz, and Vue 3.
- **Cross-component:** Backend serves REST API; frontend consumes API and displays images/metrics.

---

*These instructions are enforced for all AI coding agents working in this repository. Update this file to refine agent behavior as needed.*
