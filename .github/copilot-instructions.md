# Copilot Instructions for PixlStash

## Patch Reliability Policy

- **Always read file context before making changes:**
  - Before generating or applying any code patch, you must always read enough lines around the target location (and at very least 20 lines before and after) to fully understand the code structure, logic, and dependencies.
  - Never create or apply a patch without first inspecting the relevant file context.
  - If the code region is ambiguous, read additional context until the correct placement is certain.
  - You must always check a patch for illogical and abrupt changes that don't fit the surrounding code.
  - An example of an illogical change is a class method being placed outside of its class definition or code being placed above the top import statements.
  - An illogical change is placing a class method above the class docstring or above the __init__ method, rather than after the docstring and any class-level variables. All methods must be defined within the class block, following the established order and indentation.
  - All schema upgrade steps in Alembic migrations must be placed in strictly increasing version order. Never insert a new migration out of sequence. This ensures upgrades are applied in the correct order and the code remains maintainable and logical.
  - Always ensure correct indentation and placement within the class block. Read surrounding code to confirm the proper structure.
  - Always ensure a blank line between top-level functions and class definitions
  - For any class the code order must be:
    1. import statements
    2. Class definition
    3. Class docstring (always in Google style)
    4. Class-level variables
    5. __init__ method including initialisation of object properties
    6. Properties (getters/setters)
    7. Public methods in logical order
    8. Private methods in logical order
## Project Architecture

- **Backend:** Python FastAPI server (`pixlstash/server.py`), with core logic in `pixlstash/routes`.
- **Frontend:** Vue 3 + Vite app in `frontend/`.
- **Database:** SQLite (`vault.db`), schema managed via Python.
- **Utility functions:** Utilities should be placed in `pixlstash/utils`.
- **Tasks:** Long-running tasks (e.g., quality calculation) are handled asynchronously with the TaskRunner class in `pixlstash/task_runner.py`.

## Imports
- Mostly use imports at the top of the file. Local imports within functions are only acceptable if they are necessary to avoid circular dependencies, to reduce startup time for rarely used modules or if the import is *clearly* optional.
- Do not use local imports for libraries that are commonly used in the code base, like torch, numpy, PIL, cv2, etc. These should be imported at the top of the file for clarity and consistency.

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
- Always create a new migration file for each schema change, with a descriptive name.
- The Alembic revision identifier variables (`revision`, `down_revision`, `branch_labels`, `depends_on`) are read by Alembic at runtime via module import, not by explicit code references. Declare them as exported by including `__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]` after the `depends_on` line. This prevents false "unused variable" warnings from static analysers (including CodeQL) without needing `# noqa` comments. The script template (`migrations/script.py.mako`) already includes this line, so new migrations will have it automatically.
- When a code change requires existing data to be regenerated (e.g. tags, embeddings, quality scores), trigger reprocessing by resetting the relevant column(s) to `NULL` in the Alembic migration script. The `Missing*Finder` classes in `pixlstash/tasks/` query for pictures with `NULL` values and will automatically pick up those rows for reprocessing when the server next runs. Alembic migrations should only contain schema changes and this kind of targeted `NULL`-reset; no application logic should be placed in migrations.
## Developer Workflows

- **Install dependencies:** `pip install -e .`
- **Run server:** `python -m pixlstash.server`
- **Run tests:** `python -m pytest -s -vvv --fast-captions --force-cpu`
- **Check formatting:** `ruff check pixlstash`
- **Build frontend:** `npm run build` (in `frontend/`)
- **Dev frontend:** `npm run dev` (in `frontend/`)

## Conventions & Patterns

- **Batching:** Group images and face crops by size for efficient quality calculation.
- **Error Handling:** Always set metrics to -1.0 if calculation fails; log detailed warnings for OpenCV errors (file path, bbox, crop shape, error).
- **Database Updates:** Log before updating metrics; ensure all metrics are set to avoid repeated selection.
- **Bounding Boxes:** Clamp to image edges before cropping/resizing.

## Integration Points

- **External:** Uses OpenCV, NumPy, PIL, FastAPI, rapidfuzz, and Vue 3.
- **Cross-component:** Backend serves REST API; frontend consumes API and displays images/metrics.

## Example: Reliable Patch Workflow

1. Read 20-50 lines around the target code region.
2. Confirm logic, dependencies, and placement.
3. Generate patch only after context is clear.
4. If unsure, read more or ask for clarification.

---

*These instructions are enforced for all AI coding agents working in this repository. Update this file to refine agent behavior as needed.*
