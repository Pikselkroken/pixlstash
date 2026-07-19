# Security & authorization review — `develop` @ `92d4ef72..HEAD` (v1.7.0rc2)

**Reviewer:** chief-security-officer (independent — did not author this code)
**Date:** 2026-07-19
**Scope:** 12 commits, `92d4ef72..98807fb1`. 40 files, +4852/−383.
**Gate:** CLAUDE.md § *Security & authorization review process* + § *Endpoint scope
enforcement (HARD REQUIREMENT)*; `docs/backend_architecture.md` §16.1 / §16.2.

**Verdict: no release blocker introduced by this diff.** The primary suspect — the
new `locked_sets` / `twin_locked_sets` picture-set **names** in the review
suggestions payload — is not reachable by any resource-scoped token. Verified by
execution, not by reading. Two **pre-existing** disclosure findings and three
lower-severity issues are recorded below; none is a regression from this diff.

---

## 1. Coverage matrix

Every endpoint touched by, or whose response/behaviour is changed by, this diff.
Per CLAUDE.md the completeness claim is **arithmetic**: the diff touches exactly
two route modules (`routes/reviews.py`, `routes/tag_health.py`) and three service
modules whose output reaches three further pre-existing handlers
(`routes/tag_suggestions.py::list_tag_suggestions`,
`routes/pictures/_crud.py::get_picture_metadata`, and the write handlers in
`routes/tag_suggestions.py`). Enumeration below is over that closure. **No empty
cells.**

Legend — **(a)** calls the scope chokepoint; **(b)** explicitly scope-exempt.

### 1.1 `routes/reviews.py` — all state (b), owner-only

Every handler opens with the same gate, evaluated **before any DB read or
branch**:

```python
if _token_scope_ids(server, request) is not None:
    raise HTTPException(status_code=403, detail="Not available to this token")
```

`_token_scope_ids` (`reviews.py:163-165`) delegates to
`fetch_scope_allowed_picture_ids` (`utils/service/filter_helpers.py:222-290`),
which returns a `set` for every recognised `resource_type` and an **empty set**
(not `None`) for an unrecognised one — so the gate is fail-closed.

| # | Endpoint | State | Check location | New in diff? |
|---|---|---|---|---|
| 1 | `POST /reviews` | (b) owner-only | `reviews.py:189` | no |
| 2 | `GET /reviews` | (b) owner-only | `reviews.py:229` | no |
| 3 | `DELETE /reviews?status=` | (b) owner-only | `reviews.py:249` | **yes** |
| 4 | `GET /reviews/preview` | (b) owner-only | `reviews.py:283` | no |
| 5 | `GET /reviews/{id}` | (b) owner-only | `reviews.py:310` | no |
| 6 | `DELETE /reviews/{id}` | (b) owner-only | `reviews.py:332` | **yes** |
| 7 | `POST /reviews/{id}/refresh` | (b) owner-only | `reviews.py:353` | no |
| 8 | `POST /reviews/{id}/archive` | (b) owner-only | `reviews.py:377` | no |
| 9 | `POST /reviews/{id}/abort` | (b) owner-only | `reviews.py:393` | no |
| 10 | **`GET /reviews/{id}/suggestions`** | (b) owner-only | `reviews.py:427` | no (payload extended) |

Rows 3 and 6 are the new endpoints; both carry the gate, both placed before the
`status` validation and before any `review_service` call. Both are additionally
covered at the middleware layer: `DELETE` is non-GET and neither path is in
`READ_SAFE_POST_PATHS`, so a READ token is refused at `auth.py:1584-1592`
regardless. **Double-gated.**

Row 10 is the new-data row — `locked`, `locked_sets`, `twin_locked`,
`twin_locked_sets` (set **ids and names**) added at `review_service.py:846-853`.
See §2.1.

### 1.2 `routes/tag_health.py` — state (b), owner-only

| # | Endpoint | State | Check location | Note |
|---|---|---|---|---|
| 11 | `GET /tag_health` (+ `project_id` / `set_id` / `character_id` incl. `UNASSIGNED`) | (b) owner-only | `tag_health.py:139` via `_reject_scoped_tokens` (`:109-113`) | New `ground_truth` field |
| 12 | `POST /tag_health/rebuild` | (b) owner-only | `tag_health.py:158` | + non-GET READ block |

**Input-space trace (the `character_id=UNASSIGNED` lesson).** `get_tag_health`
has two return branches — the scoped live-compute branch and the cached
vault-wide branch (`tag_health.py:140-146`). `_reject_scoped_tokens(request)` is
on line 139, **above the `if`**, so it covers both. The `UNASSIGNED` literal
routes into `list_tag_health_scoped` inside the already-gated branch. Correct
application of the documented lesson. **CLEAN.**

### 1.3 Handlers whose behaviour changed indirectly

| # | Endpoint | State | Check location | What changed |
|---|---|---|---|---|
| 13 | `GET /pictures/{id}/metadata` | **(a)** `enforce_picture_scope(server, request, pic.id)` at `_crud.py:1084` | reference pattern, before all returns | `locked_by_sets` now computed via the new `locked_sets_for_pictures` |
| 14 | `GET /tag_suggestions` | (a)-equivalent (list filter) — `_resolve_review_picture_ids` at `tag_suggestions.py:256`, intersecting `fetch_scope_allowed_picture_ids` (`:214`) | before the service call | `list_suggestions` gained the locked-row read filter |
| 15 | `POST /tag_suggestions/{id}/accept` | (b) middleware non-GET READ block, `auth.py:1584` | — | unchanged |
| 16 | `POST /tag_suggestions/{id}/reopen` | (b) middleware non-GET READ block | — | unchanged; write guard intact (`tag_suggestion_service.py:498`) |
| 17 | `POST /tag_suggestions/{id}/fix_twin` | (b) middleware non-GET READ block | — | unchanged |
| 18 | `POST /tag_suggestions/{id}/swap` | (b) middleware non-GET READ block | — | unchanged |
| 19 | `POST /tag_suggestions/{id}/skip` | (b) middleware non-GET READ block | — | unchanged |
| 20 | `POST /tag_suggestions/{id}/dismiss` | (b) middleware non-GET READ block | — | unchanged |
| 21 | `POST /tag_suggestions/scan` | (b) middleware non-GET READ block | — | `scan_tag` lock predicate widened |
| 22 | `POST /tag_suggestions/bulk_accept`, `/bulk_reopen` | (b) middleware + `_resolve_review_picture_ids` | `:428`, `:470` | unchanged |

Rows 15-21 are state (b) **by the middleware, not by a per-handler decision** —
`scan_tag_suggestions` (`:406`), `skip_tag_suggestion` (`:489`) and
`dismiss_tag_suggestion` (`:502`) do not even take `request: Request`, so no
object check is possible in them. This is sound today only because
`create_token` forbids `ALL`+`resource_type` (`auth.py:915`) and the middleware
rejects any legacy row of that shape (`auth.py:1490-1501`), which makes "scoped"
and "READ" synonymous — so the non-GET block covers every scoped principal. That
equivalence is load-bearing and undocumented at these call sites. Recorded as
**F5** (hardening).

**Arithmetic completeness:** 22 endpoints in the closure, 22 filled cells.

---

## 2. Findings, worst first

### F1 — Locked set names disclosed to a token that cannot see the set (PRE-EXISTING, MEDIUM)

**Severity:** MEDIUM (information disclosure, CWE-200 / OWASP A01)
**Location:** `pixlstash/routes/pictures/_crud.py:1095-1098`; helper
`pixlstash/services/set_lock_service.py:141-148`
**Status: PRE-EXISTING — not introduced by this diff.** Verified against the base
commit (see Evidence).

**Exploit.** A `picture_set`-scoped READ share token for set *S* reads
`GET /api/v1/pictures/{id}/metadata` for a picture it is legitimately allowed to
see. If that picture is also a member of a **locked** set *L* — which the token
has no access to and cannot enumerate — the response's `locked_by_sets` field
returns *L*'s id and name. `enforce_picture_scope` authorizes the **picture**; it
says nothing about the **set names** attached to it.

**Evidence (reproduced).** With a READ token scoped to set "Scope", and the same
picture added to a locked set named `SECRET-Client-Q3`:

```
GET /api/v1/pictures/1/metadata  → 200
  locked_by_sets = [{'id': 2, 'name': 'SECRET-Client-Q3'}]
GET /api/v1/picture_sets         → 200
  visible = ['Scope']            # the token cannot enumerate set 2
```

The set list correctly scope-filters set 2 away; the metadata endpoint hands its
name over anyway. Set names are user-authored and routinely carry client,
project or subject identifiers.

**Why it is not a regression.** The base implementation
(`git show 92d4ef72:pixlstash/services/set_lock_service.py`) unioned set names
across *every* stack-expanded id (`for pairs in detail.values()`); the new
`locked_sets_for_pictures` rolls up over the same stack and keys by input id. The
disclosed set is identical. The diff refactored this helper; it did not widen it.

**Fix.** Filter the `locked_by_sets` payload through the token's visible-set
scope, or reduce it to a boolean + a generic reason string for scoped tokens.
The frozen-ness is legitimately useful to a scoped viewer; the *name* is not.

**Verification.** Test both directions: an owner sees the name; a set-scoped
token sees `locked: true` with no out-of-scope name. Assert the in-scope name is
still returned (over-blocking is its own regression).

---

### F2 — `GET /tag_suggestions` leaks out-of-scope twin picture attributes (PRE-EXISTING, MEDIUM)

**Severity:** MEDIUM (information disclosure, CWE-200 / OWASP A01)
**Location:** `pixlstash/routes/tag_suggestions.py:158-172` (`_serialize`),
`:272-285`; filter at `pixlstash/services/tag_suggestion_service.py:152-153`
**Status: PRE-EXISTING.** The diff modifies this handler's service function
(`list_suggestions`) but did not introduce the gap.

**Exploit.** `_resolve_review_picture_ids` intersects the token scope into
`picture_ids`, and `list_suggestions` applies it as
`TagSuggestion.picture_id.in_(picture_ids)` — the **suspect** only. The twin is
never filtered. A set-scoped READ token calling
`GET /api/v1/tag_suggestions?tag=X` therefore receives, for each in-scope
suspect: `twin_picture_id`, `twin_sim`, `twin_ext`, and
`twin_tagger_confidence` — the id, existence, file type, perceptual similarity
and model confidence of a picture outside its share. Iterating tags enumerates
picture ids across the vault.

**Evidence.** `tag_suggestion_service.py:152-153` filters `picture_id` only.
`_serialize` (`tag_suggestions.py:167-168`) emits `twin_picture_id` and
`twin_sim` unconditionally; the handler adds `twin_ext` (`:283`) and
`twin_tagger_confidence` (`:285`).

**This is the same risk the team already reasoned about — and closed — one module
over.** `reviews.py:423-426` states verbatim: *"the cards expose twin + up-to-k
neighbour picture ids and per-picture tag bits that routinely fall outside the
token's share scope"* — and that is precisely why `/reviews/{id}/suggestions` was
made owner-only. The identical reasoning was not carried to `/tag_suggestions`,
which remains scope-filtered on the suspect alone. This is the **decomposition
seam** CLAUDE.md warns about: the risk class was analysed per module rather than
per class.

**Fix.** Either null out twin fields whose `twin_picture_id` is outside
`picture_ids`, or make `/tag_suggestions` owner-only to match `/reviews`.

**Verification.** Scoped token + a suspect whose twin is out of scope → assert
every `twin_*` field is null. Owner → assert they are still populated.

---

### F3 — No regression test covers the three review **read** endpoints against a scoped token (MEDIUM, process)

**Severity:** MEDIUM (test-coverage gap on the exact new egress path)
**Location:** `tests/test_reviews_api.py:1090-1214`

`tests/test_reviews_api.py` adds seven scoped-token 403 tests —
`cannot_delete`, `cannot_clear_archived`, `cannot_create`, `cannot_preview`,
`cannot_refresh`, `cannot_archive`, `cannot_abort`. **All seven are writes.**
There is no scoped-token test for `GET /reviews`, `GET /reviews/{id}`, or
`GET /reviews/{id}/suggestions` — and the third of those is the endpoint this
diff extended with picture-set names. The one new data-egress path in the diff is
the one path with no negative test.

The gate itself is correct — I verified it by execution (§3) — so this is a
durability gap, not an open hole. But CLAUDE.md requires tests in **both**
directions across sibling vectors, and an untested gate is one refactor away
from being an untested hole.

**Fix.** Add `test_scoped_token_cannot_read_reviews` /
`_cannot_read_review_detail` / `_cannot_read_review_suggestions`, asserting 403,
and a positive owner-side assertion that `locked_sets` is still populated.

---

### F4 — Migration 0075 blanket-zeroes `ground_truth`, disabling "Start review" vault-wide until the next rebuild (LOW, availability/correctness)

**Severity:** LOW (self-healing denial-of-function; no security impact)
**Location:** `pixlstash/migrations/versions/0075_add_tag_health_ground_truth.py:52-56`;
gate at `frontend/src/components/reviews/tagHealthBoardLogic.js:98-102`

The frontend gate is explicitly designed to fail safe on old rows:

```js
// 2. A row that predates the `ground_truth` field (older cache) has it
//    `undefined`, which is `!== 0`, so the gate returns null and the button
//    stays enabled. Absence of evidence is never treated as evidence of
//    emptiness.
export function zeroYieldReason(r) {
  if (r.ground_truth !== 0) return null;
  if ((r.est_missing ?? 0) !== 0) return null;
  return ZERO_YIELD_TITLE;
}
```

That protection does not hold. The migration adds the column
`nullable=False, server_default="0"`, so every pre-existing row serializes
`ground_truth: 0` — a **placeholder**, not a measurement — never `undefined`. Any
tag whose row also has `est_missing == 0` is therefore reported as "zero yield"
and its Start-review button disabled, even where real ground truth exists. This
is exactly the false negative the comment says "this whole design exists to
avoid."

Window: bounded. `computed_at` is reset to the epoch, `is_stale` returns true,
and `TagHealthAutoRebuildFinder` (`tasks/tag_health_auto_rebuild_finder.py:63-82`)
queues a rebuild within its 300 s debounce, restoring real counts.

**No thundering herd, no DoS.** Asked and answered: the finder is debounced at
`AUTO_REBUILD_CHECK_INTERVAL_S = 300.0`, declares `max_inflight_tasks() == 1`,
skips when `get_status(...)["building"]` is true, and `start_rebuild` is
idempotent. Single-process, single-vault. The epoch reset triggers **exactly one**
backgrounded rebuild on a large vault, progress-reported, off the request path.
That is the correct design.

**Migration hygiene otherwise CLEAN:** `add_column` inspector-guarded
(`:47-51`), table-existence guarded (`:43-45`), `__all__` present (`:36`),
`down_revision = "0074_recompute_tag_health_exclude_human_decisions"` matches the
head, no application logic, `downgrade` symmetric and guarded.

**Fix.** Make the column nullable with no default (so pre-rebuild rows serialize
`null` and the frontend's documented `undefined` fallback engages), or have the
frontend additionally require a non-epoch `computed_at` before gating.

---

### F5 — The reviews/tag_health owner gate is a reimplementation, not the owner check (LOW, hardening)

**Severity:** LOW (policy/enforcement mismatch; no out-of-scope data disclosed)
**Location:** `reviews.py:163-165` + 10 call sites; `tag_health.py:109-113`

These handlers are documented "Owner-only" but the predicate is
`fetch_scope_allowed_picture_ids(...) is not None` — *"is this token restricted
to a resource?"*, which is not the same question. A vault-wide READ token
(`scope="READ"`, `resource_type=None`) yields `token_scope.resource_type is None`
→ the helper returns `None` (`filter_helpers.py:236-237`) → **the gate does not
fire**.

**Reproduced:**

```
POST /api/v1/users/me/token {"scope":"READ"}   → 200, resource_type: null
GET /api/v1/reviews                            → 200
GET /api/v1/reviews/1                          → 200
GET /api/v1/reviews/1/suggestions              → 200   (full payload)
```

**Not a data leak.** Such a token can already read every picture and every set
name in the vault, so `locked_sets` discloses nothing it could not obtain from
`GET /picture_sets`. It is mintable only by the owner (`create_token` refuses
scoped callers, `auth.py:899-903`). Incremental exposure is curation metadata
only — which tags the owner suspects are mislabelled.

The problem is that the *stated* policy ("Owner-only", repeated in ten comments)
and the *enforced* policy diverge, and the codebase already has the correct
primitive: `require_unscoped_owner` (`auth.py:687-706`), used by `snapshots.py`
and `config.py`, which checks `token_scope` **and**
`matched_token.resource_type`. Using a picture-id helper as an owner test is
precisely the duplicated-`token_scope`-ladder debt §16.2 item 3 calls out — and
it is duplicated across eleven call sites in two modules.

**Fix.** Replace with `server.auth.require_unscoped_owner(request)`, or rename the
helper to `_is_resource_scoped` and correct the comments to state the real
policy. Decide deliberately whether a vault-wide READ token should see reviews;
right now that is an accident of the predicate, not a decision.

---

### F6 — `scan_tag` materialises every locked picture id in the vault (LOW, hardening)

**Location:** `pixlstash/services/tag_scan_service.py:299-308`

```python
locked = {int(locked_id) for locked_id in session.exec(locked_picture_id_subquery()).all()}
```

Unbounded materialisation of a vault-wide id set into Python. The base commit did
the same with a narrower query, so this is not new, but the predicate now also
matches stack siblings and so returns strictly more rows. On a vault with a very
large locked set this is an avoidable memory spike inside a scan task.
Not reachable by an unauthenticated or scoped caller (owner-only surface).
**Fix (optional):** push the predicate into the candidate query as a
`notin_` rather than round-tripping the ids.

---

## 3. What I checked and found CLEAN

Recorded so the matrix is verifiable rather than assertive.

### 3.1 Primary suspect: `locked_sets` name egress via a scoped token — **CLOSED**

Reproduced by execution (temporary test, removed after the run; owner session +
Bearer READ token scoped to a picture set holding the pair):

```
SCOPED GET /api/v1/reviews                  → 403 {"detail":"Not available to this token"}
SCOPED GET /api/v1/reviews/1                → 403 {"detail":"Not available to this token"}
SCOPED GET /api/v1/reviews/1/suggestions    → 403 {"detail":"Not available to this token"}
```

A resource-scoped token cannot reach the endpoint at all, so it cannot obtain a
set name via `locked_sets` / `twin_locked_sets` — neither for a set it has no
access to, nor for a set that merely shares a stack with an in-scope picture. The
new field is a **new payload**, not a **new egress path**. The gate sits above
every branch and every return, and it is fail-closed for an unrecognised
`resource_type` (`filter_helpers.py:285-290` returns an empty set, which is
`is not None`, hence 403).

### 3.2 `locked_picture_id_subquery()` — correct, and equivalent to the write guards

```python
locked_members = select(PictureSetMember.picture_id).join(PictureSet, ...).where(PictureSet.locked.is_(True))
locked_stacks  = select(Picture.stack_id).where(Picture.id.in_(locked_members),
                                                Picture.stack_id.is_not(None),
                                                Picture.deleted.is_(False))
return select(Picture.id).where(or_(Picture.id.in_(locked_members),
                                    Picture.stack_id.in_(locked_stacks)))
```

Proved equivalent to the write-guard path
(`_locked_sets_by_picture` → `expand_picture_ids_to_stacks`), which freezes *P*
iff *P* is a direct locked-set member, **or** some non-deleted *Q* sharing *P*'s
stack is. Arm 1 is the first disjunct; arm 2 is exactly the second, with the
`deleted` filter on the stack arm only — matching
`expand_picture_ids_to_stacks` (`stack_membership.py:53-60`), which drops deleted
co-members while always keeping the input id. **Neither over- nor under-blocks
relative to the write guards**, as the docstring claims. Edge cases:

| Case | Result | Correct? |
|---|---|---|
| No locked sets at all | Both arms empty → predicate false | ✅ nothing is frozen |
| `Picture.stack_id IS NULL` | `NULL IN (…)` → NULL → arm 2 false; arm 1 still applies | ✅ |
| Picture in several sets, one locked | Join yields the locked row → arm 1 true | ✅ |
| Locked member is soft-deleted | Excluded from `locked_stacks`; still caught by arm 1 for itself | ✅ matches guards |
| `notin_` NULL-poisoning | `locked_stacks` filters `stack_id IS NOT NULL`; outer select is `Picture.id` (PK, never NULL) — no NULL enters the `IN` list | ✅ |
| `TagSuggestion.picture_id` NULL | `NULL NOT IN (…)` → NULL → row excluded | ✅ fails **closed** |

### 3.3 Lock as a write boundary — not weakened

`git diff` on `tag_suggestion_service.py` shows **only** an import and a read
filter added to `list_suggestions`. Every write guard is untouched and still
present: `reopen_suggestion` (`:498`, guarding both suspect **and** twin),
accept (`:299`), the bulk paths (`:866`, `:905`, `:999`), `locked_picture_ids`
skip-lists (`:693`, `:813`). All 24 `enforce_pictures_not_locked` /
`enforce_set_not_locked` / `locked_picture_ids` call sites across
`vault.py`, `routes/tags.py`, `routes/picture_sets.py`,
`routes/pictures/_crud.py`, `routes/characters.py`,
`tag_prediction_service.py`, `impossible_tag_clear_service.py` and the task
modules are unchanged. The diff adds **read** filters that hide rows whose writes
would 423; it removes no write guard. Direction of change is fail-closed.

`tag_scan_service`'s swap from a local `PictureSetMember` join to
`locked_picture_id_subquery()` strictly **widens** the set excluded from being a
suspect (it gains the stack-sibling arm). Also fail-closed.

### 3.4 `progress.locked` bucket — no disclosure, no miscount

`_progress_map` (`review_service.py:289-322`) adds `is_locked` as a grouped SQL
expression and routes PENDING+locked into its own bucket. Only reachable through
the owner-only `/reviews` reads. It reports a count, never an id or a name. The
`locked`-vs-`pending` split is consistent with the queue filter in
`list_review_suggestions` (both keyed on PENDING + the same subquery), so served
cards and reported progress cannot disagree.

### 3.5 Frontend URL state `?review=<id>` — a shared link is **not** an access grant

Two independent reasons:

1. **Backend authorizes per request.** `GET /reviews/{id}` (`reviews.py:309-315`)
   runs the owner gate before `review_service.get_review`. An unauthenticated or
   scoped holder of the URL gets 401/403. The id in the query string grants
   nothing.
2. **The frontend cannot be used as a confused deputy either.**
   `resolveReviewView` (`useReviewRoute.js:134-149`) only opens a review already
   present in `store.sessions` / `store.archived` — lists populated from the
   authorized `GET /reviews`. An arbitrary or forged `?review=<id>` matches
   neither list and falls back to the board. It never triggers a fetch of an
   id the session was not already authorized to see.

Also checked: `?review` presence-parsing (`:73`) treats `?review=nonsense` as
"open on the board", not as an id — no injection into a request path.

### 3.6 Migration 0075 hygiene

Inspector-guarded `add_column`, table-existence guard, `__all__` present,
`down_revision` correct and matching the current head, no application logic
(schema change + a targeted `computed_at` reset, which is the CLAUDE.md-sanctioned
reprocessing-trigger pattern), guarded and symmetric `downgrade`. No
thundering-herd or DoS implication — see F4.

### 3.7 Suites re-run independently

`tests/test_picture_set_locking.py` → 21 passed.
`tests/test_reviews_api.py` + `tests/test_tag_health_api.py` → 63 passed, 1 skipped.
`ruff check pixlstash` → clean.

### 3.8 Not applicable to this diff

No dependency changes beyond version bumps (`pyproject.toml`,
`frontend/package.json`, `electron/package.json` — all the same rc2 version
string). No secrets introduced (`git diff` grep for key/token/password literals:
clean). No CI/workflow changes. No new file-parsing, path-handling, deserialization,
subprocess or network-egress surface. No CORS or CSP change.

---

## 4. Release decision

**Release blockers: none.** No finding in this diff opens an exploitable hole,
and the diff's own new surface (two DELETE endpoints, the extended suggestions
payload, the `ground_truth` field, migration 0075) is correctly gated, verified
by execution.

**Pre-existing findings to schedule (not gating this merge):**

| ID | Severity | Issue | Owner | Revisit |
|---|---|---|---|---|
| F1 | MEDIUM | Locked set names disclosed to a token that cannot see the set | backend / auth maintainer | next auth pass |
| F2 | MEDIUM | `/tag_suggestions` leaks out-of-scope twin attributes | backend / auth maintainer | next auth pass |

Both are the same root cause — **object-level scope authorizes the picture but
not the related entities named in its payload.** Neither is newly reachable here.
Accepted for this release on the single-owner basis of §16.3: the only principal
who can mint tokens is the owner, and share tokens are the sole non-owner
principal. **Both become materially exploitable the moment share tokens are
handed to parties who must not learn set names or vault picture ids** — treat
them as blockers for any multi-user or wider-sharing work, per §16.3's precedent.

**Should land with, or immediately after, this merge:**

- **F3** — the three missing scoped-token read tests. Small, and it is the one
  new egress path in the diff with no negative test.

**Hardening that can wait:** F4 (nullable `ground_truth`), F5
(`require_unscoped_owner`), F6 (subquery push-down).

**Direction note per §16.2.** This diff adds no new per-handler authorization
check — the reviews and tag_health gates it extends are pre-existing. It is
therefore *neutral* on the centralisation debt, not a contribution to it. F5 is
the one place where converging on the shared primitive (`require_unscoped_owner`)
would repay eleven duplicated call sites; doing it as part of the chokepoint
migration's step 2 back-fill is the natural home.
