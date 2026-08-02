# Review: `feat/dedup-stack-units-mixed-stacks`

Stack units in the duplicate queue, Mixed stacks, Keep cover only, and the
fixes that fell out of using them.

Three independent adversarial security passes and one performance analysis.
Each finding records what was run, not only what was read.

---

## 1. Security

Three passes, none by the engineer who wrote the code under review. CLAUDE.md
requires that the author of a security fix does not certify it, and that the
barrier runs before merge rather than after.

### Pass 1: do not merge

Two HIGH blockers, both reproduced by execution.

**F1: out-of-scope picture ids and vault filenames served to any token.**
`GET /pictures/import/status` and `GET /pictures/import/staging/{id}/status`
were declared `ANY_TOKEN`, whose contract is that the route returns no
per-object resource data. This branch made that false by adding
`results[].picture_id`, `results[].file` and `scrapheaped_picture_ids`. A READ
token scoped to picture 2 got 403 on picture 1's thumbnail and an empty
scrapheap listing, and this route handed back picture 1's id and filename.

The authz gate did its job: it enforces declarations. What no machine checks is
whether a declaration is *still true after the payload changes*. That is the gap
this finding exposes, and it is worth more than the fix.

Resolved: both routes are `OWNER_ONLY`. Caller audit first, including confirming
the guest-scoring suite never touches them.

**F2: locked-set bypass escalating to a lock escape.** `mixed_stack_service.py`
had no lock enforcement at all; a grep for "locked" across 1219 lines returned
nothing. A locked set freezes a stack's siblings *through* the stack, so:

```
DELETE /pictures/{sibling}   423
POST   .../{id}/unstack      200
DELETE /pictures/{sibling}   200
```

Two calls turned a hard refusal into a soft delete. The same branch implemented
the check correctly in `keep_cover_only_service.py` and stated the principle in
its design doc: a decomposition-seam miss, where locks were briefed for one
service and not its sibling.

Resolved as a class, not a patch: one shared
`set_lock_service.enforce_stack_detach_not_locked`, applied to both new routes
and to `DELETE /stacks/{stack_id}/members`, which had the same hole. The guard
counts soft-deleted members, because all three routes detach scrapheaped rows on
dissolve; a live-only check would call a stack safe to detach when its only
frozen member sits in the Scrapheap.

**F5: the branch was red on its own CI guardrail.** Three new backend suites
were gated nowhere, so the both-direction authz tests the coverage matrix cites
as evidence never ran. Found only because the reviewer ran the shard test
directly: every verification run before it had used keyword-selected pytest
invocations, and the guardrail's filename matched none of the keywords.

### Pass 2: blockers cleared, with a finding worth more than the fixes

Both chains reproduced and proven to fail afterwards, in the genuinely
vulnerable state. Every code path that writes `Picture.stack_id` was enumerated
(nine) and each checked; no unguarded detach path exists.

**The regression tests did not reach the state the vulnerability lived in.**
Adding a picture to a set reconciles stack membership, so a helper claiming "a
locked set whose only member is this picture" actually produced a set containing
every sibling. The tests passed, and would have kept passing if the guard were
later narrowed to a per-member check. Proven by mutation: filtering the guard to
live members only left the entire suite green while flipping `unstack` from 423
to 200 on a stack the real code refuses.

That is the recurrence shape this project has hit three times. Resolved by
building the state directly and asserting the set membership explicitly.

**H1, pre-existing, filed separately:** `GET /pictures/export/download/{task_id}`
is `ANY_TOKEN` and serves a zip. A token scoped to picture 1 downloaded a zip
containing picture 2's image bytes and its caption sidecar. It is on `main`, this
branch does not touch export, and a careless fix breaks the legitimate scoped
export flow. Notable because it rests on the same capability-URL argument the
matrix correction on this branch declares invalid.

### Pass 3: the F7 reversal

`POST /dedup/mixed-stacks/{id}/split` originally accepted an arbitrary
`picture_ids` list. Pass 1 constrained it to a subset of the stranded set
(finding F7, rated LOW).

**The owner reversed that deliberately**, with the reversal named to them in
advance, because the page was rebuilt so the user marks which members are
strangers: once the user can mark, their marks are the input and "stranded" is
only the opening position. The new bound is that every id must be a live member
of the stack named in the path.

Verdict: **safe to merge**, and better defended than the reversal note claimed.

The reviewer removed the membership bound entirely and found a foreign id still
could not be written: `_apply_removal` re-reads live membership and intersects,
so a cross-stack write is structurally impossible regardless of the bound. What
the bound actually buys is turning a silent partial application into an explicit
400. **That is the strongest safety argument available and it was the one not
being made.**

Also closed by execution: a poisoned cohesion-cache row naming another stack's
pictures did not widen the accepted set (the member list comes from a live
`load_stack_facts` read, not the cache); the dissolve branch is byte-identical to
`unstack` including the scrapheaped-member rule; the locked-set guard cannot be
routed around, since it takes only `stack_id`; and `DELETE /stacks/{id}/members`
is strictly wider than the widened split, so the justification is if anything
understated.

Two record-keeping defects, both about what the record says rather than what the
code does:

* The reversal note says the old constraint "only ever cost the user a round trip
  through a second endpoint". Untrue: the stranded-subset bound also doubled as a
  **stale-row detector**, because `stranded` moves when the data moves while live
  membership often does not. The client now always sends explicit ids, so that
  net is gone in the shipped app. Two tabs, one splits, the other presses Split
  with stale marks: it succeeds. Owner-only, one `Ctrl+Z`, no cross-stack write.
* The F7 citation was dangling: three places quoted a review document that did
  not exist in the tree. This file is that document.

---

## 2. Performance: why splitting a stack is slow and then fails

Reported from real use: "Stack splitting takes ages and then almost always
returns with 'Could not change that stack. Nothing was changed, so you can try
again.'"

Three faults compounding, in ascending order of how much they cost.

**The error message is wrong.** `onResolveMixed` treats any failure as a refusal
and special-cases only 400, so a lock timeout, a 500 or a dropped connection all
land in `reportMixedStackRefusal`, which is the *locked-set* reporter. A genuine
lock refusal is instant; the user was reading lock wording for what is almost
certainly a 30 second SQLite `busy_timeout` expiring.

**The split computes cohesion it does not use.** `split_stranded_in_session`
calls `cohesion_for_stacks` before the `if picture_ids is None` branch, and only
that branch consumes the result. The client always sends explicit ids, so the
default path is dead in the shipped app and every split pays for an O(n^2)
comparison that is discarded, while holding the write transaction.

**The list rescans the whole library on every threshold change.** This is the
owner's own diagnosis and it is correct:

```python
stack_ids = live_stack_ids_in_session(session)          # every live stack
reports   = cohesion_for_stacks(session, stack_ids, threshold)
rows.sort(...)
page = rows[offset : offset + limit]                    # then discard most of it
```

Paging bounds the response, not the work. Measured earlier in this branch's
review history: `limit=1` and `limit=200` cost identically, and three consecutive
identical requests each re-paid in full because the read path never writes the
cache.

**The cache contradicts its own design note.** `StackCohesion`'s docstring says
the row is threshold-independent and that the list folds components out of it
"at whatever threshold it was asked for, without touching `picture`". But
`_edges_for_stacks` re-reads every stacked picture's perceptual hash on every
call, because the fingerprint is the staleness test and computing it needs the
hashes. So a cache hit costs the same reads as a miss; what the cache saves is
only the comparison.

Compounding it, the writer that populates the cache is a background task gated
behind `IMAGE_EMBEDDING`, so on a library with an import backlog the cache is
never filled at all.

That rescan competes with the split for the same database, which is why the
split waits and then times out. **Fixing the rescan may remove the need for the
priority change**, which is the owner's hypothesis and is well founded: the
splitting call takes the default `run_task` priority while the cohesion scan runs
at `LOW`, but `IMMEDIATE` only wins queue *ordering*, not preemption. The worker
runs the executing task's session to completion first, so priority alone cannot
fix a long commit sitting in front of a user action. That is issue #651's root
cause and is larger than this branch.

Three separable pieces:

1. Score only what the page needs. Ranking wants a figure per stack, so this is
   not free, but it is the difference between scoring 209 stacks and scoring 20.
2. Make a cache hit cheap. Verifying the fingerprint currently costs the same
   reads as recomputing.
3. A threshold change should fold, not rescan: the edges do not move when the cut
   moves, only the components do.

---

## 3. Correctness found by using it

Every item here came from the owner in a browser, not from the test suites.
Recorded because that ratio is the useful signal.

* **Deep-linking the lightbox served `index.html` as an image.** An overlay
  opened on an id outside the loaded grid built a URL with no extension, which
  matched no route and fell through to the SPA catch-all. The error latched and
  never cleared, so the picture stayed broken next to correctly loaded tags.
* **Deleting a picture left an empty tile in the duplicate queue**, and the
  queue's own payload served a stale `member_count`, so a deck drew one deeper
  than it was. The grid's `unresolved` filter also kept marking a survivor whose
  partner had been scrapheaped.
* **A stack with one live member still answered the `stacked` filter**, while
  `stack_count` counted live members and the badge hid below two: the filter and
  the grid disagreed about the same picture. Keep cover only produces that state
  every time, so a latent inconsistency became a guaranteed one.
* **Keep cover only did not refresh the stack badge.** The action never
  refetched; the covers were announced only when the metadata union happened to
  change something, when the thing that always changes is stack membership; and
  the count is derived and listing-only, so the per-card reconcile could never
  have repaired it. Undo and redo were broken the same way and could not be fixed
  through the operation log's existing `updated` slice, because a no-op union
  leaves the cover with an empty diff.
* **The stack ribbon vanished on hover.** The image took `z-index: 20` on hover
  over a ribbon at 10, and through `.stack-hover-active` that hid the ribbon
  across an entire expanded group at once.
* **Members 89% similar to a sibling were reported as matching nothing**, with
  the similarity rendered as an en dash. Measured on the owner's library: of 65
  members flagged as matching nothing at threshold 0.90, 61 have a sibling at 50%
  or better and 12 are 85 to 90% similar, which is 7 to 9 bits out of 64 where
  the cut is 6. The figure exists and is discarded: `strongest_edge` is defined
  as the best similarity *surviving at the row's threshold*, so it is null by
  construction exactly for the members that need it.

---

## 4. Open

* The stranded cut is the same one used for finding duplicates, where strictness
  means few false proposals. Here it means false accusations against the user's
  own curation, which is the opposite cost. Moving it from 90% to 70% would take
  flagged members from 65 to 16 on the owner's library, and every one of the 49
  removed has a sibling 70 to 90% similar. A decision, not a bug.
* `GET /pictures/export/download/{task_id}` (H1 above), on `main`.
* `DELETE /stacks/{stack_id}/members` still disagrees with its sibling on the
  dissolve rule: it counts soft-deleted rows, so it can leave a stack with
  exactly one live member. Pre-existing; the comment claiming the routes now
  agree is true only of the undo snapshot.
* `split_picture_ids` under-reports on a dissolve: the scrapheaped rows are
  detached but not listed. Undo and the websocket fan-out are correct; only the
  receipt is short.
* None of the frontend has been exercised by an automated browser test. Every
  correctness item in section 3 was found by hand.
