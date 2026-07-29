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

# 9. Re-verification (round 2, 2026-07-29) — two scopes

Certified by the same independent reviewer; the fix authors did not certify their
own work. Round 1 reviewed `feature/dedup-tiers` @ `21b07e64`. The branch was
rebased by the owner mid-round and the fix commit transplanted, so everything
below is re-derived against the **current remote head**, not against round 1's tree.

## 9.1 Final verdicts

| Branch / PR | Head | Verdict |
|---|---|---|
| `feature/dedup-tiers` (PR #638) | `029525a0` | **APPROVE** |
| `feature/gesture-batch-id` (PR #644) | `d634c869` | **APPROVE-WITH-REQUIRED-CHANGES** (one-word regex fix) |
| **Merge gate — applies to whichever of the two merges *second*** | — | **BLOCKING** (M1 below) |

New findings this round:

| # | Severity | What | Status |
|---|---|---|---|
| **M1** | **MEDIUM** | **Merge gate.** #644 establishes the `srv-` / `cli-` batch-id namespace; #638's dedup verdict routes take a caller-supplied `batch_id` from the request **body** with no validation, so a client can post `{"batch_id": "srv-…"}` and forge a server-namespaced id. Neither branch is defective alone. | **BLOCKING at merge** |
| **G1** | LOW | `sanitize_operation_batch_id` uses `re.match` + `$`, and Python's `$` matches before a trailing newline: `"cli-abcd\n"` is accepted verbatim and **persisted**. Reachable end to end. | **REQUIRED (#644)** |
| **W1** | LOW | The `MAX_PAIRS_PER_BUCKET` comment claims the cap loses "confidence *resolution* … not group membership". **False** — reproduced: 2 pictures dropped from grouping entirely. | Required comment fix |
| W2 | LOW | Folder scope: `scope_id` of `"/"`, `"\"`, `"///"` etc. `rstrip`s to `""`, so the LIKE prefix becomes `%` and the scope silently means *global* — the same class the escaping fix closed, via a different route. | Hardening |
| G2 | LOW | `POST /operations/batches/{batch_id}/undo` accepts any string and reflects it verbatim in the 409 `detail`. | Hardening |
| G3 | LOW | A client id reused across gestures merges unrelated operations into one undo unit; #644 additionally lets a client **override** the previously server-forced batch id on the two bulk scrapheap endpoints. | Accepted (see 9.5) |

---

## 9.2 Scope 1 — `feature/dedup-tiers` @ `029525a0`

### R1 — signature injectivity: **CLOSED**

`content_key` is now `f"{pixel_sha}:{size_bytes}"`. My round-1 E1 reproduction,
re-run verbatim as `test_V1`:

```
[V1] group A (sha=aaa size=100) = [1, 2]
[V1] group B (sha=aaa size=999) = [3, 4]
[V1] detected 2 group(s), 2 distinct signature(s)
[V1] persisted rows = [('7c7106052e', 'exact', [1, 2], False), ('a3c34404db', 'exact', [3, 4], False)]
[V1] after keep-separate on ONE signature = [('7c7106052e', … True), ('a3c34404db', … False)]
[V1] >>> groups resolved by that one verdict: 1 (want 1)
[V1] content_key sample = 'aaa:100'
[V1b] order independent: True
[V1b] size 100 vs 999 distinct: True
```

Two groups, two signatures, two persisted rows, and a verdict on one resolves
exactly one. All three round-1 consequences (group dropped from the queue,
keep-separate silencing both file sets, stack target depending on scan order) are
gone. The signature stays order-independent. The authors also promoted the
reproduction into a durable test and amended migration 0088 to clear stale rows.

### R2 — bulk fail-closed: **CLOSED, and the author's extra fix is real**

The author added a `session.rollback()` on the failed iteration, arguing it closes
a partial-write hazard my round-1 probe could not see. **They are right, and it is
the more important half of the fix.** My round-1 probe aborted the run at the first
423, so it never exercised what the *next* group's `session.commit()` would flush.

There are two distinct raise sites, and only the second exposes the hazard:

| Case | Guard | State pending when it raises |
|---|---|---|
| `test_V2` | `enforce_stack_membership_not_locked`, inside `_stack_members` | a `PictureStack` row already `add()`ed **and `flush()`ed** |
| `test_V2b` | `enforce_pictures_not_locked`, inside the metadata union | **both pictures already reparented** (`stack_id` / `stack_position` set and flushed) *plus* the new stack row |

`test_V2b` is the deep one — I arranged for the locked set to already contain
every resulting member so `enforce_stack_membership_not_locked` passes cleanly and
the 423 comes from the union, after `_stack_members` has done all its work:

```
[V2b] outcomes = groups=2 blocked=1 failed=0
[V2b] failures = [{"signature": "7ff931c650…", "outcome": "blocked", "status_code": 423,
                   "error": {"code": "pictures_locked", "action": "union duplicate metadata",
                             "sets": [{"id": 1, "name": "Frozen"}], "picture_ids": [3, 4]}}]
[V2b] pictures after = [(1, 1, 0), (2, 1, 1), (3, None, None), (4, None, None), (5, 2, 0), (6, 2, 1)]
[V2b] >>> locked group rows (want stack_id None) = [(3, None, None), (4, None, None)]
[V2b] >>> orphan PictureStack rows = []
```

No stowaway: the locked group's pictures are untouched and **zero** orphan
`PictureStack` rows leaked into the next group's commit. Without the rollback,
group 3's commit would have flushed group 2's reparenting. And the rollback does
not over-reach:

```
[V2c] group 1 (committed before the failure) = [(1, 1, 0), (2, 1, 1)]
[V2c] group 3 (committed after  the failure) = [(5, 2, 0), (6, 2, 1)]
```

The shallower site is equally clean, and the response shape is now legible:

```
[V2] outcomes = groups=2 blocked=1 failed=0
[V2] batch_id returned = '2fe0ed96e05c4f91b4cdfc3575f90ee9'
[V2] failures = [{… "outcome": "blocked", "status_code": 423, "error": {"code": "set_locked", …}}]
[V2] >>> locked group rows (want stack_id None) = [(3, None, None), (4, None, None)]
```

`batch_id` is always returned, every group is reported under exactly one of
`applied` / `blocked` / `failed`, and the route's API description is now true.
**Credit where due: the rollback is a defect the authors found that I missed.**

### R3 — `request_context`: **ACCEPT the author's narrowing**

The author wired `request_context` on the two *recording* handlers only
(`post_stack_verdict`, `post_auto_stack`) and argues keep-separate/reopen provably
record nothing, so threading an actor there would be dead code. **Adjudication:
accept.** The argument is stronger than "the diff would be empty" — the code path
is *absent*, which I checked structurally rather than taking on trust:

```
[V3b] apply_keep_separate_in_session: record/capture calls = []
[V3b] reopen_verdict_in_session:      record/capture calls = []
[V3b] operations after keep-separate + reopen = []
```

(an AST walk over each function for any `record*` / `capture*` call, plus the
behavioural check). And the two routes that *do* record now carry full provenance:

```
[V3] after POST /dedup/verdicts/stack (X-Client-Id: tab-abc):
[V3]   op_type=dedup.stack actor='1' source='ui' origin='tab-abc'
[V3] after POST /dedup/auto-stack (X-Client-Id: tab-xyz):
[V3]   op_type=dedup.stack actor='1' source='ui' origin='tab-abc'
[V3]   op_type=dedup.stack actor='1' source='ui' origin='tab-xyz'
```

Note the second run's earlier row keeps its *own* origin rather than being
rewritten — correct. I do **not** require all four handlers, on two conditions that
are already met: the comments on both non-recording handlers point at
`post_stack_verdict`, and `test_keep_separate_and_reopen_record_no_operation` pins
the premise, so the day keep-separate starts recording, that test fails and the
omission surfaces.

### R4 / section D — scope validation and caps: **CLOSED**

```
[V4] project    'not-an-int'            scan=400 groups=400 counts=400 auto=400
[V4] project    '1; DROP TABLE picture' scan=400 groups=400 counts=400 auto=400
[V4] project    ''                      scan=400 groups=400 counts=400 auto=400
[V4] project    '1.5'                   scan=400 groups=400 counts=400 auto=400
[V4] project    '0x10'                  scan=400 groups=400 counts=400 auto=400
[V4] project    ' 1 '                   scan=200 groups=200 counts=200 auto=200
…identical for set and character…
[V4] persisted dedupscan rows = [(1, 'project:1', 'pending'), (2, 'set:1', 'pending'), (3, 'character:1', 'pending')]
```

Every malformed id is a 400 at the boundary on all four routes, and **no poison
row is persisted**. My probe initially asserted zero scan rows and failed; that was
my error, not the code's — `" 1 "` is a *valid* integer to Python and the author
normalises it via `str(int(...))`, so the persisted `scope_key` is the canonical
`project:1`. Correct behaviour, no scope-key duplication.

LIKE escaping (`test_V4b`), and the counts cap (`test_V4c`):

```
[V4b] folder exact folder    ('/vault/real' ) -> 200 total=1
[V4b] folder pct wildcard    ('%'           ) -> 200 total=0
[V4b] folder underscore      ('_'           ) -> 200 total=0
[V4b] folder pct suffix      ('/vault/rea%' ) -> 200 total=0
[V4b] folder underscore mid  ('/vault/rea_' ) -> 200 total=0
[V4b] folder backslash       ('\'           ) -> 200 total=1   <<< see W2
[V4c] 200 scopes (cap=200) -> 200 | 201 -> 422 | 5000 -> 422
```

#### W2 (new, LOW) — a residual in the same folder-scope prefix

The escaping is correct, but it runs *after* `prefix = scope_id.rstrip("/\\")`, and
a `scope_id` consisting only of separators strips to the empty string:

```
scope_id='/vault/real' -> prefix='/vault/real'  LIKE '/vault/real%'
scope_id='%'           -> prefix='%'            LIKE '\%%'
scope_id='\'           -> prefix=''             LIKE '%'      <<< MATCHES EVERYTHING
scope_id='/'           -> prefix=''             LIKE '%'      <<< MATCHES EVERYTHING
scope_id='///'         -> prefix=''             LIKE '%'      <<< MATCHES EVERYTHING
scope_id='/\/'         -> prefix=''             LIKE '%'      <<< MATCHES EVERYTHING
```

Same class the escaping fix closed — "Find duplicates in this folder" silently
meaning *everywhere* — reached by a different input. Owner-only and read-only, so
LOW. Fix: reject an empty post-strip prefix in `__post_init__` alongside the
numeric validation. (A root scan is already expressible as `scope_type=global`.)

### W1 (new, LOW) — the Tier-2 pair cap **does** drop group membership

`MAX_PAIRS_PER_BUCKET = 50_000` is correctly introduced and warned. But its
in-code justification asserts a guarantee it does not have:

> The retained pairs are the lowest-offset ones, which still connect every
> matching member into the same component; the loss is confidence *resolution* …
> **not group membership**.

Pairs are emitted in increasing id-offset order and the loop `break`s on the cap,
so **any match at an offset beyond where the cap bites is never generated**. A
bucket of 698 mutually-identical pictures (which saturates the cap around offset
72) plus two genuine duplicates at opposite ends of the id range:

```
[W1] MAX_PAIRS_PER_BUCKET = 50000
[W1] bucket = 1 outlier + 698 identical + 1 outlier = 700 members
[W1] the two outliers ((1, 700)) match ONLY each other, at offset 699
[W1] dense-block pairs = 243,253 (>> the cap)
[W1] CONTROL (cap effectively off): 2 group(s)
[W1]   outlier pair present: True -> [(1, 700)]
[W1] REAL CAP (50000): 1 group(s)
[W1]   outlier pair present: False -> []
[W1] pictures grouped with cap off = 700, with cap on = 698
[W1] >>> MEMBERS LOST TO THE CAP: [1, 700]
[W1] >>> author's claim 'not group membership' holds: False
```

A whole genuine duplicate group vanishes. The claim *is* true for the motivating
case the comment describes, which I confirmed separately —

```
[W2] all-identical bucket of 600 -> group sizes [2, 600]
[W2] >>> every member connected: True
```

— because there the offset-1 chain alone spans the component. It is false in
general.

**Impact is LOW** (a missed suggestion in an opt-in tier, on a bucket needing
≥~320 mutually-matching members *plus* a long-range match). **The comment is the
problem**, not the behaviour: a future maintainer reading "not group membership"
will trust a guarantee that does not exist. **Required: correct the comment and the
`MAX_PAIRS_PER_BUCKET` docstring** to say membership is preserved only for matches
below the truncation offset. Optional real fix: keep scanning offsets after the cap
but record only pairs joining a member that has no pair yet.

`MAX_TRACKED_PAIRS` behaves as claimed (`test_W3`, cap patched to 100 to reach it):

```
[W3] WARNING: [dedup-scan] scan 1 reached the 100 tracked-pair cap at bucket 1 of 6;
     further cross-bucket chaining is dropped and some chains may be reported as
     separate groups. Narrow the scope or raise MAX_TRACKED_PAIRS.
[W3] scan status = ('complete', 6, 6, 1, None)
[W3] >>> cap is warned, not silent: True | scan still completed: complete
```

Warned, named, non-fatal, scan completes. **Claim holds.**

**One partial fix, non-blocking.** The incremental-persist change narrows which
groups are *written* after each bucket, but `groups_from_pairs` is still called
over the **whole** `pair_cache` on every bucket that touched anything — the
O(buckets × total_pairs) *derivation* from round-1 R5 remains, on the single DB
writer thread. Halves the problem. Recorded as remaining hardening.

### A2 — I now **insist**, as a merge gate (M1)

Round 1 I ruled the caller-supplied `batch_id` an accepted risk and offered a cheap
hardening (reject an id already carrying a different `op_type`), which the authors
declined as accepted-not-required. **That ruling was correct for #638 in isolation
and is now superseded by #644**, which introduces a namespace whose whole purpose
is that a client cannot forge a server batch id. Dedup's body field defeats it:

```
[X1] batch_id='srv-deadbeefdeadbeefdeadbeefdeadbeef' -> 200 stored='srv-deadbeefdeadbeefdeadbeefdeadbeef'
[X1] operation rows = [('dedup.stack', 'srv-deadbeefdeadbeefdeadbeefdeadbeef'), ('dedup.stack', 'cli-legit1234')]
[X1] >>> a client-supplied 'srv-' batch id reached the log: True
```

and dedup mints a third, un-namespaced shape from its own separate function:

```
[X2] server-minted auto-stack batch_id = '6e0be50b3b094788b1163e31033ff0ec'
[X2] starts with 'srv-': False | starts with 'cli-': False
[X2] >>> dedup mints bare hex, outside BOTH namespaces
```

So after both merge there are three id shapes in one column, #644's guard is
bypassable through `POST /dedup/verdicts/*`, and its comment ("nothing a client
sends can match that pattern … a future reader can tell the two apart in the log")
becomes false.

**M1 — required in whichever PR merges second (BLOCKING):**

1. Route **every** caller-supplied batch id through the single
   `sanitize_operation_batch_id` — the three dedup verdict/auto-stack body fields,
   and `SweepDryRunRequest.operation_batch_id` when it stops being inert.
2. Make `dedup_verdict_service.new_batch_id` delegate to
   `operation_log_service.new_batch_id` so dedup's ids carry `srv-` too, rather
   than being a second minting function.

This is a defect in *neither branch alone* and must not be filed against either
author; it is a seam that only exists once they meet.

---

## 9.3 Scope 2 — `feature/gesture-batch-id` (PR #644) @ `d634c869`

Backend surface is three files: `utils/request_origin.py` (the guard),
`services/operation_log_service.py` (`srv-` minting + `request_context`'s new
`batch_id` key), `routes/pictures/_crud.py` (two scrapheap sites). No
`authz/registry.py` change. `AUTHZ_GATE_ENFORCING` is `True` — verified in source
and behaviourally in G3, correcting a reconnaissance note that read it as `False`.

### The namespace guard — 28 forgery / smuggling cases, one acceptance

```
[G1] operation_log_service.new_batch_id() = 'srv-84105f15989c4d76b919caee019591b4'
[G1] server id verbatim   -> None      [G1] srv- forged        -> None
[G1] SRV- upper           -> None      [G1] Cli- mixed case    -> None
[G1] cli- legit           -> 'cli-abcd1234'
[G1] cli- min len (4)     -> 'cli-abcd'    [G1] cli- 3 chars    -> None
[G1] cli- max (76)        -> accepted      [G1] cli- 77 chars   -> None
[G1] oversize 5000        -> None      [G1] no namespace       -> None
[G1] path traversal       -> None      [G1] slash              -> None
[G1] encoded slash        -> None      [G1] dotdot only        -> None
[G1] space                -> None      [G1] TAB                -> None
[G1] LF middle            -> None      [G1] CR trailing        -> None
[G1] CRLF header inject   -> None      [G1] NUL                -> None
[G1] unicode digits       -> None      [G1] unicode homoglyph  -> None
[G1] RTL override         -> None      [G1] empty / just prefix / None -> None
[G1] LF trailing  'cli-abcd\n'  -> 'cli-abcd\n'   <<< ACCEPTED WITH A DANGEROUS CHARACTER
[G1b] >>> never truncates (drop, not trim): True
```

A server-minted id, every `srv-` forgery, case variants, path characters, encoded
slashes, whitespace, control bytes, NUL, unicode homoglyphs and RTL overrides are
all rejected. Over-length values are **dropped, never truncated**, so a crafted long
value cannot collide with a legitimate short one. Duplicate headers take the first:

```
[G2c] two X-Operation-Batch-Id headers (cli- then srv-) -> 200
[G2c] ops = [(1, 'pictures.tags.add', 'cli-firstone1', 'applied', '1')]
```

#### G1 (REQUIRED, LOW) — trailing bare LF is accepted and persisted

`_CLIENT_BATCH_ID_RE.match(raw)` with `^…$`. Python's `$` matches **before a single
trailing newline**, so `"cli-abcd\n"` passes and is returned verbatim. It is not
theoretical — it survives the HTTP layer and reaches the database:

```
[G2b] LF -> 200
[G2b] rows with a control char in batch_id = [(1, 'pictures.tags.add', 'cli-abcd\n', 'applied', '1')]
[G2b] all stored ids = ['cli-abcd\n']
```

The value is then echoed in every operations API payload (`serialize()`), written
into the INFO log line `operation_log: recorded … batch=%s …` (a spurious line
break in the log; nothing can follow the `\n`, so a full forged log record is not
constructible), and would be placed in `/operations/batches/{batch_id}/undo` by the
client. It directly contradicts the function's own docstring — *"the charset keeps
the value safe to log and to put in a URL path"*.

**Severity LOW** (the `cli-` namespace still holds, so no forgery and no authority
change) but the fix is one word and this function exists to be the guard:

```python
if len(raw) > MAX_OPERATION_BATCH_ID_LENGTH or not _CLIENT_BATCH_ID_RE.fullmatch(raw):
```

(or `\Z` in place of `$`). Add `"cli-abcd\n"` to
`test_a_client_batch_id_can_never_impersonate_a_server_minted_one`'s reject list —
the existing case list is thorough and simply lacks this one.

### Grouping never widens write authority — **CONFIRMED**

A set-scoped READ token sharing a batch id with an owner operation, across write,
delete, undo, batch-undo and read:

```
[G3] owner op under cli-sharedgesture1: [(1, 'pictures.tags.add', 'cli-sharedgesture1', 'applied', '1')]
[G3] scoped POST tags (in-scope pic)     -> 403
[G3] scoped POST tags (out-of-scope)     -> 403
[G3] scoped DELETE picture               -> 403
[G3] scoped POST operations/undo         -> 403
[G3] scoped POST batch undo              -> 403
[G3] scoped GET operations               -> 403
[G3] ops after scoped attempts = [(1, …, 'cli-sharedgesture1', 'applied', '1')]
[G3] >>> scoped token wrote into the batch: False
```

Structurally sound rather than accidentally sound: a resource-scoped token is
always `READ`, so it cannot record an operation at all, and every operations route
is `OWNER_ONLY`. Sharing a batch id confers nothing. **No cross-principal reuse is
reachable** in the single-owner product — the only principal that can write an
operation row is the owner.

### The middleware ignore-path never 500s and never truncates — **CONFIRMED**

```
[G6] unauth /version + auth /pictures with legit    -> 404 / 200
[G6] unauth /version + auth /pictures with garbage  -> 404 / 200   (raw \x01\x02)
[G6] unauth /version + auth /pictures with huge     -> 404 / 200   (9000 chars)
```

No value produced a 500, on authenticated or unauthenticated routes, and every
rejected value was dropped rather than trimmed (`test_G1b`). The middleware runs
before auth on every request, which is inert: the state attribute is only read by
handlers that already require an authenticated owner.

#### G2 (hardening, LOW) — the batch-undo 409 reflects caller input

```
[G5] batch_id=legit      -> 409 {"detail":"Batch cli-realbatch1 has nothing to undo"}
[G5] batch_id=sql-ish    -> 409 {"detail":"Batch ' OR 1=1 -- has nothing to undo"}
[G5] batch_id=long       -> 409 {"detail":"Batch zzzz…(3000 chars) has nothing to undo"}
[G5] batch_id=unicode    -> 409 {"detail":"Batch cli-абвг has nothing to undo"}
```

`POST /operations/batches/{batch_id}/undo` has no pattern or length bound on the
path parameter and echoes it verbatim. The query itself is parameterised (no
injection) and the response is JSON, so this is not XSS server-side — but the
frontend must not render `detail` as HTML, and an unbounded reflected string is
avoidable. Suggest bounding the path param and not echoing it.

### 9.4 `dedup_sweep_service.operation_batch_id` — no guard needed *now*

Verified inert on this branch: `dedup_sweep_service` contains **zero** references
to the operation log, runs under `run_immediate_read_task` (no write session), and
the value is used only in the returned dataclass and one log line. Nothing connects
it to `request.state.operation_batch_id` or to `Operation.batch_id`.

**Ruling: it does not need the namespace guard today** — guarding a field that
reaches nothing would be ceremony, and the existing tests
(`test_operation_batch_id_round_trips_and_nothing_is_written`) correctly pin it as
an echo. **It must get the guard in the same change that makes it live**, and it is
listed in M1 item 1 so the requirement is recorded now rather than rediscovered
later. Add a comment on the field saying so.

---

## 9.5 Accepted risks, re-adjudicated

**A1 (share-token widening) — unchanged, still accepted.** Not re-probed; nothing
in either fix commit touches `reconcile_stack_membership`. The §22.8 documentation
requirement from round 1 stands.

**A2 (caller-supplied batch id) — PROMOTED to blocking as M1.** See 9.2. For the
part of A2 that is *not* forgery — grouping unrelated work into one undo — I still
accept it, with the blast radius now larger than in round 1:

```
[G4] three unrelated gestures sent the SAME id 'cli-reused12345'
[G4] one POST /operations/undo reverted 3 operation(s)
[G4b] DELETE /pictures (bulk scrapheap) under 'cli-mixedbatch99' -> 200
[G4b]   id=1 op=pictures.tags.add        batch='cli-mixedbatch99'
[G4b]   id=2 op=pictures.scrapheap.move  batch='cli-mixedbatch99'
[G4b] one undo reverted: ['pictures.scrapheap.move', 'pictures.tags.add']
```

`G4b` is a **behavioural change** in #644 worth stating plainly: the two bulk
scrapheap endpoints previously forced a server-minted `batch_id`; they now use
`request_context(request, fallback_batch_id=…)`, and the client header **wins**
(`or`, not "only if absent"). So a client can put a bulk scrapheap move and an
unrelated tag edit in one undo unit.

- **Blast radius.** Owner-only; every affected operation is reversible and the log
  is append-only, so the mis-grouping is visible in `GET /operations`. The worst
  outcome is a surprising undo, not data loss.
- **Compensating controls.** `cli-` namespace; all operations routes `OWNER_ONLY`;
  redo restores.
- **Owner:** backend maintainer. **Revisit:** when the operation log becomes a
  user-visible activity feed (§21 / DAM 4.3), where mis-grouped batches become
  misleading audit entries — and immediately if a non-owner principal can ever
  record an operation.

---

## 9.6 Round-2 test log

Author suites, re-run by this reviewer at each head:

```
# feature/dedup-tiers @ 029525a0
$ pytest -q --fast-captions --force-cpu tests/test_dedup_tier_service.py \
    tests/test_dedup_tiers_api.py tests/test_dedup_verdict_service.py \
    tests/test_architecture_guardrails.py tests/test_operation_log.py
150 passed, 1 warning in 221.21s (0:03:41)      # was 131 in round 1; +19 new author tests

# feature/gesture-batch-id @ d634c869
$ pytest -q --fast-captions --force-cpu tests/test_operation_log.py \
    tests/test_architecture_guardrails.py
65 passed, 1 warning in 133.16s (0:02:13)

$ ruff check pixlstash
All checks passed!
```

Reviewer probes (written this round, run, then deleted):

| Probe | Covers | Result |
|---|---|---|
| `test_V1`, `test_V1b` | R1 signature injectivity | **CLOSED** |
| `test_V2`, `test_V2b`, `test_V2c` | R2 both 423 raise sites + rollback scope | **CLOSED**; author's rollback confirmed load-bearing |
| `test_V3`, `test_V3b` | R3 provenance + AST proof of the absent path | **ACCEPTED as narrowed** |
| `test_V4`, `test_V4b`, `test_V4c` | scope 400s, no poison row, LIKE escaping, counts cap | **CLOSED**; W2 residual found |
| `test_W1`, `test_W2` | pair-cap membership claim | **claim REFUTED** |
| `test_W3` | tracked-pair cap warning path | claim holds |
| `test_X1`, `test_X2` | dedup body batch id vs the namespace | **M1** |
| `test_G1`, `test_G1b` | 28 forgery / smuggling cases | 1 acceptance → **G1** |
| `test_G2`, `test_G2b`, `test_G2c` | over-the-wire, control chars, duplicate headers | LF reaches the DB |
| `test_G3` | scoped token vs a shared batch | **no widening** |
| `test_G4`, `test_G4b` | id reuse; scrapheap override | A2 (accepted) |
| `test_G5` | batch-undo path parameter | G2 (hardening) |
| `test_G6` | middleware never 500s / never truncates | holds |

**Independence.** Round 2 was performed by the round-1 reviewer, who wrote no code
on either branch. M1 and G1 are new required changes and must be certified by
someone other than whoever implements them.

---

# 10. Round 3 (2026-07-29) — targeted confirm, `feature/dedup-tiers` @ `b74bbd31`

Five commits on top of the head approved in round 2 (`029525a0`). Scope: the
security-relevant parts of the QA-blocker fixes plus M1. Re-derived against
`b74bbd31`, not against round 2's tree.

## 10.1 Verdict

# **APPROVE-WITH-ONE-REQUIRED-CHANGE** — #638 @ `b74bbd31`

| Item | Verdict |
|---|---|
| 1. **M1** — batch-id namespacing + client validation | **CLOSED** |
| 2. **Post-restore hook** — op_type selection | **SAFE** |
| 2. **Post-restore hook** — atomic abort on raise | **SAFE** |
| 2. **Post-restore hook** — batch-id correlation scope | **DEFECT — R5, required** |
| 3. **Cursor** — malformed input, 400 never 500 | **SAFE** (one comment-accuracy nit, R6) |
| 3. **Cursor** — cannot bypass scope/authz filtering | **SAFE** |
| 4. **W2** — slash-only folder scopes | **CLOSED** |

| # | Severity | What | Status |
|---|---|---|---|
| **R5** | **MEDIUM-LOW** | The post-restore hook correlates purely on `batch_id`, so undoing a stack verdict **also reopens every other verdict sharing that batch id** — including a `keep_separate` that recorded no operation at all. Reproduced. The authors' isolation test passes only because it supplies no `batch_id`. | **REQUIRED** |
| R6 | LOW | `decode_queue_cursor` accepts non-finite floats: `1\|inf\|0` returns the **first page again** — the exact "silent restart from the top" its own docstring says it refuses. | Hardening |

---

## 10.2 Item 1 — M1 (my round-2 blocking item): **CLOSED**

Both minting sites are namespaced and the third bare-hex shape is gone:

```
[C1] operation_log_service.new_batch_id() = 'srv-9e51e35586324b8d9dfe6c16c19cd64d'
[C1] dedup_verdict_service.new_batch_id()  = 'srv-929acdc7972b4a7984bb14a662f15e7e'
[C1b] stack with no batch_id -> 200 batch='srv-c49be517cae04338b45129f9b0707368'
```

My round-2 `srv-` impersonation repro is refused, and I extended it to **all four**
dedup bodies × 13 forged shapes (`srv-…` long and short, `SRV-`, `Cli-`,
un-namespaced, too short, too long, trailing LF, slash, space, unicode, empty,
traversal) — **52/52 rejected with 400**:

```
[C1] route x forged batch_id -> status (want 400 for every one)
[C1]   verdicts/stack           [400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400]
[C1]   verdicts/keep-separate   [400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400]
[C1]   verdicts/reopen          [400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400]
[C1]   auto-stack               [400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 400]
[C1] >>> forged ids accepted anywhere: []
[C1] valid cli- id accepted: stack 200 | keep-separate 200 | reopen 200 | auto-stack 200
[C1] >>> stored batch ids = ['cli-goodgesture1']
```

The reopen route — which I was asked to probe specifically — validates identically.
Positive direction holds too: a well-formed `cli-` id is accepted on all four, so
this is not an over-block.

**Note for the #644 merge.** This validator uses `CLIENT_BATCH_ID_RE.fullmatch`,
so it does **not** have the trailing-LF hole I raised as G1 against
`feature/gesture-batch-id`'s `re.match` + `$` (`cli-abcd\n` → 400 here, accepted
there). The duplication is deliberate and documented in the constant's docstring,
which names `utils/request_origin.py` as the future single home. **G1 still stands
against #644**, and when the two unify, the surviving implementation must be the
`fullmatch` one. M1 item 2 (delegating `new_batch_id`) is done; M1 item 1 is done
for the three verdict bodies, and `SweepDryRunRequest.operation_batch_id` remains
correctly out of scope while it is inert (§9.4).

## 10.3 Item 2 — the post-restore hook

### op_type selection: **SAFE**

Hooks dispatch on `Operation.op_type`. Nothing user-controllable reaches it:

```
[C2a] _record_operation(op_type=...) -> Name(id='OP_TYPE_STACK', ctx=Load())
[C2a] registered hooks = ['dedup.stack']
[C2a] 'op_type' occurrences in pixlstash/routes/:
  (none)
```

The value is a module constant at the single call site (AST-checked, not read by
eye), and the string `op_type` does not appear anywhere in `pixlstash/routes/` —
no handler accepts, forwards or defaults it. Registration is import-time and
keyed by that constant, and `register_post_restore_hook` is reachable only from
Python, not from a request. A hook cannot be made to fire for an op_type it does
not own, and exactly one hook is registered.

### Atomic abort on a raising hook: **SAFE**

I replaced the registered hook with a raiser and undid a real stack verdict:

```
[C2b] after verdict: pics=[(1, 1), (2, 1), (3, None), …]
[C2b]   ops=[(1, 'dedup.stack', 'srv-6b6dd00c…', 'applied')]
[C2b]   verdicts=[('d0a2bcaf', 'stacked', 'srv-6b6dd00c…', live=True)]
[C2b]   groups=[('7c45834a', False), ('d0a2bcaf', True), …]
[C2b] undo with a raising hook -> 500
[C2b] after: pics=[(1, 1), (2, 1), (3, None), …]          <- identical
[C2b]   ops=[(1, 'dedup.stack', 'srv-6b6dd00c…', 'applied')]  <- still 'applied'
[C2b]   verdicts=[('d0a2bcaf', 'stacked', 'srv-6b6dd00c…', live=True)]
[C2b]   groups=[('7c45834a', False), ('d0a2bcaf', True), …]
[C2b] >>> fully atomic (nothing changed, op stays 'applied'): True
```

All four state dimensions (pictures, operations *including* `status`, verdict rows,
group `resolved` flags) are byte-identical after the failed undo. No partial
group-reopen, no half-restored pictures, operation stays `applied` so the user can
retry. The hook runs inside `_restore` before `_mark_undone` and the commit, and
the DB worker's `session.rollback()` catches it. **Fail-closed as claimed.**

### R5 (REQUIRED) — batch-id correlation reopens verdicts the operation never touched

`restore_verdicts_in_session` correlates **only** on `batch_id`:

```python
batch_ids = sorted({op.batch_id for op in operations if op.batch_id})
…
select(DedupVerdict).where(DedupVerdict.batch_id.in_(chunk))
```

Every `DedupVerdict` sharing that id is reopened — not only the ones the restored
operations recorded. The gap is `keep_separate`, which **stores a `batch_id` but
records no operation at all**, so nothing in the restore set corresponds to it.

Reproduction (`test_C2c`): one client gesture, one `cli-` id, two verdicts — a
keep-separate on group A and a stack on group B. This is exactly the usage the
`cli-` namespace exists to enable and that `SignatureRequestModel.batch_id`
documents for keep-separate.

```
[C2c] keep-separate(7c45834a) -> 200
[C2c] stack(d0a2bcaf)        -> 200
[C2c] ops (note: keep-separate records NONE) = [(1, 'dedup.stack', 'cli-onegesture1', 'applied')]
[C2c] verdicts before undo = [('7c45834a', 'keep_separate', 'cli-onegesture1', live=True),
                              ('d0a2bcaf', 'stacked',       'cli-onegesture1', live=True)]
[C2c] groups   before undo = [('7c45834a', True), ('d0a2bcaf', True), …]
[C2c] POST /operations/undo -> 200
[C2c] verdicts after undo  = [('7c45834a', 'keep_separate', 'cli-onegesture1', live=False),
                              ('d0a2bcaf', 'stacked',       'cli-onegesture1', live=False)]
[C2c] groups   after undo  = [('7c45834a', False), ('d0a2bcaf', False), …]
[C2c] >>> keep-separate still live (want True): False
[C2c] >>> its group still resolved (want True): False
```

**Undoing the stack verdict silently reversed an unrelated "keep separate"
decision** — a decision §22.8 and the route's own description call *permanent until
reopened from the Stacks view*. Group A returns to the queue and re-asks the user a
question they already answered. `redo` is symmetric: it re-decides the
keep-separate, so a group the user reopened by hand can be silently re-resolved.

The baseline behaves correctly, so this is over-reach and not a broken hook
(`test_C2d`): a stack verdict with a server-minted id reopens exactly its own
verdict on undo and re-decides exactly it on redo.

**Why the authors' test misses it.**
`test_an_undo_does_not_reopen_a_group_it_never_touched` is a genuine test of the
right property, but it posts **no** `batch_id` on either call — so the
keep-separate row stores `NULL` and the stack gets a distinct `srv-` id, the `IN`
never matches, and isolation holds trivially. The test is true and not general.
Mine is the same scenario with `batch_id="cli-onegesture1"` on both requests.

**Severity MEDIUM-LOW.** Not an authz or confidentiality issue: owner-only,
non-destructive (a reopened group returns to the queue; no picture, file or tag is
touched), and visible in the queue rather than silent to the user. But it reverses
a decision the product promises is permanent, it is reachable by *correct* client
behaviour rather than only by abuse, and it gets more likely as #644 ships, since
that feature's whole purpose is one id across a gesture's requests.

**Fix — smallest correct change:** filter the correlation to the verdict kind that
actually records the op_type:

```python
select(DedupVerdict).where(
    DedupVerdict.batch_id.in_(chunk),
    DedupVerdict.verdict == VERDICT_STACKED,
)
```

Only `stack` records `OP_TYPE_STACK`, so this is exact for every case I could
construct: two stack verdicts sharing a batch are always restored together anyway
(`_batch_members_in_session` pulls the whole batch), and an already-undone stack
verdict is filtered out by the `status == applied` predicate, so re-reopening
cannot occur. **Also recommended:** stop storing `batch_id` on a keep-separate
verdict row at all — it drives nothing and exists only as this hazard — and add
the shared-`cli-`-id case to `test_an_undo_does_not_reopen_a_group_it_never_touched`.

## 10.4 Item 3 — the cursor

### Malformed / forged cursors: **400 or 422, never 500**

17 fuzz cases against `GET /dedup/groups?cursor=`:

```
[C3] empty                -> 200  (falsy -> treated as no cursor; first page)
[C3] not base64           -> 400 {"detail":"malformed queue cursor '!!!!not-base64!!!!': 'ascii' codec…
[C3] valid b64, garbage   -> 400 {"detail":"malformed queue cursor 'Z2FyYmFnZQ': not enough values to u…
[C3] wrong version        -> 400 {"detail":"…: unsupported cursor version…
[C3] no version           -> 400 {"detail":"…: unsupported cursor version…
[C3] too few fields       -> 400 {"detail":"…: not enough values to unpack…
[C3] too many fields      -> 400 {"detail":"…: too many values…
[C3] float id             -> 400 {"detail":"…: invalid literal for int…
[C3] sql-ish              -> 400 {"detail":"…: invalid literal for int…
[C3] unicode payload      -> 400 {"detail":"…: 'ascii' codec can't encode…
[C3] null byte            -> 400 {"detail":"…: invalid literal for int…
[C3] huge id              -> 422 (string_too_long, MAX_CURSOR_LENGTH)
[C3] over max length      -> 422 (string_too_long)
[C3] nan confidence       -> 200 {"groups":[],"total":4,…}        <- see R6
[C3] inf confidence       -> 200 {"groups":[{…first page…}]}      <- see R6
[C3] -inf confidence      -> 200 {"groups":[],…}                  <- see R6
[C3] negative id          -> 200 first page                       <- benign
[C3] >>> 5xx responses: []
[C3] cursor + offset together -> 400 {"detail":"cursor and offset are mutually exclusive; …"}
```

No 5xx on any input. The length bound is enforced by the FastAPI `Query`
constraint before the decoder sees the string, so an unbounded value never reaches
base64. `cursor` + `offset` together is a 400 as documented.

#### R6 (hardening, LOW) — non-finite floats survive the decoder

`decode_queue_cursor` ends in `float(confidence)`, which happily parses `inf`,
`-inf` and `nan`. `1|inf|0` makes the keyset predicate `confidence < inf` true for
every row, so the caller **silently gets the first page again** — precisely the
failure the function's own docstring says it refuses:

> A bad cursor is a 400, never a silent restart from the top — silently paging
> from offset 0 would hand the client the same page forever.

`nan` / `-inf` silently return an empty page, which a client reads as
end-of-queue. Neither leaks anything (the scope and policy filters still apply —
see below) and neither is a 500, so this is LOW; but it is the same
comment-asserts-a-guarantee-the-code-lacks pattern as W1. One-line fix: reject
`not math.isfinite(value)` in the decoder, and add `1|inf|0` to
`test_a_malformed_cursor_is_a_400_not_a_silent_restart`.

### Cursor cannot bypass scope or policy filtering: **SAFE**

The cursor carries only `(confidence, group_id)` — no scope, no tier, no
threshold, no filter state. `page_queue_in_session` builds the scope predicate and
the tier/threshold filters from the **request**, then ANDs the keyset predicate, so
the cursor can only advance a position inside an already-filtered query. Verified
by replaying a cursor minted under the global scope against a set-scoped request:

```
[C3b] GLOBAL page1: total=4 groups=['d0a2bcaf']
[C3b] next_cursor = 'MXwxfDE'
[C3b] SET-scoped (no cursor):        total=1 groups=['d0a2bcaf']
[C3b] SET-scoped + GLOBAL cursor:    total=1 groups=[]
[C3b] >>> groups the cursor smuggled past the scope filter: []
[C3b] >>> replayed total still the scoped total: True
```

Zero out-of-scope groups, and `total` stays the scoped total rather than the global
one. All these routes are `OWNER_ONLY` regardless, so this is defence in depth, but
the structure is right: a cursor is a position, not a capability.

## 10.5 Item 4 — W2: **CLOSED**

```
[C4] folder real folder        ('/vault') groups=200 total=4 scan=200
[C4] folder slash              ('/'     ) groups=400 scan=400
[C4] folder backslash          ('\'     ) groups=400 scan=400
[C4] folder triple slash       ('///'   ) groups=400 scan=400
[C4] folder mixed seps         ('/\/'   ) groups=400 scan=400
[C4] folder double backslash   ('\\'    ) groups=400 scan=400
[C4] folder empty              (''      ) groups=400 scan=400
[C4] folder pct                ('%'     ) groups=200 total=0 scan=200
```

Every separator-only id is refused at the boundary on both the read and the write
route — no silent global scan, and no poison scan row. `%` still correctly returns
`total=0` (escaped, matches nothing) rather than being rejected, which is right: it
is a legal, if pointless, literal folder name. Real folders unaffected.

## 10.6 Round-3 test log

```
# feature/dedup-tiers @ b74bbd31
$ pytest -q --fast-captions --force-cpu tests/test_dedup_tier_service.py \
    tests/test_dedup_tiers_api.py tests/test_dedup_verdict_service.py \
    tests/test_operation_log.py tests/test_architecture_guardrails.py
170 passed, 1 warning in 250.86s (0:04:10)      # was 150 at 029525a0; +20 new author tests

$ ruff check pixlstash
All checks passed!
```

| Probe | Covers | Result |
|---|---|---|
| `test_C1` | 4 verdict bodies × 13 forged batch ids, + positive | **M1 CLOSED** (52/52 400) |
| `test_C1b` | omitted id is server-minted `srv-` | closed |
| `test_C2a` | op_type not user-controllable (AST + repo grep) | **SAFE** |
| `test_C2b` | raising hook aborts atomically, 4 state dimensions | **SAFE** |
| `test_C2c` | shared `cli-` batch across keep-separate + stack | **R5 — DEFECT** |
| `test_C2d` | baseline: undo/redo reopens exactly its own verdict | correct |
| `test_C3` | 17 cursor fuzz cases + cursor/offset conflict | no 5xx; **R6** |
| `test_C3b` | global cursor replayed under a set scope | **SAFE** |
| `test_C4` | separator-only folder scopes, read + write route | **W2 CLOSED** |

Per-file, to rule out a hang: `test_dedup_verdict_service.py` 38 passed (49.83s),
`test_dedup_tiers_api.py` 32 passed (52.19s), `test_dedup_tier_service.py` +
`test_architecture_guardrails.py` 65 passed (36.00s), `test_operation_log.py`
35 passed (84.49s). A first combined attempt ran >25 min without finishing while
several probe servers of mine were still up; on a quiet machine the same command
finished in 4m10s. **Machine contention, not a test defect** — but it is the
load-sensitivity these suites are already known for, so a single slow CI run here
should not be read as a hang.

## 10.7 Standing items after round 3

**Required before #638 merges:** R5 (the one-line `VERDICT_STACKED` filter plus the
regression test).

**Still open from earlier rounds, unchanged by these five commits:**
W1 (the `MAX_PAIRS_PER_BUCKET` comment asserting a membership guarantee it does not
have — round 2 §9.2), the A1 §22.8 documentation of the share-token consequence
(round 1 §6), and G1 against `feature/gesture-batch-id` (its `re.match` + `$`
accepts a trailing LF; the dedup-side validator added here uses `fullmatch` and is
the implementation to keep when the two unify).

**Hardening:** R6, plus round-2's W2-adjacent items and round-1's R5/R6/R7.

**Independence.** Round 3 was performed by the round-1/2 reviewer, who wrote no code
on this branch. R5 must be certified by someone other than whoever implements it.
