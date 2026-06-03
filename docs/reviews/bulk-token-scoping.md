# Review: read-share-token scoping (branch `bulk-token-scoping`)

**Scope:** commit `053d9bdb` "Scope read-share tokens on the remaining GET reads"
(plus the prerequisite `b337aa20` "Add scoping for bulk read tokens" already on the
branch). Target: emergency patch **v1.5.1** (1.5.0 is released; tag `e2cadf14`).

**Threat principal:** a resource-scoped **READ share token** (`scope=READ`,
`resource_type ∈ {picture, picture_set, character, project}`). It passes auth, may
issue GETs, and the middleware does **not** object-authorize — every read handler
must enforce scope itself.

**Reviewers:** senior backend (correctness/regression), QA (coverage), CSO
(adversarial completeness). All three returned **DO-NOT-SHIP as-is.** Each HIGH
finding below was reproduced live against a running server with a Set-A-scoped token.

---

## Verdict

The four targeted holes are genuinely closed. But the patch leaves **three
same-class BOLA holes open that leak the same data** (whole-library picture
metadata / thumbnails). A share-link holder still walks the whole vault through the
back door. **Not shippable as the v1.5.1 security fix until the ship-blockers below
are closed.** They are all the same one-line guard pattern, in modules that already
import the helper.

---

## Closed and confirmed (by reading patched handlers + green regression tests + live PoC)

- `GET /pictures`, `/pictures/stream`, `/pictures/count` — `picture`-scope now pins the id filter (`_listing.py`). Not bypassable via id-string coercion, set/character/project nesting, or `shared_only`.
- `GET /pictures/{id}/tags`, `/pictures/{id}/tag_predictions` — `enforce_picture_scope` applied; its missing `picture` branch added.
- `GET /projects/{id}/attachments`, `/attachments/{aid}`, `/export`, `/summary` — `_require_scope_allows_project` applied; rejects wrong-project and wrong-resource-type tokens. `export_project`'s `include_pictures` path runs only after the guard.
- `get_characters_summary`, `get_picture_set_by_name`, `get_character_by_project_and_name` — scope-checked.
- No new fail-open path introduced; owner/ALL access untouched; the listing intersection is fail-closed.

---

## SHIP-BLOCKERS (must fix in the v1.5.1 patch)

### B1 — `character_id=UNASSIGNED` bypasses picture/set scope (HIGH, reproduced)
`pixlstash/routes/pictures/_listing.py` (new `scope_picture_id` block + the
`find_unassigned` call sites). The UNASSIGNED branch reads
`picture_ids = [...] if query_params.get("id") else None`. An **empty**
post-intersection id list is falsy, so it collapses to `None` = "no id filter" and
returns every unassigned picture. Set-scope is likewise never injected into this
branch.
- PoC: picture-scoped token, `GET /pictures?character_id=UNASSIGNED&id=<other>` → leaked ids outside the grant; `/pictures/count` reported the full unassigned count. picture_set-scoped token leaks with no `id` trick at all.
- Fix: treat an empty intersected id list as no-match (not no-filter) — let it flow to `id.in_([])` like the generic `Picture.find` path already does — and inject `scope_set_id`/`scope_character_id` into the UNASSIGNED branch.

### B2 — `GET /pictures/{id}/{field}` is unscoped (HIGH, reproduced)
`pixlstash/routes/pictures/_crud.py:985` (`get_picture_field`). No `request`, no
scope check. Sits directly between `get_picture` (495) and `get_picture_metadata`
(674), both of which DO check scope. Returns thumbnail bytes, any large-binary
field (base64), or any scalar field for any picture id.
- PoC: Set-A token, `GET /pictures/2/file_path` → 200; `.../width`, `.../original_file_name`, `.../thumbnail` all 200 for an out-of-scope picture. Enumerate ids to dump the library.
- Fix: add `request: Request`; `enforce_picture_scope(server, request, pic_id)` after the id parse.

### B3 — stacks read endpoints are unscoped (HIGH, reproduced)
`pixlstash/routes/stacks.py:223` (`get_stack_pictures`), `:196` (`get_stack`),
`:297` (`get_stack_for_picture`). Only `require_user_id`; not in
`READ_BLOCKED_GET_PATHS`. `get_stack_pictures` is a clone of the `/pictures` grid
leak the patch just closed.
- PoC: Set-A token, `GET /stacks/1/pictures?fields=metadata` → full metadata for out-of-scope pictures; `GET /pictures/2/stack` → 200.
- Fix: filter returned pictures through `fetch_scope_allowed_picture_ids(server, request)` (None = owner); 404 when the resolved set is empty for a scoped token.

---

## STRONGLY RECOMMENDED before tagging (same one-line pattern, cheap; a reviewer will ask why these were skipped)

### R1 — `GET /comfyui/pictures/{picture_id}/workflow` unscoped (MEDIUM)
`pixlstash/routes/comfyui.py:1628`. Leaks the embedded ComfyUI workflow (prompts,
seeds, model/LoRA names) for any picture. Fix: `request` + `enforce_picture_scope`.

### R2 — `GET /pictures/{id}/character_likeness` unscoped (LOW)
`pixlstash/routes/pictures/_crud.py:866`. Leaks existence + a likeness score of an
out-of-scope picture. Fix: `request` + `enforce_picture_scope`.

---

## Test gaps to add WITH the blocker fixes (QA)

- **Cross-PROJECT attachment download** (the actual high finding from the original audit) is effectively **untested** — the shipped test only exercises a `picture_set` token on `/projects/1/attachments` (wrong-resource-type branch, no project exists). Add a fixture with two real projects + a real uploaded attachment, a project-A READ token with `include_attachments=true`, and assert 403 on project B's `list_attachments` **and** `download_attachment`, plus a positive (own project still 200).
- **`/pictures/stream`** has separate copy-pasted scope wiring and is never called by a test. Add a picture-scoped stream test.
- **`export_project`** (`/projects/{id}/export`) is never called. Add negative (set/char token → 403) + positive (own project → 200).
- **B1 regression**: picture- and set-scoped token vs `/pictures?character_id=UNASSIGNED[&id=<other>]` and `/pictures/count` → must be `<=` grant.
- Per-blocker regression for B2/B3 modeled on `test_scoped_token_cannot_read_other_picture_tags`.

---

## DEFER (track as follow-up; not blocking v1.5.1)

- **ALL+resource_type token footgun.** `create_token` permits `scope=ALL` with a `resource_type`; such a token has `token_scope=None` and bypasses every BOLA guard. NOT reachable by the share-token threat principal (the UI only mints `scope=READ` for resource-scoped tokens), so out of scope for this patch. Fix later: reject `ALL`+`resource_type` at `create_token`, or have the guards also consult `request.state.matched_token` (as `require_unscoped_owner` already does).
- **Stack-sibling visibility / membership union — WONTFIX (accepted, working as intended).** `reconcile_stack_membership` unions project & set membership across stack members (documented "stack = unit" invariant), so stacking a picture into a stack that contains a set member makes it a real member of that set, and a set-scoped share token then sees it. Not exploitable: stacking is a write, READ share tokens cannot stack, so only the owner can cause the union, on their own data. No attacker-controlled escalation. The only residual is owner expectation (stacking into a shared set widens that share) — a UI/docs nicety, not a security issue. Character/face assignment is intentionally not unioned.
- **Guard duplication.** The `getattr(request.state, "token_scope", None)` + resource-type ladder is inlined across five files. Consolidate into one helper.
- **Name-lookup tests + project-scoped positive summary tests** (need a project fixture).
- **`/tag_predictions` positive** (own picture still readable) — one missing assertion.
- **Docs:** `docs/release-test-plan.md` has no manual share-link scope-confinement step, and `docs/regular-tests.md` (Playwright) has no API-scope entry. Add a manual smoke step for v1.5.1: mint a scoped READ token, open the share link in an incognito window, confirm only the shared resource is reachable (and that it still loads fully — over-blocking is its own regression).

---

## Release mechanics (correction to the assumed path)

- v1.5.0 **is** released (`pyproject` = 1.5.0, tag `e2cadf14`), so security fixes ship as **v1.5.1**. Correct instinct.
- **There is no `release-v1.5` maintenance branch yet** — only `release-cycle/v1.5.0*-prep` prep branches. The maintenance pattern is `release-v1.4`, `release-v1.4.1`.
- **Do NOT merge `bulk-token-scoping` wholesale into the release.** `git log v1.5.0..bulk-token-scoping` shows ~20 commits since v1.5.0, most of them unreleased feature work (Playwright, T2I wording, version-JSON, etc.), not just the security fixes.
- Recommended path: branch `release-v1.5` from the `v1.5.0` tag, **cherry-pick only the security commits** (`b337aa20` bulk + `053d9bdb` GET + the blocker fixes above), bump to 1.5.1, add a `[1.5.1] [Security:High]` CHANGELOG entry, tag. This keeps the emergency patch to security-only changes.
