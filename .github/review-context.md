# Review context for the AI reviewer

PixlStash: a FastAPI + SQLModel backend (`pixlstash/`) and a Vue 3 frontend
(`frontend/`). Loaded by `.github/workflows/ai-review.yml` from `main`.

## How to review

- Report only defects you can point at in the diff: a wrong result, a crash, a
  data-loss or security hole, or a broken rule below. Say what input breaks it.
- Skip style, naming, formatting and lint; ruff and CI cover them. Skip
  speculative "consider adding" advice and do not restate what the diff does.
- Few findings beat many. No findings is a valid review.

## Already enforced by CI, do not report

Formatting and lint; a route with no `ROUTE_POLICIES` entry; a test file not
gated in CI; private IP literals; unpinned action references; changelog
fragment format.

## Backend rules to check

- **Authorization.** Object access is enforced centrally from
  `ROUTE_POLICIES` (`pixlstash/authz/registry.py`). Check that a new or changed
  route's `AccessPolicy` fits what it returns: `PUBLIC` or `ANY_TOKEN` on a
  route that returns per-object data is a finding. Inline scope checks in a
  handler (`enforce_picture_scope`, `require_unscoped_owner`, `token_scope`
  ladders) are a finding. An authz change needs tests in both directions: the
  out-of-scope request refused and the in-scope one still allowed.
- **Siblings.** A fix to one endpoint or call site that leaves identical
  siblings unfixed is a finding; name them.
- **Migrations.** Every `op.add_column` is guarded by an inspector check for
  the existing column. The revision variables are exported with
  `__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]`.
  A migration already on `main` is never edited. Data to
  regenerate is reset to `NULL` so the `Missing*Finder` tasks pick it up;
  migrations hold no application logic.
- **Exceptions.** A caught exception is logged with context (paths, ids, the
  operation). `except ...: pass` or a silent fallback is a finding.
- **Imports** sit at the top of the file. A function-local import is only for a
  circular dependency or a heavy optional module, never torch, numpy, PIL or
  cv2.
- **Metrics.** A failed calculation stores `-1.0`, never leaves the value unset
  (that makes the row re-selected forever). Bounding boxes are clamped to the
  image before cropping.
- **Tests** reuse a module-scoped environment rather than building a `Server`
  per test, and assert on identity rather than global counts. A test that
  would still pass with the fix reverted is a finding.

## Frontend rules to check

- Use the tokens in `docs/design/design-tokens.css`: a hardcoded hex colour,
  raw `rgba()` shadow, off-grid spacing, ad-hoc radius or font size outside
  the type ramp is a finding.
- Picture image URLs are built with `pictureThumbnailUrl`
  (`frontend/src/api/pictures.js`), never a hand-written path.
- An overlay or dialog opened from code must return focus to its trigger when
  it closes.

## Repository rules

- `CHANGELOG.md` is never edited on a branch; user-visible changes add a
  `changelog.d/` fragment. A fragment for a refactor, test, CI or doc change is
  a finding.
- No real home paths or real addresses in code, tests or docs.
