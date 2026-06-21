# Security sign-off — grid-refresh-cleanup

**Reviewer:** Chief Security Officer
**Date:** 2026-06-21
**Branch:** design-system (working tree, nothing committed)
**Scope of review:** the two items raised in the grid-refresh-cleanup plan §8/§10:
1. the new e2e test-hooks endpoint `POST /api/v1/test-hooks/ws-event`
2. a pre-existing BOLA-class gap in the `tag_predictions.py` mutating handlers

This review followed CLAUDE.md's security process: coverage discipline, deny-by-default,
and the endpoint scope-enforcement HARD REQUIREMENT. I read the actual code, not the plan's
self-description of it.

---

## 1. Test-hooks endpoint — `POST /api/v1/test-hooks/ws-event`

### What it is
A router (`pixlstash/routes/test_hooks.py`) exposing one POST that calls `vault.notify`
with an attacker-shapeable payload (event type from a 6-entry allowlist, `picture_ids`,
`source`, `origin_client_id`, `change_kind`, `fields`, `repeat` 1–500). It exists so
Playwright can deterministically inject WS grid events that the disabled background workers
would otherwise never fire.

### Verification (each claim checked against code, not the docstring)

**Off by default / off in production — CONFIRMED.**
- `server.py:2535` registers the router only inside `if self._server_config.get("enable_test_hooks", False):`. Default is `False`, so when the flag is absent the route is not mounted and returns 404 (not 403). Good: absent route, not a gated-but-present one.
- Grep of the whole tree: the only place that sets `enable_test_hooks: True` is `frontend/e2e/serve_e2e_backend.py:109`, the throwaway hermetic e2e launcher (background workers disabled, CPU only, credentials stripped, temp work dir). No production config sets it. The launcher's own comment (lines 104-108) explicitly says production MUST never set it.
- `server.py:2536` logs a `warning` whenever the flag flips on. Reasonable tripwire if it ever leaks into a real config.

**Auth: owner-only, scoped/READ tokens cannot reach it — CONFIRMED, belt-and-suspenders.**
- Handler calls `server.auth.require_unscoped_owner(request)` as the first statement (`test_hooks.py:171`), before any work. `require_unscoped_owner` (`auth.py:687-706`) requires a logged-in user AND rejects (403) any request whose `token_scope` is set OR whose matched token carries a `resource_type`. So a cookie owner session or an unrestricted ALL-scope bearer passes; READ tokens and resource-scoped tokens are denied.
- Independently, the auth middleware (`auth.py:1584-1592`) already blocks every non-GET method for READ-scoped tokens unless the path is in `READ_SAFE_POST_PATHS` (this path is not). So a READ/scoped token is rejected at the middleware before the handler even runs. Two independent gates. Correct.

**Scope-enforcement decision (CLAUDE.md HARD REQUIREMENT) — CORRECT state-(b) exemption.**
- The endpoint emits broadcasts and returns no per-object resource data (only `{status, event_type, emitted}`). It does not read or return any picture/character/set row. There is nothing to scope-leak via the response, so `enforce_picture_scope` is not the right tool here.
- It still qualifies as a deliberate, owner-only, documented exemption: the docstring (`test_hooks.py:10-27`) records the posture, the handler enforces owner-only, and this review is the named sign-off. That satisfies CLAUDE.md's "exactly one of (a) or (b), recorded decision." It is not unscoped-by-omission.

### Abuse surface IF it were ever enabled in prod (threat model)
The honest worst case, assuming the flag somehow shipped on and an authenticated owner (or
anyone who has stolen an owner cookie / ALL-token) hits it:
- **Self-inflicted WS noise / mild DoS.** Each call fans `repeat` (≤500) `vault.notify`
  calls out to all connected WS clients. An owner could loop the endpoint to spam every
  open tab with bogus grid events (phantom "new pictures" pills, forced grid refetches).
  Blast radius is limited to that owner's own clients and the owner's own server CPU/socket
  budget — there is no cross-tenant surface here (single-owner app). It is annoying, not a
  data breach.
- **No data exfiltration.** The handler never reads resource data and the response carries
  no picture content. `picture_ids` are echoed back to clients inside the envelope, but the
  caller supplied them, so there is nothing the caller learns that it did not already send.
  No info leak in the response.
- **No spoofing of trust outside the WS layer.** `origin_client_id` / `source` only
  influence the frontend's pill-vs-reconcile decision. Worst a forged origin does is make a
  client suppress or show a pill incorrectly. It is not an auth token and grants nothing.
- **Event-type forgery is bounded.** Only 6 grid event types are accepted (`test_hooks.py:45-52`); snapshot/restore/progress traffic cannot be fabricated through this hook. Unknown types return 422.

**Is `repeat ≤ 500` an adequate cap?** For its stated purpose (the largest realistic flood
is a per-id `CHANGED_TAGS` burst) it is fine, and the cap is enforced by Pydantic
(`ge=1, le=_MAX_REPEAT`), not just documented. There is no per-request or per-minute rate
limit on the endpoint itself, so an owner could call it in a tight loop and multiply 500×N.
That only matters if the endpoint were exposed in prod, which is precisely what the
registration guard prevents. In the e2e backend the global rate limiter is deliberately
disabled anyway. I am not requiring a rate limit on a route that does not exist in prod.

### Residual risk
The single point of failure is the config flag. If a future deploy ever copies an e2e
config, or sets `enable_test_hooks` by accident, the endpoint goes live. Mitigations already
present: default-off, single known setter, startup warning log, `include_in_schema=False`
(not advertised in the API docs). That is a defensible posture. The one cheap hardening I
*recommend but do not block on* is asserting the flag is off when a production-ish marker is
set (e.g. refuse to register, or refuse to boot, if `enable_test_hooks` is true while
`require_ssl`/non-loopback host indicates a real deployment). Track as hardening, owner: the
backend team, revisit if test-hooks grows beyond this one route.

### Verdict: **APPROVE**
Off by default, off in prod, owner-only with two independent gates, no resource data
returned, bounded inputs, correct state-(b) scope exemption with sign-off recorded here.
The abuse surface is real only in the counterfactual where the flag is wrongly enabled, and
the blast radius there is self-inflicted WS noise on a single owner's own clients, not data
or credentials. Recommended (non-blocking) hardening: a boot-time guard that refuses to
register test-hooks under a production marker.

---

## 2. Pre-existing BOLA-class gap in `tag_predictions.py` (assess, do NOT fix)

### The handlers in question
All under `/pictures/{id}/...`, all mutating, all returning only `{"status": ...}` (no
resource data). None call `enforce_picture_scope` (line numbers from the current working
tree, which differ slightly from the plan's):
- `confirm_tag_prediction` — `POST /pictures/{id}/tag_predictions/{tag}/confirm` (`:162`)
- `reject_tag_prediction` — `POST /pictures/{id}/tag_predictions/{tag}/reject` (`:185`)
- `delete_tag_predictions` — `POST /pictures/{id}/tag_predictions/delete` (`:213`)
- `reset_picture_tags` — `POST /pictures/{id}/reset_tags` (`:244`)
- `reset_picture_description` — `POST /pictures/{id}/reset_description` (`:278`)

Note: the plan listed four; there are actually **five** unscoped mutators in this file —
`confirm_tag_prediction` was missed in the plan's §10 list. By contrast, the read handler
`get_tag_predictions` (`:110`) *does* call `enforce_picture_scope` (`:123`), which is the
correct reference pattern and shows the omission on the mutators is inconsistency, not
policy.

### Is it a real, exploitable BOLA? — NO, not currently exploitable. It is a defense-in-depth gap.
I traced the auth path that can reach these handlers:
- A **resource-scoped / share token is always READ scope.** Minting enforces this:
  `auth.py:915-922` rejects any ALL-scope token that carries a `resource_type`, and a
  resource-scoped token must be READ. So "scoped token" == "READ token" in this codebase.
- The auth **middleware blocks all writes for READ tokens.** `auth.py:1584-1592`: if
  `token_scope.scope == "READ"` and the method is not GET/HEAD/OPTIONS and the path is not
  in `READ_SAFE_POST_PATHS`, it returns **403 "Token is read-only"** before the route runs.
- All five tag_predictions mutators are **POST** and **none are in `READ_SAFE_POST_PATHS`**
  (`auth.py:79-96` — I checked the full list). So a share/scoped token hitting any of these
  is rejected at the middleware, never reaching the unscoped handler.

Therefore a scoped token **cannot** mutate a picture outside its scope via these routes
today. There is no whole-library write leak here. The exposure that *would* exist if a
write-capable scoped token type were ever introduced (or if one of these paths were
mistakenly added to `READ_SAFE_POST_PATHS`) is **mutation only** (reject/delete/reset of a
picture's tag predictions and description), not read. No resource data is returned, so even
in that hypothetical there is no read-leak, only unauthorized writes.

### Severity and exploitability
- **Current exploitability: none** (blocked one layer up by the READ-token write block).
- **Severity as a latent gap: medium.** It violates CLAUDE.md's HARD REQUIREMENT that every
  per-object mutating endpoint either call the scope chokepoint (state a) or be a recorded
  exemption (state b). These five are in neither state — they are unscoped-by-omission,
  which is exactly the recurring failure mode the policy exists to kill. The current safety
  depends entirely on the *middleware* invariant "scoped == READ == no writes." That is a
  single, implicit, easy-to-break guarantee. The day someone adds a write-capable scoped
  token, or relaxes the middleware, all five silently become live BOLAs. Defense in depth is
  missing precisely where the policy says it must not be.
- **Type: mutate, not read.** Both `confirm`/`reject`/`delete`/`reset_tags`/`reset_description`
  change picture state; none leak data in the response.

### In-scope-incidental, or its own finding?
**Its own finding.** It predates the grid-refresh work (the handlers had no scope check
before or after this branch's changes), and the grid-refresh branch only *touched* these
handlers to thread `origin_client_id` for the pill fix — it did not introduce or widen the
gap. Per CLAUDE.md this should not be silently folded into the grid-refresh PR. It needs its
own GitHub issue and its own coverage-matrix pass.

### Recommendation
1. **File a GitHub issue, tag `bug` + `security`.** Title it as a BOLA/defense-in-depth gap
   on the five `tag_predictions.py` mutators. Reference the reference pattern
   (`get_picture` / `get_picture_metadata` in `routes/pictures/_crud.py`, and
   `get_tag_predictions` in the same file) and `enforce_picture_scope` in
   `routes/pictures/_helpers.py`.
2. **Fix = add `enforce_picture_scope(server, request, pic_id)` immediately after parsing
   the id, before any DB read/mutation/return, in all five handlers** (copy the guarded
   siblings verbatim). `confirm_tag_prediction` additionally needs `request: Request` added
   to its signature. Cover all return paths with the single call. Add tests in both
   directions (out-of-scope blocked 403; in-scope still works), authored by someone other
   than the fix author.
3. **Not urgent / not a release-blocker on its own**, because it is not currently reachable
   by a scoped token. But it is a real policy violation and the safety is one refactor away
   from breaking, so it should be scheduled, not ignored. Steer the fix toward the
   centralised deny-by-default chokepoint direction (backend_architecture §16.2) rather than
   adding five more per-handler opt-ins, per CLAUDE.md's long-term direction note.

---

## Blocking summary

- **Release-block on the grid-refresh merge:** nothing. The test-hooks endpoint is approved.
- **Approved with recorded sign-off:** `POST /api/v1/test-hooks/ws-event` as a state-(b),
  owner-only, off-by-default, off-in-prod exemption. (Optional hardening: boot-time guard
  against the flag under a production marker.)
- **Separate finding to file (not blocking this PR):** the five unscoped `tag_predictions.py`
  mutators — medium-severity latent BOLA / defense-in-depth gap, not currently exploitable
  (READ-token write block stops it), file as its own `bug`+`security` issue and fix with
  `enforce_picture_scope` + two-direction tests.
