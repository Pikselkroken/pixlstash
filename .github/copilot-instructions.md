# Copilot Instructions for PixlVault

## Patch Reliability Policy

- **Always read file context before making changes:**
  - Before generating or applying any code patch, read enough lines around the target location to fully understand the code structure, logic, and dependencies.
  - Never create or apply a patch without first inspecting the relevant file context.
  - If the code region is ambiguous, read additional context until the correct placement is certain.
  - Check a patch for illogical and abrupt changes that don't fit the surrounding code.

## Project Architecture

- **Backend:** Python FastAPI server (`pixlvault/server.py`), with core logic in `pixlvault/` and `build/pixelurgy_vault/`.
- **Frontend:** Vue 3 + Vite app in `frontend/`.
- **Database:** SQLite (`vault.db`), schema managed via Python and bash scripts.
- **Image Quality:** Batch processing and metrics in `pixlvault/pictures.py` and `pixlvault/picture_quality.py`.

## Developer Workflows

- **Install dependencies:** `pip install -e .`
- **Run server:** `python -m pixlvault.server`
- **Run tests:** `python -m pytest -s -vvv --fast-captions --force-cpu`
- **Check formatting:** `ruff check pixlvault`
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
