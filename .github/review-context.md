# Review context for the AI reviewer

PixlStash: a FastAPI + SQLModel backend (`pixlstash/`) and a Vue 3 frontend
(`frontend/`). Loaded by `.github/workflows/ai-review.yml` from the pull
request's base branch.

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

## Public by design, do not report

- **The demo token** `o75qQ-w0fy_FraPb2sdxcGOVTBoKFmmZwStycljomSs` is a
  READ-scope share token for the public demo library at
  `https://demo.pixlstash.dev`. It is meant to be seen: it sits in the "Try
  demo" links (`README.md`, `website/index.html`, `website/features.html`,
  `website/nav.js`) and is prefilled in every published API reference
  (`website/api/v*/index.html`, written by `scripts/generate_openapi_docs.py`)
  so its requests run live. Do not ask to remove or rotate it. A finding is
  any other token string in those places, or this one given more than READ
  scope.

## Known non-findings, do not report

Each of these has come back across PRs and been wrong every time. None of them
excuses an authorization, data-loss or secret-exposure defect in `pixlstash/` or
`frontend/src/`: report those whatever this list says.

- **Code that does not exist.** A suggestion that names a function, class,
  config key or CSS selector must name one that exists in the repository.
  Check before suggesting it; a suggestion built on an invented API (a
  `paint-compare-after` element, a `run_task_now` method, a `needs_engine`
  attribute) is wrong whatever it is for.
- **The kind of confirm dialog.** Some flows confirm with the browser's own
  `confirm()`, others with an in-app dialog. Read the handler before saying a
  test drives the wrong one; for example, Restore all in the Scrapheap uses
  `confirm()`.
- **E2E cleanup on a failed test.** The e2e specs share one backend. A cleanup
  that only goes missing after an assertion has already failed is not a
  finding: the run is red either way, and later specs assert on identities, not
  counts. A spec that leaves state behind on a passing run is a finding.
- **Guards that fail loudly.** An assertion that checks a precondition and
  fails before acting (the Scrapheap holding only the test's own pictures,
  the library holding no workflows) is intended. Do not suggest turning it into
  `test.skip`, `test.fixme` or a softer check: that turns a broken fixture into
  coverage that silently stops running.

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
