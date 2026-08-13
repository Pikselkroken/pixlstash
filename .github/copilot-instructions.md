# Copilot and Claude Instructions for PixlStash

## The repository layout: peer checkouts, no privileged one

Several agent sessions run against this repository at once, and they used to
collide: a session that started work on `develop` could find itself on someone
else's branch mid-task, holding someone else's uncommitted files, with its own
branch renamed out from under it. Nothing warned you, and the first symptom was
usually a commit that picked up the wrong files.

`~/Projects/pixlstash` is therefore **a container, not a checkout**:

```
~/Projects/pixlstash/
  .bare/         the git dir
  .git           one line: gitdir: ./.bare
  develop/       a worktree pinned to develop, never anything else
  main/          a worktree pinned to main
  worktrees/     one per session
```

**The layout is what enforces this, not the rule.** Git refuses to have one
branch checked out in two worktrees, so once `develop/` holds `develop` no
session can take it away, and no session can silently retarget it. That is a
mechanical guarantee where the previous "never edit the shared checkout"
instruction was a remembered one, and remembered ones are what failed.

`develop/` and `main/` are **test rigs**. They are kept current and are never
edited or branched off in place. All work happens in `worktrees/`.

### Starting work

`EnterWorktree` creates under `<repo>/.claude/worktrees/`, which is not
configurable, so a session started in `develop/` would put its worktree inside
the test rig. Create it as a peer instead and enter it by path:

```
git worktree add ~/Projects/pixlstash/worktrees/<name>
# then: EnterWorktree with path: ~/Projects/pixlstash/worktrees/<name>
git fetch origin && git checkout -B <branch> origin/<base>
ln -s ~/Projects/pixlstash/develop/frontend/node_modules frontend/node_modules
```

Set the base explicitly: the default is `origin/main` and feature work is
almost always based on `develop`. The `node_modules` symlink is what makes the
worktree actually runnable: without it `npm run dev` and `vitest` both fail on
a fresh worktree, which is most of why worktrees were awkward to test in.

**The hub and the vault live outside the repo** (platformdirs user data dir), so
every checkout runs against the same library you already have configured. That
is why any worktree is testable at all. Do not "fix" it into per-checkout data.

Commit and push from the worktree, open the PR, then remove the worktree when it
merges. Never stage a file you did not write in this session: stage by name, not
`git add -A`.

Sessions that only read (questions, reviewing pushed code) can stay put.

### Say where the work can be tested

A session that changes behaviour ends by stating the path and the commands,
because the person testing is not in your worktree and cannot guess it:

```
Test at: ~/Projects/pixlstash/worktrees/<name>   (branch <branch>, based on <base>)
  backend:  cd ~/Projects/pixlstash/worktrees/<name> && python -m pixlstash.app
  frontend: cd ~/Projects/pixlstash/worktrees/<name>/frontend && npm run dev
```

If the work is already merged, say `develop/` instead and say to pull. "It's on
the branch" is not a test path.

### Say what to test, not only where — and say it in the session, never in the PR

**A path is not a handoff.** The session that wrote the change is the only party
that knows which screen it lands on, what number should appear, and which of its
own steps it is least sure about. Ending at "test at `<path>`" leaves the person
testing to reverse-engineer all three from a diff, and the honest answer they
give back is *"I don't know what to test."*

**The handoff goes to the person, in the session. It does not go in the PR.**
A test plan written against the machine the work was done on is made of that
machine's absolute paths, its library, its folder names and its disk figures —
`/home/<user>/...`, the models they happen to own, how full their drive is. A PR
is published, permanent and public, and none of that belongs in one. This was
learned by doing it: a `## Test this` section went into a PR body carrying the
owner's home directory and an inventory of their private model collection, and
had to be edited back out. **Do not put a test plan, a path under `$HOME`, a
listing of the owner's library, or their disk usage into a PR body, a commit
message or a PR comment.** The PR describes the change; the session describes
how to try it.

Aggregate engineering figures that justify a decision are fine and already house
style — "0.01 s to read the index", "a 339 MB tagger". The line is between *this
mechanism costs this much* and *here is what is on that person's disk*.

**So end the session with the plan, and always name a folder to run it from.**
Five parts, and the last two are the ones that get skipped:

1. **Where** — the worktree block above, always, even when the answer is
   `develop/`. Plus any step that has to happen first: *"restart the backend,
   the declaration runs at start-up"* is the difference between a working
   feature and a bug report.
2. **What to look at**, as numbered checks against a named screen or control.
   "Model folders dialog, toolbar folder icon", not "the folders UI".
3. **The expected values, concretely** — the row count, the size, *"no size at
   all rather than `0 B`"*. Read off the machine and written down, because a
   tester cannot confirm a number nobody stated and a wrong one is invisible
   next to a vague one. This is exactly the part that must not be published.
4. **What "wrong" looks like**, per check. State the failure, not only the pass:
   *"wrong if a locked row offers scan or forget"*. That is what lets someone
   who does not know the design spot a regression rather than assume the screen
   is meant to look that way.
5. **What you could not test yourself**, named and separated. Cold-boot cost,
   whether a long list is pleasant, anything needing hardware or a judgement
   call. This is the part actually being handed off — the rest is verification —
   and folding it into the checks above buries the only items that genuinely
   require a human.

**Order the checks by risk, and say which one matters.** If a change opens a
path that could destroy data, that check goes near the top, says so, and tells
the tester to stop rather than complete the gesture: *"the drag must never
start; if the row picks up, say so and do not drop it."*

**Automated coverage is not a test-this item.** If a suite already proves it,
say so in a line and spend the human's attention on what the suite cannot see.

## Patch Reliability Policy

- **Read before you edit.** Read enough surrounding context (at least 50 lines before and after the target) to understand structure, logic, and dependencies before generating a patch. If placement is ambiguous, read more until it is certain.
- **Don't assess what you haven't read.** Never critique, judge, or make claims about the adequacy of a file, document, or module you have not actually read. Read it first, or explicitly scope your statement to what you did read and flag the gap.
- **Reject illogical edits.** Check every patch for abrupt changes that don't fit the surrounding code — e.g. a method placed outside its class, code inserted above the top imports, or a missing blank line between top-level definitions.
- **Class member order:** imports → class definition → Google-style docstring → class-level variables → `__init__` (including property initialisation) → properties (getters/setters) → public methods → private methods. Keep everything correctly indented within the class block.

## Project Architecture

Use the following architecture documents depending on the scope of the task.

**Read them by section, never end to end.** These are reference manuals, not
preambles: `backend_architecture.md` is ~82k tokens, `frontend_architecture.md`
~53k, `integration_architecture.md` ~24k. Reading all three costs ~160k tokens
before you have looked at a single line of code, and almost all of it will be
about subsystems you are not touching. The required move is:

1. Read the **Table of Contents** at the top of the relevant document.
2. Read **only the sections that cover the code you are about to change**, plus
   any section the ToC or a cross-reference explicitly points you to.
3. Widen only when what you read turns out to be insufficient.

`grep -n '^## ' docs/backend_architecture.md` gives you the section map and line
numbers; `sed -n 'START,ENDp'` reads one section. A whole-file read of any of
these three documents is a mistake unless you are deliberately auditing the
document itself.

Scope, once you are reading by section:

1. Frontend tasks:
   The relevant sections of `/docs/frontend_architecture.md`.

2. Backend tasks:
   The relevant sections of `/docs/backend_architecture.md`.

3. Full‑stack tasks (involving both frontend and backend):
   The relevant sections of both documents, plus the `/docs/integration_architecture.md`
   sections covering the contract you are changing (§2 API surface, §8 event
   contract, and whichever feature section applies).

4. Any task that adds or changes UI (a new feature, a component, a screen, a control):
   The **design manual in `/docs/design/` is mandatory, not advisory.** Read
   `/docs/design/visual-language.md` and build against the tokens in
   `/docs/design/design-tokens.css` and the color themes in `frontend/src/main.js`.
   New UI must use the existing tokens (spacing, radius, type ramp, elevation, motion,
   color) — never a hardcoded hex, off-grid spacing, ad-hoc radius, raw `rgba(0,0,0,…)`
   shadow, or `em`/`px` font-size outside the ramp. A genuinely new value is a design
   decision: route it to the `lead-designer` skill, do not inline a one-off. Anything
   that changes a flow, a state, or what a control does also goes past the `ui-ux-expert`.

   **And read the design system before you build the surface.** It is a
   published Claude Design project,
   <https://claude.ai/design/p/ac544c9e-b278-4439-be75-e442fca29d41>, readable
   through the `DesignSync` tool. `/docs/design/` tells you what the *values*
   are; the design system tells you what the *surface* is made of, and
   `ui_kits/app/` already holds real specs for the app's screens. Building a
   screen that exists there without reading it is how a surface gets invented
   twice and disagrees with itself. Full contract in
   `docs/frontend_architecture.md` §7.

Task classification rules:
- If the task involves UI, components, state management, routing, or client-side logic → treat it as a frontend task **and apply the design manual (item 4)**.
- If the task involves APIs, storage, indexing, ML pipelines, or server-side logic → treat it as a backend task.
- If the task touches both (e.g., API changes that require UI updates) → treat it as a full‑stack task.

When making changes to architecture or integration patterns, always update the relevant documentation to reflect the new approach. This ensures that all future work follows the updated architecture and maintains consistency across the codebase.


## Skill Delegation

This repo ships role-specific **skills** — personas with their own expertise (and, for the developer roles, their own subagents). Route work to the skill that owns the domain instead of doing everything in one generalist pass. This extends the frontend/backend/full‑stack classification above; check the available-skills list at the start of each session, because the set can grow.

### Who owns what

| Task | Skill |
|---|---|
| Backend code: Python, FastAPI, SQLModel/SQLAlchemy, Alembic, async/concurrency, data models, observability | `senior-backend-developer` |
| Routine backend work that copies an existing pattern (mirror a CRUD endpoint, add a field + migration, straightforward tests, obvious bugfix, type hints/docstrings) | `junior-backend-developer` |
| Frontend code: Vue 3, JS, HTML, CSS, state/data flow, routing, browser/CORS/CSP issues, rendering | `senior-frontend-developer` |
| Routine frontend work that mirrors an existing component (presentational component, props/emits, simple layout fix, basic route/computed, a11y attributes, copy) | `junior-frontend-developer` |
| Visual language: the design manual in `/docs/design/`, tokens, type/color/spacing/iconography, making UI look sleek and consistently PixlStash, auditing visual drift | `lead-designer` |
| Usability: flows, information hierarchy, discoverability, accessibility (WCAG), keyboard/power-user efficiency, anything that changes what a control does or how a screen behaves | `ui-ux-expert` |
| ML: training/fine-tuning, model eval, embeddings, captioning, quality scoring, architecture/dataset choices | `machine-learning-expert` |
| ComfyUI graphs, nodes, model selection, generation/upscale/inpaint pipelines | `comfyui-workflow-wizard` |
| CI/CD, GitHub Actions, pipeline speed/flakiness, release automation, the `pixlstash-metrics` collector | `ci-expert` |
| Security review of a diff/PR/codebase, secret hunting, dependency audit, API/deploy/demo hardening, threat modeling | `chief-security-officer` |
| Product strategy, roadmap, build‑vs‑cut, metrics interpretation, monetization, investor/fundraising narrative | `chief-executive-officer` |
| Marketing & growth: Reddit/YouTube/Discord/forums, pixlstash.dev content, adoption tactics | `chief-marketing-officer` |
| Deep, multi-source, fact-checked research | `deep-research` |

Senior vs. junior: the senior decides and delegates; the junior only takes work that already has a clear pattern to copy and **escalates anything non‑trivial** instead of guessing.

### Handing a task to a skill

- **Single domain, advisory, or you'll do it inline:** invoke the skill in this conversation (the `Skill` tool, or a `/skill-name` slash command). It loads that persona's expertise into your context for the rest of the task.
- **Self-contained chunk, or a search/implementation-heavy job you don't want filling your context:** spawn a subagent (the `Agent` tool) and have it invoke the skill, then report back. Keeps the main context clean.
- Always pair the skill with its architecture doc: frontend → `docs/frontend_architecture.md`, backend → `docs/backend_architecture.md`, full‑stack → both + `docs/integration_architecture.md`.

### Splitting one task across several skills (in parallel)

Decompose by domain first, then fan out. **Independent** sub-tasks should run concurrently — issue all the subagent calls in a **single message** so they execute at once; **dependent** ones run in sequence.

- **Full‑stack feature** → split at the API boundary: `senior-backend-developer` (endpoint/model/migration) and `senior-frontend-developer` (UI/state) in parallel, then reconcile the contract against `docs/integration_architecture.md`. Each senior hands its routine sub-parts to the matching junior.
- **Honour the built-in escalation chains:**
  - Seniors spawn juniors for mechanical sub-work (`senior-backend-developer` → `junior-backend-developer`; `senior-frontend-developer` → `junior-frontend-developer`).
  - `ci-expert` must clear any workflow/CI change with `chief-security-officer` before it is pushed.
  - `chief-executive-officer` drives the execution skills — it tasks `ci-expert` (metrics/pipelines) and `chief-marketing-officer` (growth).
- **Gate, don't parallelize, the safety steps.** Anything touching auth, secrets, external exposure, dependencies, deploys, or CI must pass `chief-security-officer` review (or `/security-review`) **before merge/push** — that is a barrier *after* the implementation work, not a concurrent lane.
- When you fan out, give each skill a tightly-scoped brief and reconcile their outputs yourself. Don't let parallel agents edit the same files; split by file/area or sequence the overlap.


## Imports
- Mostly use imports at the top of the file. Local imports within functions are only acceptable if they are necessary to avoid circular dependencies, to reduce startup time for rarely used modules or if the import is *clearly* optional.
- Do not use local imports for libraries that are commonly used in the code base, like torch, numpy, PIL, cv2, etc. These should be imported at the top of the file for clarity and consistency.

## Exception handling
- Always log exceptions with as much context as possible (e.g., variable values, file paths, operation being performed) to facilitate debugging.
- Avoid silent failures. If an exception is caught, it should either be handled in a way that resolves the issue or logged with sufficient detail to understand the impact.
- Using `pass` to ignore exceptions is not acceptable. If you need to ignore an exception, you must log it with a warning or error level log message explaining why it is being ignored and what the potential implications are.

## Task System

- The TaskRunner class manages asynchronous tasks, allowing for background processing of image quality calculations and other operations without blocking the main server thread.
- Work is first found using the WorkPlanner (`pixlstash/work_planner.py`), whose `work_finders()` returns the registered finder instances that locate different types of work (e.g., quality calculation, metadata extraction). Each finder is a `Missing*Finder`/`*Finder` subclass of `BaseTaskFinder` in `pixlstash/tasks/`.
- Once work is found a new Task for a batch of images is created and added to the TaskRunner's queue.
- The TaskRunner continuously processes tasks from the queue, executing the associated work function, reporting progress and handling results.

## Fixing bugs and default error resolution approach
- NEVER assume a fix without understanding the root cause.
- ALWAYS read error messages carefully and check stack traces to identify the source of the error.
- NEVER apply fallback-based fixes unless I explicitly approve them in this conversation.
- REQUIRED debugging sequence: reproduce issue → isolate root cause → implement direct fix → validate with tests/log evidence.
- Fallbacks are LAST RESORT only, not a default strategy.
- If a fallback is approved and necessary, implement it so it does not mask the underlying issue and includes clear logging for future resolution.
- If you cannot resolve the root cause, document findings, blockers, and attempted fixes, then ask for direction instead of applying an unverified workaround.

## Alembic migrations
- Give every migration a descriptive name. The baseline rule is one new migration file per schema change, but **the branch decides how strictly to apply it:**
  - **Feature branch (schema still in flux):** it's fine to amend, squash, or merge migrations rather than stacking multiple migrations for the same change. Keep the migration history tidy before it lands.
  - **`main` branch:** strict patterns apply. A migration on `main` must never be modified; all subsequent schema changes go in new migration files. (Reason: anything on `main` may already have been deployed and run, so altering an existing migration would leave those databases divergent.)
- Place schema upgrade steps in strictly increasing version order; never insert a migration out of sequence, so upgrades always apply in the correct order.
- The Alembic revision identifier variables (`revision`, `down_revision`, `branch_labels`, `depends_on`) are read by Alembic at runtime via module import, not by explicit code references. Declare them as exported by including `__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]` after the `depends_on` line. This prevents false "unused variable" warnings from static analysers (including CodeQL) without needing `# noqa` comments. The script template (`pixlstash/migrations/script.py.mako`) already includes this line, so new migrations will have it automatically.
- When a code change requires existing data to be regenerated (e.g. tags, embeddings, quality scores), trigger reprocessing by resetting the relevant column(s) to `NULL` in the Alembic migration script. The `Missing*Finder` classes in `pixlstash/tasks/` query for pictures with `NULL` values and will automatically pick up those rows for reprocessing when the server next runs. Alembic migrations should only contain schema changes and this kind of targeted `NULL`-reset; no application logic should be placed in migrations.
- **All `op.add_column` calls must be conditional.** Always use `sa.inspect(op.get_bind())` to fetch existing columns and skip the `add_column` if the column already exists. The baseline migration (`0001_baseline`) uses `SQLModel.metadata.create_all()`, which creates tables with all current model columns; later migrations that blindly run `ALTER TABLE … ADD COLUMN` will therefore fail on a fresh database. The standard pattern is:
  ```python
  bind = op.get_bind()
  inspector = sa.inspect(bind)
  existing_cols = {col["name"] for col in inspector.get_columns("<table>")}
  if "<column>" not in existing_cols:
      op.add_column("<table>", sa.Column(...))
  ```
## Developer Workflows

- **Install dependencies:** `pip install -e .`
- **Run server:** `python -m pixlstash.app`
- **Run tests:** `python -m pytest -s -vvv --fast-captions`
- **Check formatting:** `ruff check pixlstash`
- **Build frontend:** `npm run build` (in `frontend/`)
- **Dev frontend:** `npm run dev` (in `frontend/`)

### Do not pass `--force-cpu` locally

**`--force-cpu` is a CI flag, not a local one.** The gate passes it in
`PYTEST_FLAGS` (`.github/workflows/ci.yml`) because GitHub's runners have no
GPU. A development box here does, and forcing CPU inference throws it away:
every model in the suite runs on the CPU instead, for no coverage that the GPU
path does not already give.

Copying the CI invocation verbatim is the easy mistake, and this file used to
invite it by listing the flag under **Run tests**.

**Also do not run the whole suite in one local process.** The gate shards it
eight ways for a reason (`--ci-shard N/8`), and a single serial `pytest tests/`
does all eight shards' work. Run the files your change actually touches and let
the sharded gate cover the rest. When a change is to something shared enough
that "the files it touches" is most of the suite, shard it locally the same way
CI does rather than waiting on one process.

## Never open a PR onto another PR

**A PR's base must be a long-lived branch — `develop`, `main`, a release branch.
Never another PR's branch.** Stacking reads as tidy and loses work.

It lost work here on 2026-08-11. Three shelf PRs merged inside forty seconds:

* **#873** had **#871**'s branch as its base. #871 merged to `develop`; #873 then
  merged **into #871's branch**, not into `develop`. GitHub only auto-retargets a
  PR when its base branch is *deleted*, and this one was not. The badge said
  merged, the content was not in the product, and it was found by chance two
  hours later — `grep -c withEmptyFolders frontend/src/utils/modelShelf.js`
  returned **0** on `develop`.
* **#872** merged at the head GitHub had recorded, which was one commit behind
  what had just been pushed to it. The newer commit was simply left on the
  branch. Same silent shape, different cause.

Neither was recoverable by looking at the PR list, because both said MERGED.

**This rule is about the BASE, not about waiting.** It does not say "wait for the
open PR to merge before you start". Depending on unmerged work is fine and
normal; *targeting its branch* is what is banned. There are exactly two ways:

1. **Push the commits onto that PR's own branch.** Right when the new work
   *belongs* to that PR — a review fix, a test it was missing. The PR updates,
   its checks re-run, and there is one thing to merge.
2. **Branch off the open PR to get its content, and target `develop` anyway.**
   Right when the new work is its own step that merely needs the other's code,
   which is the common case. The new PR carries the old one's commits in its
   history and in its diff until the old PR merges, at which point they become
   common ancestors and drop out of the diff by themselves. Nothing has to be
   rebased and nothing has to wait.

   The same shape *replaces* a PR: carry its full history, target the base it
   targeted, close the old one.

Both keep a single merge into a long-lived branch. Neither creates a base that
can be merged, deleted or retargeted out from under you.

**The misreading to guard against**, which happened the day this was written:
treating option 2 as "open a PR only once the other has landed". That serialises
every dependent piece of work behind a review queue and buys nothing — the risk
was never *depending* on an open PR, only *merging into* one.

**Corollary — verify the merge, not the badge.** After a PR you care about is
merged, confirm its content actually reached the target:
`git merge-base --is-ancestor <head-at-merge> origin/develop`, or grep for a
symbol the PR introduced. `MERGED` is a statement about a pull request, not about
the branch you are going to build on next.

## Fixing a CI failure: update the existing PR, do not open another

**One full gate run costs ~200 runner-minutes** (8 Linux shards, 4 Windows, e2e,
checks). A PR is not free to open and it is not free to leave open — every push
to it, and every push to anything it is stacked on, runs the whole gate again.
On 2026-08-09 this repo had **13 PRs open at once** and opened **47 in a day**.
Treat runner time as a budget you are spending, because you are.

So, in order:

1. **A CI failure on a PR is fixed on that PR's branch.** Even when the cause is
   somewhere else entirely — a stale map, another PR's merge, an unrelated
   flake. Pushing the fix to the red PR turns it green in the run it was already
   going to spend. Opening a second PR to fix the first spends a second run and
   leaves the first red until the second lands.
2. **Check `gh pr list --state open` before opening anything.** Several agent
   sessions run against this repository at once and cannot see each other. On
   2026-08-09 two sessions independently fixed the same red guardrail (#848 and
   #851), which is ~400 runner-minutes for one change. This check costs one
   second.
3. **Fold the unblock into the fix.** If the guardrail is wrong *and* its data is
   stale, that is one PR, not two. Splitting them doubles the CI for no review
   benefit — the reviewer reads the same diff either way.
4. **Close a superseded PR the moment it is superseded**, not at merge time. A PR
   left open after its content moved elsewhere keeps drawing runs from every push
   to its base, and it is a live hazard: merging it can reintroduce exactly the
   code a later fix corrected.
5. **Prefer one PR per work step, with clean separated commits.** Split into a
   stack only when the pieces can genuinely **merge independently** — not merely
   because the diff is large. Review granularity comes from commits; a reviewer
   reads commits either way. A six-PR stack whose parts must all land together
   re-runs the gate on growing supersets and buys nothing.

The corollary for anyone orchestrating parallel agents: per-agent instructions
bound each agent's diff, and nothing bounds the *aggregate*. Counting the PRs
across all in-flight lanes is the orchestrator's job, and it is the one that was
not being done when the number reached 13.

## Answering a review: reply and resolve, don't just push a fix

**Addressing a review comment is three acts, not one: push the fix, reply on the
thread, resolve the thread.** A fix pushed in silence is invisible as an answer.
The reviewer is left with an open thread and a changed file, and has to
reconstruct whether the two are related, which is the work they asked you to do.

- **Reply on each thread**, naming the commit and what actually changed:
  `gh api repos/OWNER/REPO/pulls/N/comments/<comment_id>/replies -f body=...`
- **Then resolve it.** Only GraphQL can:
  `gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<id>"}) { thread { isResolved } } }'`
  Thread ids come from `pullRequest(number: N) { reviewThreads(first: 20) { nodes { id isResolved path comments(first: 1) { nodes { databaseId body } } } } }`.
- **Resolving without replying is worse than leaving it open**, because it reads
  as answered. Never resolve a thread you did not act on.
- **Disagreeing is a reply, not a silence.** If the comment is wrong, or the fix
  is deliberately different from what it asked for, say so on the thread and
  leave it for a human to resolve.
- **Collapsed "suppressed comments" have no thread**, so there is nothing to
  resolve and nothing tracking them. Pick them up in a top-level PR comment or
  they are simply lost. A Copilot review hides them in a `<details>` block below
  the per-file summary, which is easy to skim past.

**Verify the fix is on the PR head before calling the review answered**, by
grepping the *remote* branch for a symbol the fix introduces:
`git grep -q '<symbol>' origin/<branch>`. Do not use `headRefOid` for this. As
the corollary in "Never open a PR onto another PR" says of merges: for a merged
PR that value *is* the merged commit, so comparing against it is a tautology
that cannot fail. On 2026-08-11 two review fixes went missing behind exactly
that, one to a merge that beat the push by 40 seconds, and one to a push made 14
minutes after the merge and then "verified" with `headRefOid` and reported as
landed.

## New test files must be gated in CI

Every file under `tests/` must be either listed in the `backend` job's file list in `.github/workflows/ci.yml` (preferred, since it then blocks PRs) or listed in `DEFERRED_FROM_GATE` in `tests/test_ci_shards.py` with a reason if it is not green yet. `tests/test_ci_shards.py::test_every_test_file_is_classified` fails the build on anything unclassified, so a new suite that is written and passing locally still breaks CI until it is listed. Add the file in alphabetical position as part of the same change that adds the test.

**Do not try to place a test in a particular shard.** The blocking gate runs `--ci-shard N/8`, which splits by *test* rather than by file (`tests/conftest.py`), so all eight shards share one file list and a single file's tests spread across all of them. There is no per-shard list to pick and no placement decision to make when you add a test file.

The deal is time-balanced, not round-robin. `_time_balanced_shard_assignment` (`tests/conftest.py`) seeds every test on its round-robin position and then re-places the ones it has timings for, longest-processing-time-first, over the committed `tests/ci_test_durations.json`; a test missing from the map keeps its seeded position and is charged the median cost. Shards therefore come out level on recorded *time* (max/min 1.000 at N=8), and `tests/test_ci_shards.py::test_recorded_durations_actually_balance_the_gate` fails the build above 1.05, reading N from the workflow so a resize has to re-prove it. CI records the data that feeds this: `PYTEST_FLAGS` carries `--durations=0 --durations-min=0`, and `scripts/record_test_durations.py` turns that output back into the committed map. Adding a test file therefore imposes no obligation on the map: refreshing it is an optimisation chore, never a correctness obligation, and a stale map costs a little balance and never coverage. The guardrail names every gated file it has not timed as a warning rather than a failure, because that failure could only ever land on an unrelated PR — the file is already on `develop` by the time the map is stale. It does fail once the map times less than `MINIMUM_GATE_COVERAGE` (90%) of the gated files, at which point the 1.05 balance figure is describing a shrinking subset rather than the gate. Refresh it by dispatching `.github/workflows/record-test-durations.yml`, which harvests a green `backend` gate run and pushes the regenerated map to a branch.

## Tests: reuse the environment, don't rebuild it

The expensive thing in this suite is **not the test, it is the environment
rebuild**. Measured: standing up a `Server` costs ~1.35 s; the assertion it
serves costs 0.003-0.25 s. Sixty-eight of eighty-nine `Server`-using files
rebuilt that environment *inside every test body*, which is most of how the
backend gate reached 45 min per shard. Converting three files to a shared
module-scoped environment cut them 8.5x, 8.2x and 1.4x.

`--durations` will not show you this. Work done in the test body is reported as
`call`, so a test spending 2.5 s building a server reads `setup 0.00s`.

**Before adding a test, look for a module whose environment already gives you
what you need, and put your assertion there.** Stand up a new environment only
when the state you need genuinely conflicts with what is already there. A new
assertion in a warm module is close to free; a new `with Server(...)` block is
not.

When a file does need its own fixture:

- **Module scope, not class scope.** The gate shards *individual tests* across 8
  shards, so a class-scoped fixture is rebuilt once per shard per class and
  barely amortises. `tests/test_authentication.py` is the reference shape;
  `tests/test_reviews_api.py` and `tests/test_operation_log.py` are the worked
  examples.
- **Reset every global observable the module touches, not just the obvious one.**
  `test_operation_log` needed the Scrapheap cleared as well as the `operation`
  table, because `POST /pictures/scrapheap/restore` with no body restores *all*
  soft-deleted pictures.
- **Assert on identity, not counts.** State accumulates across a shared module, so
  a test counting a global collection breaks — or worse, passes for the wrong
  reason.
- **Integrity checks belong in the autouse fixture, never a trailing "canary"
  test.** The sharder partitions individual tests, so a trailing canary lands in
  one shard while the tests it was meant to guard land in others.
- **A shared server runs the background work a per-test server used to suppress.**
  A fresh vault has nothing to backfill and no models loaded, so sweeps sit in a
  long backoff; a warm one lands *inside* your test and overwrites hand-written
  fixture data (`ImageEmbeddingTask` owns the embedding *and* the perceptual
  hash; `TagTask` deletes existing `Tag` rows before writing its own). Pull the
  conflicting finders out of the planner for the module's lifetime — waiting for
  it to settle measured *slower* than the per-test servers it replaces. Keep the
  planner itself running; the import endpoint refuses while workers are down.
- **Stop the schedulers before wiping tables.** `BaseTaskFinder._claimed_picture_ids`
  and `WorkPlanner._inflight_by_finder` drain only on a task's completion path, so
  a cancelled task never releases its ids — and SQLite reuses picture ids from 1
  after a wipe, so a finder then permanently refuses the next test's pictures.
- **Order the wipe instead of reaching for a PRAGMA.** Restore or insert parents
  first, then delete children, and no foreign-key trickery is needed at all.
  `PRAGMA defer_foreign_keys = ON` does not work here: with pysqlite a PRAGMA
  issued before any DML runs in autocommit, so the deferral is already gone by
  the time the DELETEs open their own transaction. #822 measured it reading back
  `0`. Never `PRAGMA foreign_keys` off/on either: if a delete raises in between,
  enforcement stays OFF on that pooled connection and silently weakens every
  later test. Whichever approach you pick, demonstrate it took effect rather
  than assume it did.
- **Anything that used to die with the per-test engine now leaks** — `connect`
  listeners, patched SQLite limits, monkeypatched globals. Undo them in a
  `finally`.
- **Anchor a mutation check, then confirm it landed.** Proving an assertion can
  fail means editing the thing it asserts on, and a first-occurrence replace
  often hits prose instead of code. `AUTHZ_GATE_ENFORCING = True` appears twice
  in `pixlstash/authz/gate.py`, once inside a docstring: mutate that one and
  behaviour is unchanged, the suite stays green, and the green reads as "this
  assertion is dead". Anchor on `^AUTHZ_GATE_ENFORCING = True$` and verify the
  mutation is in the line you meant.
- **Assert the route resolves before trusting an authz negative.** Those paths
  are guarded twice, by the READ-token middleware and by the gate, and the
  middleware runs ahead of routing. A request to a renamed or nonexistent route
  therefore returns the same 403 as a genuine in-scope refusal, so a negative
  test that names its path as a string can pass against a dead path.

For an authz or security suite the shared environment also has to keep the
negative assertions honest. Re-mint credentials in the autouse fixture, keep the
in-scope positive control next to every negative one, and prove the result can
still fail: remove one scope guard in `pixlstash/`, confirm the suite goes red,
restore it. A negative assertion that passes because the credential was missing,
rather than because the scope was refused, is a silent coverage loss — and it is
the specific failure this repo has to design against.

## Reviews

If asked to do a review on a branch, write the review into docs/reviews/NAME_OF_BRANCH.md

**`docs/reviews/` is gitignored on purpose: reviews stay on the machine that
wrote them and are not pushed to GitHub.** So do not force-add a review doc, and
do not "fix" the ignore rule. Two consequences worth knowing:

- The rule only affects *new* files. Review docs added before it exist are still
  tracked, and `.gitignore` does not apply to a tracked file. Editing one of
  those: plain `git add` fails with "paths are ignored by one of your .gitignore
  files", so use `git add -u docs/reviews/<file>`.
- **Nothing CI-enforced may read a file under `docs/reviews/`**, because a fresh
  checkout is not guaranteed to have it. Living contract documents therefore live
  in `docs/` proper, not in `docs/reviews/`. The authz coverage matrix is the
  worked example: it is `docs/authz-coverage-matrix.md`, tracked, and parsed by
  `tests/test_architecture_guardrails.py::test_coverage_matrix_document_matches_the_registry`.

## Security & authorization review process

Mandatory for any change touching authentication, authorization, or access-scope (tokens, sharing, per-object/per-resource access). These exist because a BOLA audit once shipped a "fix" that closed four endpoints and left three siblings of the same severity open (whole-library leaks via `/pictures/{id}/{field}`, `/stacks/{id}/pictures`, and a `character_id=UNASSIGNED` bypass). The misses were completeness and verification failures, not knowledge gaps.

- **Coverage matrix, not a findings list.** Enumerate *every* endpoint that returns or mutates resource data and record, per endpoint, where its access check is. Empty cells are the bug list. Completeness must be arithmetic, not judgement, before an authz audit is called done.
- **Mind the decomposition seams.** When a review is fanned out across file clusters, a risk class that spans files (e.g. read-BOLA in a CRUD module assigned to the "uploads" reviewer) falls between mandates. Assign by risk class as well as by file, and explicitly cover the read endpoints in every module.
- **Trace the whole input space of a touched endpoint.** A fix verified only on the default path is not verified. Exercise alternate branches and parameters of the same handler (e.g. `character_id=UNASSIGNED`, `?fields=grid`, stream vs list).
- **Independent adversarial sign-off before "done".** The author of a security fix must not be the one who certifies it complete. Spawn a separate reviewer/board tasked to *refute* and to hunt sibling and leftover holes; run it before merge, not after, and reproduce each finding.
- **Tests assert both directions and fail-closed.** Cover the negative (out-of-scope blocked) and the positive (in-scope still works; over-blocking is its own regression), across sibling vectors, ideally written by someone other than the fix author.
- **Prefer deny-by-default, centralised authz.** Per-handler opt-in checks guarantee this bug class recurs. Flag every new ad-hoc per-endpoint check and move toward a single chokepoint that fails closed for unrecognised routes/scopes.

### Endpoint scope enforcement (SHIPPED — the central authz gate)

**Object authorization is centralised and deny-by-default. Do NOT add per-handler scope checks.** The `AuthzGate` router dependency (`pixlstash/authz/gate.py`) enforces every data route from its declared `AccessPolicy` in `pixlstash/authz/registry.py` (`ROUTE_POLICIES`), calling the membership helpers in `pixlstash/authz/membership.py`. `AUTHZ_GATE_ENFORCING = True`: an **undeclared data route is denied (403) at runtime AND fails the startup assertion + CI guardrail** (`tests/test_architecture_guardrails.py::test_all_routes_declare_access_policy`, allowlist zero). Safe by *omission* is now a machine fact, not a human-remembered check — which is what finally closes the BOLA-by-omission class that recurred three times here (the R2 `GET /pictures/{id}/character_likeness` leak, `docs/reviews/v1.5.1-security-signoff.md`).

- **A new or modified data endpoint's only required action is to add its `(method, effective_path) → RoutePolicy(...)` entry to `ROUTE_POLICIES`.** Pick the `AccessPolicy` that fits: `PICTURE_SCOPED` / `SET_SCOPED` / `CHARACTER_SCOPED` / `PROJECT_SCOPED` (+ `id_param=` or `body_ids=`), `SCOPED_LIST`, `OWNER_ONLY`, `LOCAL_OWNER_ONLY` / `LOOPBACK_OWNER_ONLY` (§16.3 host-capability, `justification=` mandatory), `PUBLIC` (`justification=` mandatory), or `ANY_TOKEN` (returns no per-object data). The enum is closed — a genuinely new access level is a deliberate edit to `policy.py` + tests.
- **Do NOT** add inline `enforce_picture_scope` / `require_unscoped_owner` / `token_scope` ladders in handlers — the gate owns them, and a duplicate check is debt to be removed, not added. Flag any new per-handler authz check in review.
- **The only surviving inline object checks** are the 4 name-derived `resolved_inline=True` routes (by-name set/character/project — the gate cannot resolve name→id without duplicating handler logic). All four also carry an inline `enforce_project_path_scope` call on the **project** named in their path, which the gate's query-param chokepoint cannot see; it must run on the resolved id *before* any membership query, or the route's 404 branches become a project-existence oracle (#708). Do not remove either inline check until a shared name→id resolver exists. They are recorded in `docs/backend_architecture.md` §16.1 / §16.6 and the coverage matrix.
- **The disciplines still apply.** The coverage matrix (`docs/authz-coverage-matrix.md`) must stay arithmetically complete (the CI guardrail enforces it); tests in both directions (out-of-scope 403 AND in-scope 200 — over-blocking is its own regression) are mandatory for any authz change; and an independent adversarial sign-off (author must not certify their own security work) gates any change to the gate, the registry's scope declarations, or the membership helpers. See `docs/backend_architecture.md` §16.2 (shipped design) and §16.3 for the host-capability tiers.
- **Rollback:** `AUTHZ_GATE_ENFORCING = False` in `pixlstash/authz/gate.py` reverts both object-enforcement and unknown-route fail-closed in one line, leaving declarations and helpers in place.

## Conventions & Patterns

- **Throughput & batching:** Always think about throughput and concurrency. Evaluate whether a piece of work is best handled as a batch following ML best practices — for images this usually means sorting and grouping by size so each batch is composed of equally-sized tensors (e.g. image and face-crop quality calculation).
- **Error Handling:** Always set metrics to -1.0 if calculation fails; log detailed warnings for OpenCV errors (file path, bbox, crop shape, error).
- **Database Updates:** Log before updating metrics; ensure all metrics are set to avoid repeated selection.
- **Bounding Boxes:** Clamp to image edges before cropping/resizing.

## Integration Points

- **External:** Uses OpenCV, NumPy, PIL, FastAPI, rapidfuzz, and Vue 3.
- **Cross-component:** Backend serves REST API; frontend consumes API and displays images/metrics.

## Always Run Ruff on Python code before considering the job complete

Do ruff format and ruff check.

## Commit messages

Write short concise commit messages without a torrent of detail.

---

*These instructions are enforced for all AI coding agents working in this repository. Update this file to refine agent behavior as needed.*
