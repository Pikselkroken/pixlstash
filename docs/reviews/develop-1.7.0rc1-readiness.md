# Release-Readiness Review — `1.7.0rc1` from `develop`

- **Subject:** `origin/develop` @ `f30b2d4c` (version `1.7.0-dev.6`) — HEAD is the merge of PR #528 `tag-review-rewrite`.
- **Date:** 2026-07-18
- **Method:** router-orchestrated, five independent read-only assessment lanes (CI, QA, security, backend code, frontend code) synthesized by a release decision-owner. No code changed.

## Verdict: NO-GO to cut `v1.7.0rc1` at `f30b2d4c` — short-fuse (~1 day critical path)

The code is release-quality. What is not yet trustworthy is the **release evidence** and **one flagship path**. Three low-effort fixes convert this to a GO; cutting now and patching in rc2 buys no schedule and burns credibility.

**Why the 4 GO / 1 NO-GO split is not a contradiction:** each GO lane assessed only its own surface and **none of them ran the failing tests**. QA is the only lane that executed the full backend suite. A GO from a lane that never ran `test_label_ledger` is not counter-evidence to that test failing. QA's NO-GO dominates on the merits.

## Lane verdicts

| Lane | Verdict | Headline |
|---|---|---|
| CI / build (ci-expert) | GO (no CI blockers) | Tip fully green: `build`, `build-windows`, `e2e` (real Playwright). rc-suffix path proven & prerelease-aware. **But**: no dep-audit/SAST job; `release-test-issues` glob `v*-rc1` won't match `v1.7.0rc1`. |
| QA (qa-tester) | **NO-GO** | Backend **2 failed / 862 passed**; frontend 240/240 + build OK; e2e 48 pass. Both failures are on the headline feature and invisible to CI. |
| Security (chief-security-officer) | GO, conditional | Strong posture (0 npm vulns, no secrets, safe CORS, loopback-pinned owner, mature authz). One recommended blocker: bump **pillow → ≥12.3.0**. |
| Backend code (senior-backend-developer) | GO | Migrations linear/guarded (0057→0072); authz model sound; concurrency/error-handling OK. Risk is *maturity* (churny tag-review services), not defectiveness. |
| Frontend code (senior-frontend-developer) | GO | Build clean, 240 vitest, e2e green; `useTasksStore` poller well-engineered. CI gates neither build nor lint; reviews-UI token drift. |

## The two QA blockers (decisive)

- **B1 — deterministic regression on the flagship path.** `tests/test_label_ledger.py::test_accept_remove_suggestion_records_neg` fails 100% (independently reproduced). The F2 accept-guard added in PR #528 (`bf67a702`) raises `SuggestionConflictError` when accepting a *remove*-suggestion on a picture that carries a human POS label — while the sibling *manual* remove path flips POS→NEG cleanly. Identical user intent, divergent outcome by code path. We do not currently know whether this is a real regression or an intentional guard with a stale test — that unknown is itself the problem.
- **B2 — CI-green is not release evidence.** CI runs **28 of 84 backend test files (~33%)**. The entire 1.7.0 headline surface (`test_tag_health_api`, `test_reviews_api`, `test_tag_predictions_api`, `test_tag_suggestions_api`, `test_tagger_runs_api`) and nearly all authz/BOLA scope tests are ungated. Both failures merged red *because* they live in that blind spot.

## Blocker list

### MUST-FIX before rc1

| # | Item | Why it blocks | Owner skill | Effort |
|---|---|---|---|---|
| M1 | Reconcile B1 — decide correct behavior for accept-remove-on-POS-label; align code + test so the accept path and manual path agree | Flagship throws on a normal action; correctness unknown | senior-backend-developer + qa-tester | reconcile ~1h; fix S–M |
| M2 | Make CI a real gate — add the 5 headline API files, the authz/scope files, and the 2 failing files to the CI backend run | Both failures merged red in CI's blind spot; RC soak needs a gate over the surface under test | ci-expert (+ chief-security-officer for the dep-audit part) | config, hours |
| M3 | Bump `pillow 12.2.0 → ≥12.3.0` (requirements.txt + pyproject) | Heap OOB write in `crop()`/`paste()`/`alpha_composite()` — untrusted-image parse path an RC exercises | chief-security-officer → junior-backend-developer | 1 line |

### FIX before final 1.7.0

| # | Item | Owner skill | Notes |
|---|---|---|---|
| F1 | torch `2.12.1 → ≥2.13.0` | senior-backend-developer | dependabot #64; PR #529 already open |
| F2 | Authz coverage-matrix debt — 7 `tag_suggestions` mutators + `tagger_runs` lack `enforce_picture_scope` | senior-backend-developer + chief-security-officer sign-off | LOW/latent, **not live-exploitable** (scoped tokens are read-only, blocked from these POSTs). Record as a filled coverage-matrix cell |
| F3 | Full CI hardening — run all 84 backend files; add `npm run build` + lint gate; add dep-audit/SAST; fix `release-test-issues` glob | ci-expert + chief-security-officer | M2 is the rc1 subset; this is the complete fix |
| F4 | `est_wrong` flake in `test_tag_health_aggregates_on_fixture_vault` (~15–25%; `_seed_stable` gated likeness but not the tagger pipeline) | senior-backend-developer / machine-learning-expert | Stabilize before final |
| F5 | Silent `except (ValueError, TypeError): pass` at `review_service.py:571` | senior-backend-developer | Log it (no-silent-pass rule) |
| F6 | Design-token drift in reviews UI — 14 raw `rgba(0,0,0,…)` + hardcoded hex | lead-designer | Token pass |
| F7 | Reconcile `docs/release-test-plan.md` §20 (stale — marks resolved BUG-RS-1 as a blocker; §20.9 demands 6 `review-session.spec.js` green, 4 `fixme`'d) | qa-tester | The gate doc itself is wrong |

### TRACK / defer
- Security hardening: CSP/X-Frame/HSTS headers; drop unused `python-jose`/`ecdsa`; setuptools `≥83.0.0` (build-time only).
- Frontend: code-splitting (1.39 MB bundle), 133 eslint warnings, 1 eslint error in an e2e fixture.
- CI minor: `concurrency:` cancel-in-progress; deprecated action majors.
- Migrations 0068–0071 add-then-drop churn (informational).

**Cross-lane fold:** "CI is not a real gate" unifies B2 (33% backend coverage) + no build/lint gate (frontend) + no dep-audit/SAST (CI). One workstream: M2 is the rc1-blocking subset, F3 the remainder. The authz-matrix item appears in three lanes as one issue (F2), latent not live.

## Sequencing — shortest path to a trustworthy rc1 (~1 day)

**Day 0, three parallel lanes:**
1. ci-expert widens CI to the headline + authz + 2 failing files (M2), dep-audit portion cleared with chief-security-officer.
2. chief-security-officer → junior-backend-developer bumps pillow (M3) and merges torch PR #529 (F1).
3. senior-backend-developer + qa-tester reconcile B1 (M1): if the guard is intentional → fix the stale test and confirm accept-vs-manual divergence is a deliberate, documented decision; if a regression → make accept-remove flip POS→NEG to match the manual path.

**Then:** re-run the now-gated full suite → require green (F4 flake stabilized or quarantined with a tracked issue) → re-cut `v1.7.0rc1` at the new SHA (run the node-version sync script; the strict PEP 440 tag guard accepts `1.7.0rc1`).

**During rc soak:** qa-tester runs a manual regression pass on scan / suggestion / health flows (the churn concern is integration maturity).

**Kill criterion:** if B1 reconciliation shows the accept-guard is load-bearing for data integrity and cannot be aligned to the manual path without deeper rework, hold rc1 and escalate the tag-review service design to principal-software-engineer before any cut.

## Note

M1 and F4 are fallout from the just-merged PR #528: the F2 accept-guard over-fires, and the `_seed_stable` flake fix covered only the likeness pipeline, not the tagger one. Both slipped through precisely because those files are not in CI (M2) — the review's central lesson.

---

## Update — `main` → `develop` merge (2026-07-18)

`main` (v1.6.12) merged into `develop` at `0a1b60a1`: **M3 (pillow 12.3.0)** and **F1 (torch 2.13.0)** security bumps are now on develop; `main..develop` = 0. No source code changed; version preserved at `1.7.0-dev.6`. Two of the fix items are closed.

## Principal review addendum (2026-07-18) — B1 decision + plan refinements

**B1 is an over-fire, NOT load-bearing. Narrow the guard; the CEO kill-criterion is NOT triggered.** The F2 accept-guard (`tag_suggestion_service.py` `accept_suggestion`) claims (per its docstring) to refuse a human label "recorded *since the suggestion was raised*", but the code never checks time — it fires on *any* opposite human label. The manual-remove path flips human POS→NEG with no guard at all, and that is documented-correct; an explicit accept-remove is equally safe. No data-integrity invariant needs redesign.

**M4 (the M1 fix; ~1h, one predicate, no schema/migration):** add the missing temporal term to guard 2 — refuse only when `human.labeled_at > suggestion.created_at` (both timestamps already exist). Keep guard 1 (deleted picture) and the `status == PENDING and prior_status is None` scoping byte-for-byte unchanged. Missing-timestamp edge: allow, but log (no silent pass). This is the discriminator the existing tests already encode (B1: POS predates suggestion → allow→NEG; F2 refute-test: POS postdates → refuse). Keep the guard — `bulk_accept` still needs it for programmatically-stale rows.

**Plan corrections:**
1. **M2 — widen the lens, not just the gate:** keep the "headline + authz + 2-failing" subset as the *blocking* rc1 gate, and *also* add a full-84 informational (continue-on-error) run so the other ~50 files' status is visible before the cut. Fix the `v*-rc1` release-issues glob in M2 (not F3) if it gates rc1 automation. Full hardening (build/lint/SAST) stays F3.
2. **New-red triage checkpoint (blocking):** any red the full-84 run surfaces is triaged/dispositioned (fix / quarantine-with-issue / accept) *before* the cut — never auto-deferred.
3. **Two pre-cut checkboxes:** F2 authz-latent stays out of MUST-FIX *only* with a written CSO confirmation that scoped tokens cannot reach the 8 unscoped mutators (recorded as a filled coverage-matrix cell). F4's `est_wrong` flake must be **quarantined with a tracked issue before rc1** (blocking) even though its root-cause fix is before-final — a flake inside the gating suite is unacceptable at cut time.

**Go-condition — re-cut only when ALL hold:** (1) guard change adds only the temporal term, guards 1 + scoping unchanged, missing-timestamp logs; (2) regression trio in the PR — `test_accept_remove_suggestion_records_neg` GREEN and both refute-tests (`...refuses_when_a_manual_fix_contradicts...`, `...soft_deleted_suspect...unacceptable`) still FIRE, shown not asserted; (3) CI shows the blocking subset AND the informational full-84 run, every red beyond quarantined-F4 dispositioned; (4) F4 quarantine carries a tracked issue; (5) CSO F2 sign-off as a filled coverage-matrix cell; (6) cut SHA carries pillow ≥12.3.0 + torch ≥2.13.0, no schema/migration in the guard PR; (7) all met → GO, absent any one → hold (do not cut-and-patch in rc2).

**Updated blocker status:** M3 ✅ (merge) · F1 ✅ (merge) · M1 → **M4** (concrete fix, no escalation) · M2 (refined) · **F4 pulled to pre-cut-blocking** (quarantine) · F2 gated on CSO sign-off.
