# `feature/dedup-tiers` (PR #638) — independent adversarial authz sign-off

- **Reviewer:** CSO persona, independent of the authors. Did not write any code on this branch.
- **Branch under review:** `origin/feature/dedup-tiers` @ `21b07e64` (merge of `develop-1.9`), diffed against `origin/develop-1.9` @ `9f9cd566`.
- **Diff size:** 25 files, +7647 / −25. New surface: 8 routes, 3 services, 4 tables, 1 migration, 2 finders/tasks.
- **Date:** 2026-07-29.
- **Mandate:** refute the branch's safety claims. Reproduce every finding with a concrete request or test before reporting it. Coverage matrix, not a findings list.

---

## Verdict

# **APPROVE-WITH-REQUIRED-CHANGES**

The **authorization** work is correct and I could not break it. All eight routes are
`OWNER_ONLY`, enforced solely by the central gate, verified 403 in both transports
against two token shapes and 200 for the owner on every route. The coverage-matrix
arithmetic reproduces exactly. The locked-set fail-closed contract holds on a
partial-lock batch, including the folded-stack co-member case the authors did not
test. The stack-expanded undo snapshot is genuinely non-vacuous.

The required changes are **not** authorization holes. They are: one write-path
identity defect that makes a verdict address the wrong picture set (R1), one
fail-closed gap in the bulk path that leaves a partially-applied mutation whose
undo handle is never returned (R2), and one §21 compliance omission on the most
far-reaching mutation on the surface (R3). R1 and R2 are both reproduced below with
concrete state dumps.

| # | Severity | What | Status |
|---|---|---|---|
| **R1** | **MEDIUM** | Group signature omits the `size_bytes` co-key → two distinct duplicate groups collide on one signature; one is silently dropped from the queue and a verdict about one silently resolves the other | **REQUIRED** |
| **R2** | **MEDIUM** | `bulk_auto_stack` catches only `DedupVerdictError`; a `423` mid-run aborts after committing earlier groups, and the server-minted `batch_id` is never returned — a partially-applied bulk mutation with no undo handle in the response | **REQUIRED** |
| **R3** | **LOW-MED** | The verdict routes are the only operation-log recording routes on the branch that omit `request_context(request)`; every dedup stack is recorded `actor=None, source="external"` | **REQUIRED** |
| R4 | LOW | Non-numeric `scope_id` for project/set/character → unhandled `ValueError` → **500** on `/dedup/groups`, `/dedup/counts`, `/dedup/auto-stack`; `POST /dedup/scan` **persists** the poison row | Hardening |
| R5 | LOW | `MAX_BUCKET_MEMBERS` bounds tier-2 CPU but **not** memory: the pair list is O(k²) and `DedupScanTask` accumulates it across all buckets, re-deriving and re-persisting every group after every bucket, on the single DB writer thread | Hardening |
| R6 | LOW | No cap or coalescing on distinct scan scopes (301 pending rows from 301 requests) and no cap on the `POST /dedup/counts` scope list (5000 accepted) | Hardening |
| R7 | LOW | `include_deleted=True` on the undo snapshot is load-bearing and **correct** but untested — removing it leaves all 28 verdict tests green | Test gap |
| A1 | Accepted | A stack verdict widens live share tokens via the set/project membership union; `/dedup/auto-stack` amplifies this to a one-click vault-wide bulk operation | Accepted risk |
| A2 | Accepted | Caller-supplied `batch_id` is taken verbatim; unrelated verdicts can be grafted into one undo unit | Accepted risk |
| D1 | Doc | §22.9 describes a lazy import and "the operation log is not on this branch" — stale after the merge; the import is module-level | Doc fix |

---

## 1. Coverage matrix — recounted from `ROUTE_POLICIES`, not from prose

The authors claim **236 declared / 235 mounted / 1 conditional**. Re-derived
independently against the live route inventory (probe `test_A1`):

```
[A1] mounted (api_endpoint_set)  = 235
[A1] declared (ROUTE_POLICIES)   = 236
[A1] conditional waiver          = 1 [('POST', '/api/v1/test-hooks/ws-event')]
[A1] declared - conditional      = 235
[A1] undeclared mounted routes   = []
[A1] dead declarations           = []
[A1] mounted dedup paths         = 10 [.../auto-stack, .../counts, .../groups, .../policy,
     .../scan, .../sweep/dry-run, .../sweep/policy, .../verdicts/keep-separate,
     .../verdicts/reopen, .../verdicts/stack]
```

`235 == 236 − 1`. **Arithmetic confirmed.** Policy distribution recounted:
`owner_only` 92 → **100** (+8, exactly the new dedup routes); no other class moved
(`public` 13, `any_token` 16, `picture_scoped` 35, `scoped_list` 39, `set_scoped` 4,
`character_scoped` 5, `project_scoped` 6, `local_owner_only` 13,
`loopback_owner_only` 5). The matrix document's updated numbers match.

**Note on my first attempt:** a naive `app.routes` walk returned **14** routes, not
235 — the documented FastAPI `_IncludedRouter` trap that `pixlstash/route_inventory.py`
exists to defeat. That trap is real and the guard in `route_inventory.py` is
load-bearing; anyone re-deriving this count by hand will silently under-count.

### 1.1 The 8 new routes — both directions, both transports

Probe `test_A2` / `test_A2b`, one fresh `TestClient` per request:

| Method | Path | Policy | set-scoped READ (hdr / `?token=`) | unscoped READ (hdr / `?token=`) | owner cookie |
|---|---|---|---|---|---|
| GET | `/dedup/policy` | `OWNER_ONLY` | 403 / 403 | 403 / 403 | 200 |
| GET | `/dedup/groups` | `OWNER_ONLY` | 403 / 403 | 403 / 403 | 200 |
| POST | `/dedup/counts` | `OWNER_ONLY` | 403 / 403 | 403 / 403 | 200 |
| POST | `/dedup/scan` | `OWNER_ONLY` | 403 / 403 | 403 / 403 | 200 |
| POST | `/dedup/verdicts/stack` | `OWNER_ONLY` | 403 / 403 | 403 / 403 | 200 |
| POST | `/dedup/verdicts/keep-separate` | `OWNER_ONLY` | 403 / 403 | 403 / 403 | 200 |
| POST | `/dedup/verdicts/reopen` | `OWNER_ONLY` | 403 / 403 | 403 / 403 | 200 (with a prior verdict) |
| POST | `/dedup/auto-stack` | `OWNER_ONLY` | 403 / 403 | 403 / 403 | 200 |

**The authors' tests only cover the resource-scoped READ token.** I added the
**unscoped READ** token shape — a legitimately mintable whole-library read-only
share token, and the shape that `require_unscoped_owner` distinguishes with a
*different* branch (`token_scope is not None` rather than
`matched_token.resource_type is not None`). It is denied on all eight, both
transports. That sibling vector is now verified but is **not** pinned by a durable
test; see R7-adjacent recommendation in §6.

Handlers contain **no** inline authz code (verified by reading
`pixlstash/routes/dedup.py` end to end — the only guards are `_policy` / `_scope`
400-translators). §16.1 satisfied: the gate is the sole enforcement.

### 1.2 Sibling hunt — can a scoped-reachable route leak dedup-derived facts?

I checked every scoped-reachable route the verdict path touches, before and after a
verdict (probe `test_A3`).

| Route | Scoped token, before verdict | After verdict | Leak? |
|---|---|---|---|
| `GET /pictures` | `[1]` | `[1, 2]` | Not a leak — see A1 |
| `GET /pictures?fields=grid` | `[1]` | `[1]` (collapsed) | No |
| `GET /pictures/{out_of_scope}` | 403 | 200 | Not a leak — see A1 |
| `GET /pictures/{out}/pixel_sha` | **403** | 200 | Not a leak — see A1 |
| `GET /pictures/{out}/tags` | **403** | 200 | Not a leak — see A1 |
| `GET /stacks/{stack_id}/pictures` | n/a | 200 | Consistent with membership |
| `GET /picture_sets/{id}/members` | 200 (1 member) | 200 (2 members) | Consistent |

**No route exposes a dedup-derived fact** — no signature, no group id, no group
membership, no confidence, no scan progress reaches a scoped token. `pixel_sha` is
reachable only through `GET /pictures/{id}/{field}`, which is `PICTURE_SCOPED` and
returned **403** for an out-of-scope picture (verified). The widening in the table
above is a *membership* change, not a scope-check failure — see A1.

---

## 2. R1 (REQUIRED) — the group signature is not injective over groups

**Severity:** MEDIUM (write-path target ambiguity + silent data loss from the queue).
Not a confidentiality issue. **Location:** `pixlstash/services/dedup_tier_service.py`
`CandidateMember.content_key` (line ~415) and `group_signature` (line ~485).

`§22.1` states, as the branch's own central design claim:

> **`size_bytes` is a co-key, not decoration.** The sample offsets are derived from
> the file size, so equal size plus equal sampled digest is a far stronger claim
> than the digest alone.

Tier 1 honours this in **detection** (`GROUP BY pixel_sha, size_bytes`) and drops it
in **identity**:

```python
@property
def content_key(self) -> str:
    return self.pixel_sha or f"id:{self.id}"      # no size_bytes
```

`signature = sha256("\x1f".join(sorted(content_keys)))`. Two distinct tier-1 groups
that differ only by `size_bytes` therefore produce the **same** signature — and
`dedupgroup.signature` / `dedupverdict.signature` are both `unique=True`.

### Reproduction (probe `test_E1`)

Four pictures: `{1,2}` at `pixel_sha='aaa', size=100`; `{3,4}` at `pixel_sha='aaa', size=999`.

```
[E1] tier 1 detected 2 distinct group(s): [[1, 2], [3, 4]]
[E1] their signatures = ['731037cc1fb26ece2ede462c36719f53cb4a00bb51d2876b05ec2a062f923c4c']
[E1] >>> distinct groups: 2, distinct signatures: 1
[E1] persisted dedupgroup rows = [('731037cc1fb2', 'exact', [3, 4], False)]
[E1] >>> 2 detected groups collapsed to 1 stored row(s) — the upsert-on-signature dropped one
[E1] after keep-separate on the surviving group: [('731037cc1fb2', 'exact', [3, 4], True)]
[E1] after a rescan:                             [('731037cc1fb2', 'exact', [3, 4], True)]
[E1] verdict rows = [('731037cc1fb2', 'keep_separate', '[3, 4]')]
[E1] >>> a decision about one file-set silently resolves the other
```

**Three consequences, all reproduced:**

1. **A real duplicate group vanishes from the queue.** Pictures 1 and 2 are exact
   duplicates and the user is never shown them. The sidebar badge under-counts, and
   no log line says anything was dropped — `persist_groups_in_session` treats the
   second group as a refresh of the first.
2. **A `keep_separate` verdict silences both file-sets permanently.**
   `verdict_signatures_in_session` marks any future re-detection `resolved=True`,
   and the design's whole point is that this decision survives rescans and
   re-imports. The user consented to one thing and got two.
3. **A `stack` verdict's write target depends on scan ordering, not on what the
   user saw.** `_load_group(signature)` returns whichever member set survived the
   upsert. A UI showing group A can send a confirmed cover and the server stacks
   group B. This is the one place on the surface where "the signature bounds the
   write" — the justification in `ROUTE_POLICIES` — is not true.

**Likelihood.** A *natural* same-sha-different-size pair is unlikely: below 128 KiB
`pixel_sha` is a full-file digest, and above it the sample offsets are size-derived.
But the branch's own §22.1 raises this exact case as the reason the co-key exists,
the failure is **silent**, the blast radius is a permanent verdict on files the user
never reviewed, and it is trivially reachable in fixtures and in any future path
that writes `pixel_sha` without a size (e.g. a sidecar/metadata import).

**Fix (one line):**

```python
@property
def content_key(self) -> str:
    if self.pixel_sha:
        return f"{self.pixel_sha}:{self.size_bytes}"
    return f"id:{self.id}"
```

Changing the signature invalidates stored verdict rows — free on an unreleased
feature branch, so do it now rather than after 1.9 ships. Add a regression test
asserting two same-sha/different-size groups produce **two** signatures and **two**
persisted rows.

---

## 3. R2 (REQUIRED) — the bulk path is not fail-closed and loses its undo handle

**Severity:** MEDIUM. **Location:** `pixlstash/services/dedup_verdict_service.py`
`bulk_auto_stack_in_session`, the `except DedupVerdictError` at line ~711.

`POST /dedup/auto-stack`'s own API description states:

> A single unstackable group never aborts the run, so a partial result is reported
> honestly rather than hidden.

That is **false for the locked-set case**, which is the most likely cause of an
unstackable group. `enforce_stack_membership_not_locked` / `enforce_pictures_not_locked`
raise `HTTPException(423)`, not `DedupVerdictError`, so the loop does not catch it —
and each successful iteration has *already* `session.commit()`ed.

### Reproduction (probe `test_B2`)

Three exact groups; one member of the second group is frozen by a locked set.

```
[B2] raised HTTPException 423: {'code': 'set_locked', 'action': 'stack duplicates together', ...}
[B2] pictures before  = [(1,None,None,..), (2,None,None,..), (3,None,None,..), (4,..), (5,..), (6,..)]
[B2] pictures after   = [(1, 1, 0, ..),    (2, 1, 1, ..),    (3,None,None,..), (4,..), (5,..), (6,..)]
[B2] stacks before/after = [] / [1]
[B2] verdicts after   = [('731037cc…', 'stacked')]
[B2] operations after = [('dedup.stack', '8b4f2a48aa704be6bc076b382e907e5e', 'applied')]
[B2] >>> PARTIAL WRITE after a 423: True
[B2] >>> batch ids committed but NEVER returned to caller: {'8b4f2a48aa704be6bc076b382e907e5e'}
```

The caller receives a `423` whose body is the lock detail. The `batch_id` was minted
**server-side** (`batch_id = batch_id or new_batch_id()`) and is only ever returned
in the success response. So the user has a partially-applied bulk stack and the
`POST /operations/batches/{batch_id}/undo` handle for it exists **only** in the
`operation` table — recoverable via `GET /operations`, but not from the response,
and not from the toast the frontend would show.

Contrast with the single-verdict path, which **is** correctly fail-closed (§4 below).
The bulk path breaks the same contract its siblings honour.

**Fix — either is acceptable, pick one and state it:**

- **(a) Make it match the docstring.** Catch `HTTPException` alongside
  `DedupVerdictError` and record it in `failures` with the status code, so a locked
  group is skipped and reported instead of aborting. This is the behaviour the API
  description already promises.
- **(b) Make it genuinely atomic.** Do not commit per group; commit once at the end.
  Then a 423 rolls the whole run back (the DB worker already rolls back on exception —
  `database.py:885`) and "nothing was written" is true.

Whichever is chosen, **the response must carry the `batch_id` on the failure path
too** if any group committed. Add a regression test with a partial-lock bulk run
asserting both the surviving state and the returned handle.

---

## 4. What I could **not** break

These were attacked and held. Recording them so the next reviewer does not re-spend
the effort, and so a regression is visible.

### 4.1 The single-verdict locked path is genuinely fail-closed (probe `test_B1`)

Three-member exact group, **one** member frozen by a locked set. I snapshotted seven
independent state dimensions before and after:

```
[B1] status = 423
[B1] detail = {'code': 'set_locked', 'action': 'stack duplicates together', 'sets': [{'id': 1, 'name': 'Frozen'}]}
[B1] pictures         unchanged=True
[B1] stacks           unchanged=True
[B1] tags             unchanged=True
[B1] verdicts         unchanged=True
[B1] operations       unchanged=True
[B1] set_members      unchanged=True
[B1] groups_resolved  unchanged=True
```

Nothing written, including the `PictureStack` row that `_stack_members` `flush()`es
*before* the lock check — the per-task `session.rollback()` in
`database.py:_task_worker_loop` covers it. **The claim holds.** The authors' own test
only asserts the tag facet; mine asserts all seven.

### 4.2 A locked co-member of a *folded* stack is also caught (probe `test_B6b`)

This is the case I expected to break, because `apply_metadata_union_in_session` calls
`enforce_pictures_not_locked` over `included` only — **not** over the co-members that
`_stack_members` drags in when it folds another stack. It holds anyway:
`enforce_stack_membership_not_locked` runs first and expands through
`expand_picture_ids_to_stacks`, so the folded co-member is inside the checked set.

```
[B6b] locked set 1 freezes co-member 3 only  (group = {1,2}; 2 and 3 share a pre-existing stack)
[B6b] -> 423: {'code': 'set_locked', 'action': 'stack duplicates together', 'sets': [{'id': 1, 'name': 'Frozen'}]}
[B6b] world changed = False
```

The ordering that makes this safe is load-bearing and undocumented. If anyone ever
moves `enforce_stack_membership_not_locked` after the fold, this opens. Worth a
comment and a durable test.

### 4.3 Signature, cover and exclusion abuse (probes `test_B3`, `test_B4`, `test_B5`)

`_load_group` requires an existing `dedupgroup` row, so a signature cannot name an
arbitrary picture set — it can only name a *detected* group.

```
[B3] stack(empty     ) -> DedupVerdictError (400)
[B3] stack(random hex) -> DedupVerdictError (400)
[B3] stack(sql-ish   ) -> DedupVerdictError (400)   "' OR 1=1 --"  (parameterised; no injection)
[B3] stack(huge      ) -> DedupVerdictError (400)   100 000 chars
[B3] stack(weird     ) -> DedupVerdictError (400)   RTL-override chars
[B3] stale sig (1 of 2 scrapheaped) -> DedupVerdictError: "…has 1 member(s) left after exclusions"
[B3] world changed: False
```

Scrapheaped members are filtered by `_load_group`'s `Picture.deleted.is_(False)` join,
and the surviving group then fails the `len(included) < 2` gate. Nothing written.

```
[B4] cover=outsider     -> rejected (400)   cover=nonexistent/negative/0 -> rejected (400)
[B4] excl=outsider      -> rejected (400)   excl=nonexistent -> rejected (400)
[B4] excl=all           -> rejected (400)   excl=2of3        -> rejected (400)
[B4] excl=dupes         -> ACCEPTED [1, 3] cover=1           (correctly de-duplicated)
[B5] reopen(never-decided) -> rejected      reopen(forged) -> rejected
[B5] double reopen         -> rejected: "Verdict for '…' is already reopened"
```

Every abuse case rejected with a 400 and no state change. No SQL injection —
`scope_id` and `signature` reach SQLAlchemy as bound parameters throughout.

### 4.4 Bounds validation (probe `test_D6`)

`limit>MAX_PAGE_SIZE`, `limit<=0`, `offset<0`, `threshold` outside
`[MIN_THRESHOLD, MAX_THRESHOLD]` → 422 from FastAPI `Query` constraints;
`embedding_enabled` without `near_enabled` → 400 with the tier-gating message.
No silent clamping, exactly as §22.4 promises.

### 4.5 The undo integration (probes `test_C1`, `test_C3`, plus a reverted-fix run)

**The stack-expanded snapshot fix is genuinely non-vacuous.** I reverted it myself:

```python
def _undo_targets(session, picture_ids):
    return list(picture_ids)   # CSO NON-VACUITY PROBE: fix reverted
```

```
FAILED tests/test_dedup_verdict_service.py::test_the_snapshot_covers_stack_siblings_the_group_never_named
E       assert 2 == 1
E        +  where 2 = _picture(server, 2).stack_id
E        +  and   1 = _picture(server, 4).stack_id
1 failed, 5 passed, 22 deselected
```

The sibling the group never named is stranded on the merged stack — exactly the
symptom the fix exists to prevent. **The two-stack-fold test is real.**

Batch undo of an auto-stack reverses **all** its stacks:

```
[C3] auto-stack groups=5 batch=f13f7d4ec9454ebe9ac57b1ad667e129
[C3] undo_batch reverted 5
[C3] >>> fully reversed: True
[C3] leftover stack rows after undo = [1, 2, 3, 4, 5]     ← 5 orphaned empty PictureStack rows
```

The orphaned empty stack rows are DB hygiene, not a correctness or security issue
(no picture points at them and they are invisible in the UI). Worth a cleanup pass.

**Undo cannot cross a scope boundary.** All 7 `operations.py` routes are
`OWNER_ONLY` (verified in the recount). The only caller-supplied-`batch_id` readers
in the whole tree are `GET /operations?batch_id=` and
`POST /operations/batches/{batch_id}/undo`, both owner-only; the two other
`batch_id` sites (`routes/pictures/_crud.py`) mint their own. A `batch_id` is
therefore never a capability reachable by a scoped principal.

---

## 5. R3–R7 (required change R3; the rest is hardening)

### R3 (REQUIRED) — the verdict routes skip `request_context(request)`

**Location:** `pixlstash/routes/dedup.py` — none of the four verdict/auto-stack
handlers takes a `request: Request` parameter; `_record_operation` therefore always
runs with its defaults `actor=None, source="external", origin_client_id=None`.

§21 *Origin discipline* is explicit that these are read **from the request, in the
handler**. Every other operation-log recording site on the branch complies
(`routes/tags.py` ×4, `routes/picture_sets.py`, `routes/pictures/_crud.py`, …).
The dedup verdict routes are the only exceptions, and they cover the *most
far-reaching* mutation on the surface — a bulk auto-stack can rewrite thousands of
pictures under one operation row with no actor recorded.

Consequences: the operation log's audit-trail purpose (§21: "the undo/redo stack
today and the audit log / Studio activity feed later") is degraded for exactly the
bulk action that most needs attribution; and `source` is always `"external"`, so the
frontend cannot tell its own click apart from a scripted call.

**Fix:** add `request: Request` to the four handlers and thread
`**operation_log_service.request_context(request)` through
`apply_stack_verdict` / `bulk_auto_stack` into `_record_operation`. Mirror
`routes/tags.py`. Pin it with the AST-style check already used for the contextvar
rule.

### R4 (hardening) — non-numeric `scope_id` is a 500 and a persisted poison row

`DedupScope.picture_predicate()` calls `int(self.scope_id)` for
`project` / `set` / `character`, but `DedupScope.__post_init__` never validates it.
`_scope()` in the route only catches the `ValueError` raised at construction time,
not the one raised later at query time (probe `test_D4`):

```
[D4] project    scan=200 groups=500 counts=500 auto-stack=500
[D4] set        scan=200 groups=500 counts=500 auto-stack=500
[D4] character  scan=200 groups=500 counts=500 auto-stack=500
[D4] persisted dedupscan rows = [(1, 'project:not-an-int', 'pending'),
                                 (2, 'set:not-an-int',     'pending'),
                                 (3, 'character:not-an-int','pending')]
```

`POST /dedup/scan` returns **200** and writes a `dedupscan` row with an unparseable
`scope_id`. The background `DedupScanTask` then fails on it (handled — `_mark_failed`
sets `status=failed`), but every subsequent `GET /dedup/groups` for that scope 500s.
Owner-only, so no disclosure; still an unhandled-exception surface and a self-inflicted
poison pill. **Fix:** validate/coerce `scope_id` in `DedupScope.__post_init__`
(int for project/set/character) so it is a 400 at the boundary.

Related, lower: `folder` scopes build `Picture.file_path.like(f"{prefix}%")` without
escaping LIKE metacharacters, so `scope_id="%"` or `"_"` is accepted and matches
more than the caller named (probe `test_D5`). Owner-only and read-only, but escape
it — a "Find duplicates in this folder" entry should not silently mean "everywhere".

### R5 (hardening) — the bucket cap bounds CPU, not memory; the scan is quadratic

The direct question asked: **does the bucket sharding cap actually bound tier-2
memory? No.** `MAX_BUCKET_MEMBERS = 4000` bounds the *popcount work* per bucket. The
materialised pair list is O(k²) and unbounded (probe `test_D2`):

```
[D2] MAX_BUCKET_MEMBERS = 4000
[D2] k=400 identical hashes -> 79800 pairs in 0.06s, ~5.8 MB
[D2] >>> extrapolated at k=MAX_BUCKET_MEMBERS: 7,980,000 pairs ~582 MB in ONE bucket
```

400 identical perceptual hashes (a plausible real case: a burst of near-black frames,
a folder of solid-colour placeholders, an import of one image copied 4000 times) give
79 800 pairs. At the cap that is ~8M tuples, ~580 MB, in a single bucket.

Worse, `DedupScanTask.run_scan_in_session` keeps a `pair_cache` across **all**
buckets and re-derives + re-persists **every group from every pair seen so far**
after **each** bucket (probe `test_D3` prints the loop verbatim):

```python
for index, bucket in enumerate(buckets, start=1):
    for a, b, similarity in near_pairs_in_bucket(session, bucket, policy.threshold):
        ...pair_cache[key] = similarity
    groups = groups_from_pairs(session, [(a, b, sim) for (a, b), sim in pair_cache.items()], ...)
    persist_groups_in_session(session, groups, scan_id)   # deletes + reinserts ALL members
    session.commit()
```

That is O(buckets × total_pairs) work and O(buckets) full re-persists. And the whole
scan runs inside **one** `self._db.run_task(...)` — `database.py:661` shows a single
`_task_worker` thread — so a vault-wide near-scan holds the only DB writer for its
entire duration. Every import, tag edit, scrapheap move and verdict queues behind it.

The comment justifies the re-derivation ("a chain that spans two buckets becomes one
group"), which is a real requirement — but it can be satisfied by deriving groups
once at the end and streaming only the *new* components per bucket, or by capping
`pair_cache`. **Not a release blocker** (owner-only, self-inflicted, and the tier is
opt-in), but §22.7's "10 groups and 10,000 cost the same" claim is true of the
*queue page* and false of the *scan*, and the doc should not imply otherwise.

### R6 (hardening) — scan and counts amplification

Probe `test_D1`:

```
[D1a] 50x global scan -> 1 dedupscan row(s): [(1, 'global', 'pending')]     ← coalescing works
[D1b] +300 distinct folder scopes -> 301 row(s) in 0.6s
[D1b] >>> pending scan rows queued for the background worker: 301
```

Same-scope requests **do** coalesce (upsert on `scope_key`) — good. Distinct scopes
do not, and `scope_id` is an unvalidated free-text string, so there is no bound on
how many `dedupscan` rows a client can create. `DedupScanFinder.max_inflight_tasks()
== 1` means they run one at a time rather than in parallel, but the queue itself is
unbounded and each one is a full scan of its scope.

Probe `test_D7`: `POST /dedup/counts` accepts an uncapped `scopes` list — 5000
entries → 5000 separate correlated `COUNT` subqueries, accepted with a 200. One
request, 5000 queries.

Both are owner-only, so this is availability of the owner's own server rather than a
multi-tenant DoS. **Fix:** cap the `scopes` list (a `max_length` on the Pydantic
field), and cap or age out `dedupscan` rows.

### R7 (test gap) — `include_deleted=True` is load-bearing but untested

I verified the behaviour is **correct** (probe `test_C1`): a scrapheaped stack
sibling at `stack_position=5` is renumbered to `2` by the verdict and restored to
`5` by undo.

```
[C1] before verdict = [(1, None, None, False), (2, 1, 0, False), (3, 1, 5, True)]
[C1] after verdict  = [(1, 1, 0, False),       (2, 1, 1, False), (3, 1, 2, True)]
[C1] after undo     = [(1, None, None, False), (2, 1, 0, False), (3, 1, 5, True)]
[C1] >>> scrapheaped sibling reversed: True
```

But it is **not pinned**. Dropping only the flag:

```python
return expand_picture_ids_to_stacks(session, picture_ids)   # include_deleted dropped
```

leaves the suite green: `28 passed`. §21.1 requires the flag precisely because
`normalize_stack_positions` renumbers soft-deleted members too. Promote my `test_C1`
into `tests/test_dedup_verdict_service.py`.

---

## 6. Accepted risks (per the CLAUDE.md accepted-risk rule)

### A1 — a stack verdict widens live share tokens; auto-stack does it in bulk

- **Risk.** `apply_metadata_union_in_session` calls `reconcile_stack_membership`,
  which unions set/project membership across the stack (§22.8, intentional). An
  out-of-scope duplicate is thereby **added to a shared set**, and every live share
  token for that set immediately reaches it — pictures, tags, `pixel_sha`, files.
- **Reproduced** (probe `test_A3`), owner performs one `POST /dedup/verdicts/stack`:

  ```
  [A3] set 1 members BEFORE = [1]
  [A3] BEFORE verdict   visible=[1] GET pictures/2/pixel_sha=403 GET pictures/2/tags=403
  [A3] owner POST /dedup/verdicts/stack -> {... "picture_ids": [1, 2],
                                            "metadata_union": {..., "membership_changed": true}}
  [A3] set 1 members AFTER  = [1, 2]
  [A3] AFTER verdict    visible=[1, 2] GET pictures/2/pixel_sha=200 GET pictures/2/tags=200
  [A3]   out-of-scope tag body = {"id":2,"tags":[{"id":6,"tag":"secret-tag-0"},{"id":2,"tag":"secret-tag-1"}]}
  ```
- **Control — is it new?** No. Ordinary `POST /stacks` does the same (probe `test_A3b`):
  `scoped-token visible before=[1] after=[1, 3]`. This is the shipped stack-atomic
  membership model, not a dedup regression, and the authz gate is behaving exactly
  as designed (picture 2 *is* a set member after the union).
- **What is new** is the amplification: `POST /dedup/auto-stack` with `dry_run=false`
  applies it to **every exact group in the vault** behind one consent dialog
  (probe `test_A3c`: 2 groups, 4 pictures, one click; at scale, thousands). The
  dry-run response reports `groups` and `pictures` and says nothing about how many
  **shared sets or live tokens** would gain members.
- **Blast radius.** Bounded to the owner's own sharing decisions; only affects sets/
  projects that already have an outstanding share token, and only adds pictures the
  owner has just declared to be duplicates of a picture already in that set.
- **Compensating controls.** `dry_run=true` default; the union is additive and
  reversible via the operation log; share tokens are minted only by the owner and
  only as `READ`; locked sets refuse the union outright (§4.1).
- **Ruling.** **Accepted** for the single-owner product, with one required
  documentation change: **§22.8 must state the share-token consequence** — that the
  membership union is a change to *who can see what*, and that auto-stack applies it
  in bulk. It currently documents the union as a metadata operation only, and the
  coverage matrix does not mention it at all. Recommended (not blocking): surface a
  `shared_sets_affected` count in the auto-stack dry-run response so the consent
  dialog can say so.
- **Owner:** backend / auth maintainer. **Revisit:** mandatory at the start of
  multi-user work, and immediately if bulk auto-stack is ever wired to run
  unattended (at import, or on a schedule) — unattended bulk membership widening is
  a different risk and this acceptance does not cover it.

### A2 — caller-supplied `batch_id` is taken verbatim

- **Risk.** `StackVerdictRequestModel.batch_id` / `SignatureRequestModel.batch_id` /
  `AutoStackRequestModel.batch_id` are stored with no uniqueness, ownership or
  op-type check. Two unrelated verdicts sent with the same id become one undo unit;
  supplying an id that belongs to a *foreign* batch (a scrapheap bulk move, say)
  grafts a dedup stack into it, so one Ctrl+Z reverses both.
- **Reproduced** (probe `test_C2`): two independent verdicts sent with
  `batch_id="shared-batch"` produce two `applied` rows in one batch; a single
  `undo_batch` reverses both, unstacking four pictures across two stacks.
- **Blast radius.** Owner-only, client-driven, fully reversible (redo restores).
  It is a usability footgun (one undo does more than the user expects), not a
  privilege issue.
- **Compensating control.** All operation routes are `OWNER_ONLY`; the log is
  append-only, so the mis-grouping is visible in `GET /operations`.
- **Ruling.** **Accepted.** Cheap hardening if you want it: reject a `batch_id`
  that already carries a different `op_type`.
- **Owner:** backend maintainer. **Revisit:** when the operation log becomes a
  user-visible activity feed (§21 / DAM 4.3), where a mis-grouped batch becomes a
  misleading audit entry.

### D1 — §22.9 is stale after the merge

`docs/backend_architecture.md` §22.9 says:

> The operation log ships on `feature/operation-log` and is **not on this branch**,
> so `dedup_verdict_service._record_operation` imports it lazily and logs a warning
> naming the missing module…

Both halves are now false: `develop-1.9` was merged into this branch, and
`dedup_verdict_service.py:89` imports `operation_log_service` at module level. There
is no lazy import and no warning path. A security-review document that describes a
mechanism the code does not have is a liability — fix the paragraph to describe what
ships (one operation row per verdict, one shared `batch_id` per bulk run, no row for
keep-separate/reopen), and fold in the R3 fix and the A1 consequence.

---

## 7. Release blockers, and what is not one

**Release blockers for PR #638 (all must land before merge):**

1. **R1** — signature must include the `size_bytes` co-key, with a regression test
   proving two same-sha/different-size groups stay distinct.
2. **R2** — `bulk_auto_stack` must either skip-and-report a `423` (matching its own
   API description) or become atomic; either way the response must carry the
   `batch_id` when anything committed. Regression test with a partial-lock bulk run.
3. **R3** — thread `request_context(request)` through the four verdict handlers.
4. **A1 doc** — §22.8 must record the share-token consequence of the membership
   union and that auto-stack applies it in bulk.
5. **D1 doc** — correct §22.9.

**Explicitly NOT blockers.** The authorization design is sound and I could not find a
scope hole: all eight routes deny every scoped token shape in both transports, no
sibling route leaks a dedup-derived fact, the coverage matrix is arithmetically
complete against an independent recount, the locked-set contract is fail-closed on
the single-verdict path including the folded-stack case, the stack-expanded undo
snapshot is non-vacuous, and there is no injection, no traversal, and no destructive
route on this surface. R4–R7 are hardening and a test gap; they can follow.

**Independence.** This sign-off was produced by a reviewer who wrote none of the
branch. Per the CLAUDE.md review process, the authors must **not** self-certify the
R1/R2/R3 fixes: re-run this document's reproductions and have a second party confirm
before merge.

---

## 8. Reproductions — how to re-run

Probes were written by this reviewer, run against a live `TestClient`/`Server`, and
**deleted** rather than committed (durable tests belong to the authors — R7 and the
R1/R2 regressions above name the ones that should be promoted).

```
PYTHONPATH=<worktree> /home/glindkvist/Projects/pixlstash/.venv/bin/python \
    -m pytest -s --fast-captions --force-cpu <paths>
```

| Probe | Covers | Result |
|---|---|---|
| `test_A1` | registry vs `api_endpoint_set` recount | 235 / 236 / 1 confirmed |
| `test_A2`, `test_A2b` | 8 routes × 2 token shapes × 2 transports, + owner 200 | all 403 / all 200 |
| `test_A3`, `test_A3b`, `test_A3c` | share-token widening + pre-existing control + bulk | A1 |
| `test_B1` | partial-lock, 7 state dimensions | fail-closed ✅ |
| `test_B2` | bulk auto-stack, locked group mid-run | **R2** |
| `test_B3`, `test_B4`, `test_B5` | signature / cover / exclusion / reopen abuse | all rejected ✅ |
| `test_B6`, `test_B6b` | folded-stack co-member: union coverage + lock | lock ✅; union gap noted |
| `test_C1` | scrapheaped sibling reversibility | correct, untested → **R7** |
| `test_C2` | batch-id grafting | A2 |
| `test_C3` | auto-stack batch undo | fully reversed ✅ |
| `test_D1`, `test_D4`–`test_D7` | scan spam, bad scopes, bounds, counts fan-out | **R4**, **R6** |
| `test_D2`, `test_D3` | tier-2 memory and scan complexity | **R5** |
| `test_E1`, `test_E2` | signature injectivity | **R1** |
| *(reverted-fix run)* | `_undo_targets` non-vacuity | fix is real ✅ |

**Author suites re-run on this branch:**

- `tests/test_architecture_guardrails.py` — **19 passed**
- `tests/test_dedup_verdict_service.py` — **28 passed**
- `tests/test_dedup_tier_service.py`, `tests/test_dedup_tiers_api.py`,
  `tests/test_dedup_verdict_service.py`, `tests/test_architecture_guardrails.py`,
  `tests/test_operation_log.py` — see §8.1
- `ruff check pixlstash` — **All checks passed!**

### 8.1 Combined suite

```
$ PYTHONPATH=<worktree> .venv/bin/python -m pytest -q --fast-captions --force-cpu \
    tests/test_dedup_tier_service.py tests/test_dedup_tiers_api.py \
    tests/test_dedup_verdict_service.py tests/test_architecture_guardrails.py \
    tests/test_operation_log.py

131 passed, 1 warning in 215.61s (0:03:35)
```

The branch is green as authored. Every finding above is a gap in what the tests
*assert*, not a test the authors broke — which is the point of an adversarial pass.

---

## Addendum (2026-07-30) — keep-separate is now op-logged (owner override)

The stance this review recorded — keep-separate writes **no** operation row
(it changes no reversible picture facet; a row would consume a `Ctrl+Z` for
nothing) and must never be reversed silently through a shared gesture batch id
(the "R5" batch-correlation concern folded into the #644 round) — was
**explicitly overridden by the owner on 2026-07-30**: keep-separate must be
undoable, symmetric with the stack verdict.

What shipped (branch `feature/keep-separate-undo`):

- `POST /dedup/verdicts/keep-separate` records one `dedup.keep_separate`
  operation via the operation log's empty-diff path (empty before/after
  payloads, member ids as targets) and stores its `batch_id` on the verdict row.
- Undo reopens the verdict and returns the group to the queue; redo re-decides
  it — both via the same post-restore hook the stack verdict uses, now
  registered per op_type and filtered per verdict kind.
- The *no-silent-reversal* half of the original concern still holds by
  construction: a shared gesture batch reverses a keep-separate only through
  its **own** operation, named in the undo response — never as a side effect of
  a sibling stack's restore. `reopen` remains unlogged (it is the explicit
  inverse action).

See `docs/backend_architecture.md` §22.10 and
`docs/integration_architecture.md` for the current contract.
