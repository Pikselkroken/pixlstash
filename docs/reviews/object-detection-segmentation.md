# Security review — Object detection → bounding boxes

- **Reviewer:** Chief Security Officer (independent; did not author the feature)
- **Date:** 2026-06-21
- **Branch / state:** `develop`, feature in the working tree (uncommitted)
- **Scope:** the two new detection endpoints, the modified thumbnails/export data
  paths that now carry detection data, and the supporting task/model/migration/
  inference code listed below.
- **Discipline applied:** coverage matrix (not a findings list), full input-space
  trace of each touched endpoint, deny-by-default, tests asserted both directions.
  Per `CLAUDE.md` §"Security & authorization review process" / §"Endpoint scope
  enforcement (HARD REQUIREMENT)" and `docs/backend_architecture.md` §16.

---

## VERDICT: SIGN-OFF (re-verified 2026-06-21 — see the Re-verification section at the end)

> **Status update (2026-06-21):** The original verdict below was **BLOCK** on a
> single blocker, **B1**. The author has since landed the cap. I re-verified B1
> adversarially (cap constant, check placement, dedup-bypass, the new test) and
> it is genuinely resolved. The current verdict is **SIGN-OFF**. The original
> analysis is preserved verbatim below for the record; the Re-verification
> section at the end of this document records exactly what I re-checked.

---

## ORIGINAL VERDICT (2026-06-21, superseded by the SIGN-OFF above): BLOCK

One blocker. It is not an authorization hole (the scope story is clean and
verified both directions). It is a missing input bound that every sibling batch
endpoint in this file already enforces, on a path that enqueues GPU work.

**Blocker list:**

1. **B1 — No cap on `picture_ids` in `POST /pictures/detect`** (resource
   exhaustion / DoS). The sibling `POST /pictures/character_likeness/batch`
   caps at `BATCH_CHARACTER_LIKENESS_MAX_IDS = 1000` and returns 422 over the
   limit; `detect_pictures` accepts an unbounded list and enqueues a single GPU
   task over all of it. Add an equivalent cap. Details below.

Everything else is a SIGN-OFF. Fix B1 (a few lines, mirror the existing
constant) and this merges.

---

## Coverage matrix

Every endpoint that returns or mutates detection-derived resource data, with the
scope state (a = calls the chokepoint, b = documented exempt) and the exact line
the check sits on. No empty cells.

| # | Endpoint | Method | Returns/mutates resource data? | State | Check location | Covers all return paths? |
|---|----------|--------|-------------------------------|-------|----------------|--------------------------|
| 1 | `/pictures/detect` | POST | Mutates (enqueues per-picture detection; echoes accepted ids) | **(a)** | `fetch_scope_allowed_picture_ids(server, request)` at `_crud.py:786`, filters ids and 403s on empty intersection **before** the DB read (`_crud.py:810`) and task submit | Yes — single path; the only success `return` (`_crud.py:838`) and the 403/404/503 branches are all downstream of the filter |
| 2 | `/pictures/{id}/detections` | GET | Returns `Detection` rows | **(a)** | `enforce_picture_scope(server, request, pic_id)` at `_crud.py:1880`, immediately after id parse, **before** `fetch_detections` (`_crud.py:1882-1885`) | Yes — one return path, check sits in front of it |
| 3 | `/pictures/thumbnails` (now also returns `detections`) | POST | Returns per-image detections mapped to thumbnail space | **(a)** | `fetch_scope_allowed_picture_ids` at `_thumbnails.py:315` filters `ids` before `Picture.find` (`_thumbnails.py:390`); detections are loaded off the already-scoped `pics` | Yes — both result branches (`_thumbnails.py:497` / `:509`) iterate only the scoped `pics` |
| 4 | Export ZIP detection sidecars (`coco-json` / `ideogram-json`) | GET (async ZIP build) | Writes detection data into the ZIP | **(a)** | `fetch_scope_allowed_picture_ids` at `export_utils.py:404-406` filters `pics`; the detection pre-fetch at `:444` queries `Detection.picture_id.in_([pic.id for pic in pics])` from the already-scoped list | Yes — pre-fetch and per-image sidecar writes run after the scope filter |

Middleware-layer state for endpoint 1:

| Endpoint | In `READ_SAFE_POST_PATHS`? | Effect on a READ token |
|----------|---------------------------|------------------------|
| `POST /pictures/detect` | **No** (`auth.py:79-96`) | A READ-scoped token POSTing here is rejected **403 "Token is read-only"** by the middleware non-GET block (`auth.py:1584-1592`) before the handler runs. Asserted by `tests/test_detections_scope.py::test_detect_endpoint_not_in_read_safe_post_paths`. |

---

## Adversarial verification — what I tried and what held

### Endpoint 1 `POST /pictures/detect` — input-space trace

Handler `detect_pictures` (`_crud.py:766-843`). Order of operations:

1. `origin_client_id = getattr(request.state, "origin_client_id", None)` — echo
   only, never used for authz (see §origin_client_id below).
2. `picture_ids` validation: non-list / empty → 400; non-int / non-positive
   filtered out via `{int(pid) for pid in raw_ids if pid is not None and int(pid) > 0}`;
   empty after coercion → 400. **Tried:** `picture_ids: "all"` (string) → 400 via
   the `isinstance(list)` guard. `[0, -1, "x", null]` → coerced/dropped, 400 if
   nothing survives. `[1.9]` → `int(1.9)=1` (float ids silently truncate, cosmetic
   only). No crash path found.
3. `prompt`: `payload.get("prompt") or ""`, coerced to `str` if not a string,
   `.strip()`. **Tried:** `prompt` as a dict/list → `str(...)` stringifies it, no
   crash. Non-string is handled.
4. **Scope filter** (`_crud.py:786`): `fetch_scope_allowed_picture_ids` returns
   `None` for owner/unscoped (no filter), a fail-closed set for scoped tokens, an
   empty set for an unrecognised `resource_type`. In-scope ids kept; empty
   intersection → **403**. This is on the single path before any DB read.
5. Engine availability → 503; `fetch_pictures` (DB read, **after** the scope
   filter) → 404 if none; task submit → 503 if no runner.

**Branch that returns resource data before the check?** None found. The DB read
(`_crud.py:810`) and the 404 are both downstream of the scope filter, so a scoped
token never triggers a DB read or a 404 existence-oracle for out-of-scope ids.
The 404 "No pictures found" can only fire on already-in-scope ids, so it leaks
nothing across the scope boundary.

**Prompt injection into the local Florence model — assessed, low risk.** The
prompt is concatenated `f"<CAPTION_TO_PHRASE_GROUNDING>{phrase}"`
(`florence2.py` `detect_objects`) and fed to a local vision-language model that
only emits detection tokens. It has no tool access, no code execution, no data
egress; the worst an injected phrase does is steer what *the caller's own*
detection pass labels on *the caller's own* pictures. Output is parsed
structurally (`post_process_generation` → bboxes/labels), coordinates are numeric
and clamped to image bounds (`_parse_detections`, `florence2.py`), labels are
`str(...)`-coerced. No `eval`, no shell, no template render. Not a security
finding.

### Endpoint 2 `GET /pictures/{id}/detections` — ordering vs the catch-all

- Handler `get_picture_detections` (`_crud.py:1867`) parses the id (400 on
  garbage) then calls `enforce_picture_scope` (`_crud.py:1880`) **before** the
  single `fetch_detections` read. Owner/unscoped passes straight through; a
  scoped token outside the picture's grant gets 403. One return path, gated.
- **Registration order:** `/pictures/{id}/detections` is registered at
  `_crud.py:1857`, *before* the `/pictures/{id}/{field}` catch-all at
  `_crud.py:1897`. This is correct and required: FastAPI matches in registration
  order, so the specific `/detections` route must win, otherwise `detections`
  would fall into `{field}` and `get_picture_field` would try to read a
  non-existent `Picture.detections` *field* (it's a relationship, not a scalar
  field) — a 404/500, not a bypass. **Tried** the inverse worry: does the
  ordering leave the catch-all unguarded for anything? No. `get_picture_field`
  (`_crud.py:1913`) still calls `enforce_picture_scope` at `_crud.py:1928` before
  returning any field/thumbnail bytes. Both siblings are guarded; the ordering
  creates no unguarded path.

### Endpoints 3 & 4 — out-of-scope token cannot retrieve detections

- **Thumbnails:** `fetch_scope_allowed_picture_ids` filters the raw `ids` list at
  `_thumbnails.py:315-324` before `Picture.find` (`:390`). The new `detections`
  block (`:466-491` in the diff) iterates `getattr(pic, "detections", [])` on the
  *already-scoped* `pics`, so an out-of-scope id is never in `pics` and never
  contributes a detection entry. `include_deleted=True` / `include_unimported=True`
  widen *state* (deleted/unimported), not *scope* — the id-set was already
  narrowed by the scope filter. **Tried:** a scoped token requesting a mix of
  in/out ids gets thumbnails+detections only for in-scope ids; out-of-scope ids
  are dropped at `:317-324` and absent from the result map.
- **Export:** the scope filter at `export_utils.py:404-406` reduces `pics` to the
  in-scope set; the detection pre-fetch at `:444-459` runs *after* and queries
  only `[pic.id for pic in pics]`. So `coco-json` / `ideogram-json` sidecars are
  written only for in-scope pictures. The `bbox_mode` value itself is bounded to
  `{none, coco-json, ideogram-json}` (`export_utils.py`), forced to `none` for
  non-FULL exports — no injection via the mode string.

### `origin_client_id` — injection / XSS / spoofing

- Captured from the `X-Client-Id` header by `OriginClientMiddleware`
  (`utils/request_origin.py`), **length-capped at 200** (`MAX_CLIENT_ID_LENGTH`),
  dropped (not truncated) if longer, opaque, never logged at INFO, and the module
  docstring states it is "used ONLY for echo-matching, NEVER for authorization or
  scoping." Verified: the detection path uses it only as `task.params["origin_client_id"]`
  → echoed on the `CHANGED_PICTURES` WS event (`vault.py:752`). It is not a SQL
  param, not a path component, not rendered as raw HTML by the backend.
- **XSS:** none server-side; it travels as a JSON string value the SPA compares
  against its own id. (Frontend should treat it as data, not `v-html`; nothing in
  this diff renders it.)
- **Spoofing (low, accepted-by-design):** a client can send any tab's id and thus
  suppress *that tab's* "view changed externally" pill, or attribute a change to
  another tab. Blast radius: a cosmetic notification pill on the attacker's own
  account. No data access, no authz effect. Already documented as accepted in
  `request_origin.py`. **LOW / informational**, noted, not a blocker.
- **WS leak check:** the completion event carries `picture_ids`, but
  `_broadcast_ws_event` only delivers to owner connections
  (`server.py:1407` — `if not client.get("owner"): continue`). A resource-scoped /
  READ token connects but receives no vault-wide events, so the id list does not
  leak to a scoped connection. Pre-existing mechanism, correctly reused.

### Migration & model

- `Detection` (`db_models/detection.py`): `picture_id` FK with
  `ondelete="CASCADE"` (`:37`) + indexed; `label` indexed; unique constraint on
  `(picture_id, frame_index, detection_index)`. Picture relationship uses
  `cascade="all, delete-orphan"` + `passive_deletes=True` (`picture.py`),
  mirroring `Face`. No orphan rows on picture delete. `bbox` is stored as JSON
  text via a property; reads `json.loads` — no `eval`. The thumbnails path also
  tolerates a stringified bbox via `ast.literal_eval` (`_thumbnails.py:470`),
  which is safe (`literal_eval`, not `eval`).
- Migration `0061_add_detection.py`: guarded `create_table` (only on
  pre-existing DBs), matching columns, unique index. `down_revision` chains
  correctly to `0060`. No issue.

### Tests — both directions, both endpoints

`tests/test_detections_scope.py` asserts:
- POST not in `READ_SAFE_POST_PATHS` (`test_detect_endpoint_not_in_read_safe_post_paths`) — **real**, verified by reading `auth.py:79-96`.
- POST scoped token keeps only in-scope ids (`test_detect_scoped_token_filters_to_allowed_ids`).
- POST all-out-of-scope → 403 **and** `submit_task` must not run (`test_detect_all_out_of_scope_is_forbidden`).
- POST unscoped owner → full access (`test_detect_unscoped_owner_processes_all_ids`).
- GET both directions end-to-end through a real resource-scoped READ token +
  middleware: in-scope 200, out-of-scope 403, owner 200 (`test_get_detections_scope_both_directions`).

Supplementary: `tests/test_detection_model.py` covers FK cascade delete, bbox
round-trip, and the export sidecar schemas/scaling; `tests/test_detection_florence.py`
covers detection coordinate space. All changed/new Python files compile
(`py_compile` clean). (The suite was not executed here — pytest is not installed
in the available environment; verification was by reading the assertions and a
compile check.)

**Minor coverage gap (not blocking):** the POST scope tests patch
`fetch_scope_allowed_picture_ids` at the handler rather than driving a real READ
token through the middleware (the GET test does go end-to-end). The middleware
403 for a READ token on `/pictures/detect` is covered only by the static
allowlist assertion plus the shared middleware tests, not by an end-to-end POST.
Low value to add, but it would close the symmetry. **INFO.**

---

## Findings

### B1 — `POST /pictures/detect` has no cap on `picture_ids` — BLOCKER (medium severity, release-blocking by the sibling-parity rule)

**Class:** Uncontrolled resource consumption (CWE-770), OWASP API4 (Unrestricted
Resource Consumption).

**Where:** `pixlstash/routes/pictures/_crud.py` `detect_pictures` (`:766-843`).
After validation/scope-filtering, `picture_ids` of arbitrary length is handed to
`DetectionTask` (`:830`) and submitted as one GPU task. `DetectionTask._run_task`
(`tasks/detection_task.py`) loops over the whole list in chunks of up to 16 with
Florence-2 generation per chunk — bounded VRAM per chunk, but **unbounded total
work**.

**Reproduction:** as an authenticated owner (no scope filter applies),
`POST /api/v1/pictures/detect` with `{"picture_ids": [<every id in the library>]}`
enqueues a single detection task over the entire library — minutes-to-hours of
GPU time, blocking the GPU queue (the task is `TaskPriority.HIGH`, so it preempts
background tagging/embeddings). A scoped token over a large set/project can do
the same within its grant (thousands of ids).

**Why it blocks:** the immediate sibling in the same file,
`POST /pictures/character_likeness/batch`, enforces
`BATCH_CHARACTER_LIKENESS_MAX_IDS = 1000` and returns 422 over the limit
(`_crud.py:83`, `:1420-1427`). Detection is *more* expensive per image (a GPU
generation pass vs an embedding compare), so shipping it with *no* cap is a
strict regression against the established contract. This is exactly the kind of
inconsistency the coverage discipline exists to catch.

**Fix (small, mirror the sibling):**
- Add a module constant, e.g. `DETECT_MAX_IDS = 1000` (or lower, given the cost),
  next to `BATCH_CHARACTER_LIKENESS_MAX_IDS`.
- In `detect_pictures`, after coercing `picture_ids` and before/after scope
  filtering, `if len(picture_ids) > DETECT_MAX_IDS: raise HTTPException(422, ...)`
  with the same message shape as the batch endpoint.
- Add a test asserting the 422 over the cap (both raw and post-scope), mirroring
  the character-likeness batch test.

Mitigating context (why medium, not high): authenticated-only, and in today's
single-owner product the only caller with an unscoped token is the owner, so the
worst unscoped case is self-inflicted. But a delegated scoped token over a big
resource can still enqueue thousands, and the contract should bound input like
its sibling regardless. Fix before merge.

### Informational / low (no action required to merge)

- **I1 — origin_client_id spoofing (LOW, accepted-by-design).** A client can
  forge another tab's `X-Client-Id` to suppress that tab's change pill. Cosmetic,
  no authz/data effect, already documented in `request_origin.py`. No change.
- **I2 — POST middleware-403 not tested end-to-end (INFO).** See the coverage-gap
  note above. Optional hardening of the test suite.
- **I3 — Per-handler opt-in check (debt against §16.2 direction).** Both new
  endpoints add another per-handler `enforce_picture_scope` /
  `fetch_scope_allowed_picture_ids` call rather than a central declaration. This
  is correct under the current §16.1 hard requirement, but it is debt against the
  centralised-chokepoint target state (`backend_architecture.md` §16.2). Flagged
  per the review rule; not blocking — it follows the reference siblings verbatim,
  which is what §16.1 demands today.

---

## What I am signing off on (with the reference)

- **Authorization is clean, both endpoints, all four data paths.** The chokepoint
  is on the single path before any DB read/return in every case (matrix above),
  it fails closed for unrecognised scopes (`filter_helpers.py:285-290`,
  `_helpers.py:370-374`), and the tests assert both directions. No BOLA, no
  existence oracle, no sibling left unguarded. The recurring `/{id}/{field}`-style
  trap is avoided: the catch-all sibling is still guarded and the specific route
  is registered ahead of it.
- **READ-token write block is real** for `/pictures/detect` (not in
  `READ_SAFE_POST_PATHS`; 403 at the middleware).
- **No leak via thumbnails or export** — detection data is derived from the
  already-scope-filtered picture set in both.
- **Model/migration/relationship** are sound (cascade delete, indexes, JSON bbox,
  no `eval`).
- **Prompt injection into the local Florence model is not a meaningful vector**
  (local VLM, structured output, no execution/egress).

Fix **B1** and this is a clean merge.

— CSO, 2026-06-21

---

## Re-verification — B1 cap landed (2026-06-21)

- **Reviewer:** Chief Security Officer (same independent reviewer; follow-up pass)
- **Scope of this pass:** ONLY whether B1 (no cap on `picture_ids` in
  `POST /pictures/detect`, CWE-770 / OWASP API4) is correctly resolved. The rest
  of the feature already signed off and was not re-reviewed.
- **What changed:** the author added a `DETECT_MAX_IDS` cap and a test. I read
  the code and the test; I did not run pytest (not installed in this env), so the
  test is assessed by reading, consistent with the original pass.

### What I checked, adversarially

**1. The cap exists and mirrors the sibling.**
`pixlstash/routes/pictures/_crud.py:89` defines `DETECT_MAX_IDS = 1000`, sitting
right next to `BATCH_CHARACTER_LIKENESS_MAX_IDS = 1000` (`:83`) with a comment
explaining each id is a unit of GPU work. Same value, same 422 status, same
message shape ("picture_ids exceeds the maximum of N ids per request") as the
character-likeness batch endpoint (`:1431-1438`). Consistent with the sibling, as
B1's fix note required.

**2. The check fires BEFORE any work, and the bound cannot be exceeded.**
In `detect_pictures` the order is: read `picture_ids` and reject non-list/empty
with 400 (`_crud.py:777-781`) → **cap check `if len(raw_ids) > DETECT_MAX_IDS:
raise 422` (`_crud.py:782-786`)** → int/dedup coercion (`:787-795`) → empty-after-
coercion 400 (`:796-799`) → scope filter (`:809`) → DB read (`:832`) → task submit
(`:845`). The 422 is raised before the DB read and before `submit_task`, so no GPU
task is ever enqueued for an over-cap request.

The sharp edge I went looking for — **bypass via duplicate ids** — does not exist,
and the placement is actually stronger than the fix note suggested. The cap is
applied to `len(raw_ids)`, the **raw payload list length, before de-duplication**
(dedup happens in the `{int(pid) ...}` set comprehension at `:788`). Since the
working id set only ever shrinks from `raw_ids` (dedup, then drop non-positive,
then scope-filter), `len(picture_ids) <= len(raw_ids) <= DETECT_MAX_IDS` always
holds. There is no way to submit more than `DETECT_MAX_IDS` units of work, and no
way for dedup to "shrink under the cap" to sneak a larger raw list through,
because the check is on the raw length, not the deduped set. Capping the raw
length alone fully bounds the task; the note's "both raw and post-scope" is not
needed.

**3. No new issue introduced.**
- `len()` on the already-`isinstance(list)`-checked `raw_ids` is O(1) and cannot
  throw; no integer-overflow concern in Python; the check does not iterate the
  list, so the guard itself is not a DoS.
- The valid path is unchanged: a list of <= 1000 ids passes the check untouched
  and proceeds exactly as before; the existing scope tests (2-id lists) are
  unaffected.
- Body-size note (pre-existing, not a regression): the JSON array is still
  parsed before the cap, so a giant array is decoded before rejection. This is
  identical to the sibling batch endpoint and every other `Body(...)` handler in
  this file; it belongs at the ASGI/proxy body-size layer, not here. Not a B1
  concern and not introduced by this fix.

**4. The test is real, not a tautology.**
`tests/test_detections_scope.py::test_detect_rejects_too_many_ids` (`:184-196`):
- imports the real `DETECT_MAX_IDS` constant (so it tracks the cap, not a magic
  number);
- builds `list(range(1, DETECT_MAX_IDS + 2))` = exactly `DETECT_MAX_IDS + 1`
  positive ints (1001 when the cap is 1000), i.e. one over the boundary — it
  drives the off-by-one edge, not a wildly-over value;
- POSTs that body to the real endpoint via `TestClient` and asserts
  `status_code == 422`.
The ids are all valid positive integers, so they pass the empty/non-list 400
guard and the int-coercion 400 guard; the only statement that can produce a 422
on this input is the cap check. The test therefore isolates the cap and is not
satisfied by unrelated validation. Sound.

### Re-verification verdict: SIGN-OFF

B1 is genuinely resolved at `pixlstash/routes/pictures/_crud.py:89` (constant)
and `:782-786` (the 422 guard, placed before any DB read or task submit, on the
raw pre-dedup length so the bound is unbypassable). Cap value and message match
the sibling. The new test drives the boundary and asserts the 422 for real. No
new issue introduced; the valid path is intact. Nothing else was changed, and
the rest of the feature retains its original sign-off. **This merges.**

Residual (non-blocking, unchanged from the original pass): I1 (origin_client_id
spoofing, cosmetic, accepted), I2 (POST middleware-403 not tested end-to-end,
optional), I3 (per-handler scope opt-in is debt against §16.2). None gate the
merge.

— CSO, 2026-06-21
