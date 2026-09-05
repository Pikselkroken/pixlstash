# Copilot and Claude Instructions for PixlStash

## Working in the checkout

Several agent sessions may run against one clone at once and cannot see each
other. Before touching anything: `git status && git branch --show-current`. If
the tree holds uncommitted changes you did not make, or the current branch is
another session's work in progress, say so and stop rather than switching under
it.

Start work on its own branch with the base set explicitly: feature work on
`develop`, bugfixes on `main` (see the PR base rules):

```
git fetch origin && git checkout -b <branch> origin/<base>
```

**Do not create a worktree on your own.** When every session made one, the cost
was fifty-odd checkouts and nothing where the person testing expected it. If
one is genuinely necessary (a second lane at the same time, a long bisect) say
so and let the person decide.

**The hub and the vault live outside the repo** (platformdirs user data dir), so
every checkout runs against the same library. Do not "fix" it into per-checkout
data.

Commit and push from the checkout you are in, open the PR, and when it merges
`git checkout <base> && git pull`. Stage by name, never `git add -A`: never
stage a file you did not write in this session.

Sessions that only read (questions, reviewing pushed code) can stay put.

### Say where the work can be tested

A session that changes behaviour ends by stating the checkout, the branch and
the commands:

```
Test at: <checkout>   (branch <branch>, based on <base>)
  backend:  cd <checkout> && python -m pixlstash.app
  frontend: cd <checkout>/frontend && npm run dev
```

If the work is already merged, say so and say to pull. "It's on the branch" is
not a test path.

### Say what to test, in the session, never in the PR

A path is not a handoff. Only the session that wrote the change knows which
screen it lands on, what number should appear, and which of its own steps it is
least sure of; "test at `<path>`" makes the tester reverse-engineer all three.

**The handoff goes to the person, in the session. It does not go in the PR.** A
test plan is made of this machine's absolute paths, its library, its folder
names and its disk figures. A PR is public and permanent. **Never put a test
plan, a path under `$HOME`, a listing of the owner's library, or their disk
usage into a PR body, a commit message or a PR comment.** A `## Test this`
section carrying the owner's home directory and private model inventory has
already had to be edited back out of one. Aggregate engineering figures that
justify a decision are fine: "0.01 s to read the index", "a 339 MB tagger". The
line is between what a mechanism costs and what is on that person's disk.

End the session with the plan, five parts, the last two being the ones that get
skipped:

1. **Where**: the test-at block above, always, plus any step that has to happen
   first ("restart the backend, the declaration runs at start-up").
2. **What to look at**, as numbered checks against a named screen or control:
   "Model folders dialog, toolbar folder icon", not "the folders UI".
3. **The expected values, concretely**: the row count, the size, "no size at
   all rather than `0 B`". A tester cannot confirm a number nobody stated, and a
   wrong one is invisible next to a vague one. This is exactly the part that
   must not be published.
4. **What "wrong" looks like**, per check: "wrong if a locked row offers scan
   or forget". That lets someone who does not know the design spot a regression
   rather than assume the screen is meant to look that way.
5. **What you could not test yourself**, named and separated: cold-boot cost,
   whether a long list is pleasant, anything needing hardware or a judgement
   call. This is the part actually being handed off; the rest is verification.

Order the checks by risk and say which one matters. A check that could destroy
data goes near the top and tells the tester to stop rather than complete the
gesture: "the drag must never start; if the row picks up, say so and do not
drop it." If a suite already proves something, say so in a line and spend the
human's attention on what the suite cannot see.

## Patch Reliability Policy

- **Read before you edit.** Read enough surrounding context (at least 50 lines before and after the target) to understand structure, logic, and dependencies before generating a patch. If placement is ambiguous, read more until it is certain.
- **Don't assess what you haven't read.** Never critique, judge, or make claims about the adequacy of a file, document, or module you have not actually read. Read it first, or explicitly scope your statement to what you did read and flag the gap.
- **Reject illogical edits.** Check every patch for abrupt changes that don't fit the surrounding code, e.g. a method placed outside its class, code inserted above the top imports, or a missing blank line between top-level definitions.
- **Class member order:** imports → class definition → Google-style docstring → class-level variables → `__init__` (including property initialisation) → properties (getters/setters) → public methods → private methods. Keep everything correctly indented within the class block.

## Project Architecture

Use the architecture documents according to the scope of the task.

**Read them by section, never end to end.** They are reference manuals:
`backend_architecture.md` is ~82k tokens, `frontend_architecture.md` ~53k,
`integration_architecture.md` ~24k. Reading all three costs ~160k tokens before
you have looked at a line of code, almost all of it about subsystems you are not
touching. Read the Table of Contents at the top, then only the sections covering
the code you are about to change plus any section they point you to, and widen
only when that turns out to be insufficient. `grep -n '^## ' docs/backend_architecture.md` gives the section map; `sed -n 'START,ENDp'`
reads one section. A whole-file read is a mistake unless you are auditing the
document itself.

1. Frontend tasks: the relevant sections of `/docs/frontend_architecture.md`.
2. Backend tasks: the relevant sections of `/docs/backend_architecture.md`.
3. Full-stack tasks: both, plus the `/docs/integration_architecture.md` sections
   covering the contract you are changing (§2 API surface, §8 event contract,
   and whichever feature section applies).
4. Any task that adds or changes UI (a feature, a component, a screen, a
   control): **the design manual in `/docs/design/` is mandatory, not
   advisory.** Read `/docs/design/visual-language.md` and build against the
   tokens in `/docs/design/design-tokens.css` and the color themes in
   `frontend/src/main.js`. New UI uses the existing tokens (spacing, radius,
   type ramp, elevation, motion, color): never a hardcoded hex, off-grid
   spacing, ad-hoc radius, raw `rgba(0,0,0,…)` shadow, or `em`/`px` font-size
   outside the ramp. A genuinely new value is a design decision for the
   `lead-designer` skill, not an inline one-off. Anything that changes a flow, a
   state, or what a control does also goes past the `ui-ux-expert`.

   **Read the design system before you build the surface.** It is a published
   Claude Design project,
   <https://claude.ai/design/p/ac544c9e-b278-4439-be75-e442fca29d41>, readable
   through the `DesignSync` tool; `ui_kits/app/` holds real specs for the app's
   screens, and building one that exists there without reading it is how a
   surface gets invented twice. Full contract in
   `docs/frontend_architecture.md` §7.

Task classification:
- UI, components, state management, routing, or client-side logic → frontend task, **and apply the design manual (item 4)**.
- APIs, storage, indexing, ML pipelines, or server-side logic → backend task.
- Both (e.g. API changes that require UI updates) → full-stack task.

When changing architecture or integration patterns, update the relevant documentation in the same change so future work follows the new approach.

## Skill Delegation

This repo ships role-specific **skills**: personas with their own expertise (and, for the developer roles, their own subagents). Route work to the skill that owns the domain instead of doing everything in one generalist pass. Check the available-skills list at the start of each session; the set can grow.

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
| Product strategy, roadmap, build-vs-cut, metrics interpretation, monetization, investor/fundraising narrative | `chief-executive-officer` |
| Marketing & growth: Reddit/YouTube/Discord/forums, pixlstash.dev content, adoption tactics | `chief-marketing-officer` |
| Deep, multi-source, fact-checked research | `deep-research` |

Senior vs. junior: the senior decides and delegates; the junior only takes work that already has a clear pattern to copy and **escalates anything non-trivial** instead of guessing.

### Handing a task to a skill

- **Single domain, advisory, or you'll do it inline:** invoke the skill in this conversation (the `Skill` tool, or a `/skill-name` slash command).
- **Self-contained chunk, or a search/implementation-heavy job you don't want filling your context:** spawn a subagent (the `Agent` tool) and have it invoke the skill, then report back.
- Always pair the skill with its architecture doc: frontend → `docs/frontend_architecture.md`, backend → `docs/backend_architecture.md`, full-stack → both + `docs/integration_architecture.md`.

### Splitting one task across several skills (in parallel)

Decompose by domain first, then fan out. **Independent** sub-tasks run concurrently: issue all the subagent calls in a **single message**. **Dependent** ones run in sequence.

- **Full-stack feature** → split at the API boundary: `senior-backend-developer` (endpoint/model/migration) and `senior-frontend-developer` (UI/state) in parallel, then reconcile the contract against `docs/integration_architecture.md`. Each senior hands its routine sub-parts to the matching junior.
- **Honour the built-in escalation chains:** seniors spawn juniors for mechanical sub-work; `ci-expert` must clear any workflow/CI change with `chief-security-officer` before it is pushed; `chief-executive-officer` drives the execution skills (`ci-expert` for metrics/pipelines, `chief-marketing-officer` for growth).
- **Gate, don't parallelize, the safety steps.** Anything touching auth, secrets, external exposure, dependencies, deploys, or CI must pass `chief-security-officer` review (or `/security-review`) **before merge/push**: a barrier after the implementation work, not a concurrent lane.
- Give each skill a tightly-scoped brief and reconcile their outputs yourself. Don't let parallel agents edit the same files; split by file/area or sequence the overlap.

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
  - **`main` branch:** strict patterns apply. A migration on `main` must never be modified; all subsequent schema changes go in new migration files. Anything on `main` may already have been deployed and run, so altering an existing migration would leave those databases divergent.
- Place schema upgrade steps in strictly increasing version order; never insert a migration out of sequence.
- The Alembic revision identifier variables (`revision`, `down_revision`, `branch_labels`, `depends_on`) are read by Alembic at runtime via module import. Declare them as exported with `__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]` after the `depends_on` line, which prevents false "unused variable" warnings from static analysers (including CodeQL) without `# noqa`. The script template (`pixlstash/migrations/script.py.mako`) already includes this line.
- When a code change requires existing data to be regenerated (e.g. tags, embeddings, quality scores), trigger reprocessing by resetting the relevant column(s) to `NULL` in the migration. The `Missing*Finder` classes in `pixlstash/tasks/` query for `NULL` values and pick those rows up when the server next runs. Migrations contain only schema changes and this kind of targeted `NULL`-reset; no application logic.
- **All `op.add_column` calls must be conditional.** The baseline migration (`0001_baseline`) uses `SQLModel.metadata.create_all()`, which creates tables with all current model columns, so a later blind `ALTER TABLE … ADD COLUMN` fails on a fresh database. The standard pattern is:
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
GPU. A development box here has one, and forcing CPU inference throws it away
for no coverage the GPU path does not already give. Copying the CI invocation
verbatim is the easy mistake.

**Also do not run the whole suite in one local process.** The gate shards it
eight ways (`--ci-shard N/8`), and a single serial `pytest tests/` does all
eight shards' work. Run the files your change actually touches and let the
sharded gate cover the rest. When "the files it touches" is most of the suite,
shard it locally the same way CI does.

## Never open a PR onto another PR

**A PR's base must be a long-lived branch: `develop`, `main`, a release branch.
Never another PR's branch.** Stacking reads as tidy and loses work.

It lost work on 2026-08-11: #873 had #871's branch as its base; #871 merged to
`develop`, then #873 merged **into #871's branch** (GitHub only auto-retargets
a PR when its base branch is *deleted*). The badge said merged, the content was
not in the product, and the PR list could not show it.

**This rule is about the BASE, not about waiting.** Depending on unmerged work
is fine and normal; *targeting its branch* is what is banned. There are exactly
two ways:

1. **Push the commits onto that PR's own branch**, when the new work belongs to
   that PR (a review fix, a test it was missing).
2. **Branch off the open PR to get its content, and target `develop` anyway**,
   when the new work is its own step that merely needs the other's code. The
   new PR carries the old one's commits in its diff until the old PR merges, at
   which point they become common ancestors and drop out by themselves. Nothing
   has to be rebased and nothing has to wait. The same shape *replaces* a PR:
   carry its full history, target the base it targeted, close the old one.

The misreading to guard against: treating option 2 as "open a PR only once the
other has landed". That serialises every dependent piece of work behind a
review queue and buys nothing.

**Corollary: verify the merge, not the badge.** After a PR you care about is
merged, confirm its content actually reached the target:
`git merge-base --is-ancestor <head-at-merge> origin/develop`, or grep for a
symbol the PR introduced. `MERGED` is a statement about a pull request, not
about the branch you are going to build on next.

## Fixing a CI failure: update the existing PR, do not open another

**One full gate run costs ~200 runner-minutes** (8 Linux shards, 4 Windows, e2e,
checks), and every push to a PR runs the whole gate again. This repo once had
13 PRs open at once and opened 47 in a day. Runner time is a budget.

1. **A CI failure on a PR is fixed on that PR's branch**, even when the cause is
   somewhere else entirely (a stale map, another PR's merge, an unrelated
   flake). Pushing the fix to the red PR turns it green in the run it was
   already going to spend; a second PR spends a second run and leaves the first
   red until the second lands.
2. **Check `gh pr list --state open` before opening anything.** Sessions cannot
   see each other; two once fixed the same red guardrail independently (#848
   and #851), ~400 runner-minutes for one change.
3. **Fold the unblock into the fix.** If the guardrail is wrong *and* its data is
   stale, that is one PR, not two; the reviewer reads the same diff either way.
4. **Close a superseded PR the moment it is superseded**, not at merge time. Left
   open it keeps drawing runs from every push to its base, and merging it can
   reintroduce exactly the code a later fix corrected.
5. **Prefer one PR per work step, with clean separated commits.** Split into a
   stack only when the pieces can genuinely **merge independently**, not merely
   because the diff is large. Review granularity comes from commits.

For anyone orchestrating parallel agents: per-agent instructions bound each
agent's diff, and nothing bounds the aggregate. Counting the PRs across all
in-flight lanes is the orchestrator's job.

## Answering a review: reply and resolve, don't just push a fix

**Addressing a review comment is three acts: push the fix, reply on the thread,
resolve the thread.** A fix pushed in silence leaves the reviewer with an open
thread and a changed file, reconstructing whether the two are related.

- **Reply on each thread**, naming the commit and what actually changed:
  `gh api repos/OWNER/REPO/pulls/N/comments/<comment_id>/replies -f body=...`
- **Then resolve it.** Only GraphQL can:
  `gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<id>"}) { thread { isResolved } } }'`
  Thread ids come from `pullRequest(number: N) { reviewThreads(first: 20) { nodes { id isResolved path comments(first: 1) { nodes { databaseId body } } } } }`.
- **Resolving without replying is worse than leaving it open**, because it reads
  as answered. Never resolve a thread you did not act on.
- **Disagreeing is a reply, not a silence.** If the comment is wrong, or the fix
  is deliberately different, say so on the thread and leave it for a human.
- **Collapsed "suppressed comments" have no thread**, so nothing tracks them.
  Pick them up in a top-level PR comment or they are lost. A Copilot review
  hides them in a `<details>` block below the per-file summary.

**Verify the fix is on the PR head before calling the review answered**, by
grepping the *remote* branch for a symbol the fix introduces:
`git grep -q '<symbol>' origin/<branch>`. Do not use `headRefOid`: for a merged
PR that value *is* the merged commit, so comparing against it is a tautology,
and two review fixes went missing behind exactly that.

## New test files must be gated in CI

Every file under `tests/` must be either listed in the `backend` job's file list in `.github/workflows/ci.yml` (preferred, since it then blocks PRs) or listed in `DEFERRED_FROM_GATE` in `tests/test_ci_shards.py` with a reason if it is not green yet. `tests/test_ci_shards.py::test_every_test_file_is_classified` fails the build on anything unclassified. Add the file in alphabetical position as part of the same change that adds the test.

**Do not try to place a test in a particular shard.** The gate's `--ci-shard N/8` splits by *test*, not by file (`tests/conftest.py`), so all eight shards share one file list and there is no placement decision to make.

The deal is time-balanced: `_time_balanced_shard_assignment` (`tests/conftest.py`) seeds every test round-robin, then re-places the ones it has timings for, longest first, over the committed `tests/ci_test_durations.json`; an untimed test keeps its seeded position at the median cost. `tests/test_ci_shards.py::test_recorded_durations_actually_balance_the_gate` fails the build when shards diverge above 1.05. Adding a test file imposes no obligation on the map: a stale map costs a little balance, never coverage, and untimed gated files are a warning. It does fail once the map times less than `MINIMUM_GATE_COVERAGE` (90%) of the gated files. Refresh it by dispatching `.github/workflows/record-test-durations.yml`, which harvests a green `backend` gate run and pushes the regenerated map to a branch.

**When the coverage floor fails on your own branch, the dispatch cannot fix it**: it only harvests an already-green run, and your PR's run is the red one. Instead run the missing files locally with the gate's own flags (`--force-cpu --fast-captions --durations=0 --durations-min=0`; here `--force-cpu` is mandatory because the point is to model the CPU-only runners) and feed the output through `scripts/record_test_durations.py`. The script **rebuilds the whole document from only the input you give it**, so read the existing map first and merge the new entries into it (`durations.update(...)`) before writing, or every other test's time is silently dropped. Set `recorded_from` to say the run was local, as `tests/test_library_insights.py`'s entry already does.

## Stand-in values: what is safe to write down

Pushes are scanned for secrets and private information, and the scan reads
**added lines**. Merging `develop` into a branch re-presents every literal on
`develop` as an added line, so a literal already merged can block a branch that
never touched it (#963). The rules below are the vocabulary the scan is quiet
about.

| You need | Write | Why |
|---|---|---|
| A private IPv4 in a test | `LAN_IPV4`, `PRIVATE_10_IPV4` or `PRIVATE_172_IPV4` from `tests/network_vectors.py` | The first host of each RFC 1918 block identifies nobody. Any other quad is read as somebody's network. |
| RFC 1918 itself | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` | A definition, not a machine. |
| A private IP in prose | `<lan-ip>`, `<your-server-ip>`, or name the block | A worked example is where a real address gets copied in. |
| A non-private example address | `192.0.2.x`, `198.51.100.x`, `203.0.113.x`, `2001:db8::` (RFC 5737 / 3849) | Reserved for documentation; never a finding. |
| Loopback, wildcard, CGNAT, a public sentinel | `127.0.0.1`, `0.0.0.0`, `100.64.0.x`, `8.8.8.8` | Not private; never a finding. |
| A fixture password, token or key | A value starting `test-`, `example-`, `dummy-`, `fake-`, `placeholder-`, or `$…`/`os.environ[…]` | The marker records that the value was invented. It is **not** a way to quieten a real one. |
| An email | `me@example.com`, or any name under `.test` / `.invalid` / `.localhost` | RFC 2606. |
| A home path | `/home/me/`, `/home/you/`, `~/` | Placeholder logins. A real login is private information. |

Never write another RFC 1918 quad, `/home/<a real login>/`, `/Users/<anybody>/`,
or a real address of any kind. There is no allowlist and no `# not a secret`
comment: the answer to a false positive is to change the line.
`tests/test_architecture_guardrails.py::test_no_unsanctioned_private_address_literal`
fails the build on a new private-address literal under `tests/`, `docs/`,
`pixlstash/`, `frontend/e2e/` or `README.md`.

### The owner's address is published on purpose

**One real email address appears in this repository, it is the owner's own, and
every occurrence is a deliberate declaration**: the plugin `author` field
(`pixlstash/image_plugins/built-in/*.py`, `pixlstash/tagger_plugins/*.py`),
package authorship (`pyproject.toml`, `electron/package.json`), and the contact
a reporter is told to use (`SECURITY.md`, `PRIVACY.md`, `CODE_OF_CONDUCT.md`,
`website/privacy.html`, and the gated test that asserts it,
`tests/test_security_supported_versions.py`). Do not remove or rewrite any of
them: editing the contact out of `SECURITY.md` reds the gate *and* deletes the
address somebody is supposed to report a vulnerability to.

When a push scan cites those lines after a merge of `develop`, **prove each
cited line is one of these** (already on `develop`, in a file your branch does
not touch) with `git diff origin/develop...HEAD` and `git log -S`, then say so
and leave the release to a person. Clear the lines one by one. This is not an
allowlist for anything else: a finding outside those files is a finding.

## Tests: reuse the environment, don't rebuild it

The expensive thing in this suite is **the environment rebuild, not the test**.
Standing up a `Server` costs ~1.35 s; the assertion it serves costs
0.003-0.25 s. Rebuilding it inside every test body is most of how the backend
gate reached 45 min per shard; a shared module-scoped environment cut files 8x.
`--durations` will not show you this: work done in the test body is reported as
`call`, so a test spending 2.5 s building a server reads `setup 0.00s`.

**Before adding a test, look for a module whose environment already gives you
what you need, and put your assertion there.** Stand up a new environment only
when the state you need genuinely conflicts with what is already there.

When a file does need its own fixture:

- **Module scope, not class scope.** The gate shards individual tests, so a
  class-scoped fixture is rebuilt once per shard per class and barely
  amortises. `tests/test_authentication.py` is the reference shape;
  `tests/test_reviews_api.py` and `tests/test_operation_log.py` are worked
  examples.
- **Reset every global observable the module touches, not just the obvious one.**
  `test_operation_log` needed the Scrapheap cleared as well as the `operation`
  table, because `POST /pictures/scrapheap/restore` with no body restores *all*
  soft-deleted pictures.
- **Assert on identity, not counts.** State accumulates across a shared module,
  so a test counting a global collection breaks, or passes for the wrong reason.
- **Integrity checks belong in the autouse fixture, never a trailing "canary"
  test.** The sharder partitions individual tests, so a trailing canary lands in
  one shard while the tests it guards land in others.
- **A shared server runs the background work a per-test server used to suppress.**
  A warm vault's sweeps land inside your test and overwrite hand-written fixture
  data (`ImageEmbeddingTask` owns the embedding *and* the perceptual hash;
  `TagTask` deletes existing `Tag` rows before writing its own). Pull the
  conflicting finders out of the planner for the module's lifetime; waiting for
  it to settle measured slower than per-test servers. Keep the planner itself
  running; the import endpoint refuses while workers are down.
- **Stop the schedulers before wiping tables.** `BaseTaskFinder._claimed_picture_ids`
  and `WorkPlanner._inflight_by_finder` drain only on a task's completion path,
  and SQLite reuses picture ids from 1 after a wipe, so a finder then
  permanently refuses the next test's pictures.
- **Order the wipe instead of reaching for a PRAGMA.** Restore or insert parents
  first, then delete children. `PRAGMA defer_foreign_keys = ON` does not work
  here: with pysqlite a PRAGMA issued before any DML runs in autocommit, so the
  deferral is gone by the time the DELETEs open their own transaction (#822
  measured it reading back `0`). Never `PRAGMA foreign_keys` off/on either: if
  a delete raises in between, enforcement stays OFF on that pooled connection
  and silently weakens every later test. Demonstrate whatever you pick took
  effect.
- **Anything that used to die with the per-test engine now leaks**: `connect`
  listeners, patched SQLite limits, monkeypatched globals. Undo them in a
  `finally`.
- **Anchor a mutation check, then confirm it landed.** `AUTHZ_GATE_ENFORCING =
  True` appears twice in `pixlstash/authz/gate.py`, once inside a docstring;
  mutate that one and the suite stays green, reading as "this assertion is
  dead". Anchor on `^AUTHZ_GATE_ENFORCING = True$` and verify the mutation is
  in the line you meant.
- **Assert the route resolves before trusting an authz negative.** The
  READ-token middleware runs ahead of routing, so a renamed or nonexistent
  route returns the same 403 as a genuine in-scope refusal, and a negative test
  that names its path as a string can pass against a dead path.

For an authz or security suite the shared environment also has to keep the
negative assertions honest: re-mint credentials in the autouse fixture, keep the
in-scope positive control next to every negative one, and prove the result can
still fail (remove one scope guard in `pixlstash/`, confirm red, restore). A
negative that passes because the credential was missing rather than because the
scope was refused is the specific silent coverage loss this repo designs against.

## Reviews

If asked to do a review on a branch, write the review into `docs/reviews/NAME_OF_BRANCH.md`.

**`docs/reviews/` is gitignored on purpose: reviews stay on the machine that
wrote them.** Do not force-add a review doc or "fix" the ignore rule.

- The rule only affects *new* files. Review docs added before it are still
  tracked; editing one, plain `git add` fails as ignored, so use
  `git add -u docs/reviews/<file>`.
- **Nothing CI-enforced may read a file under `docs/reviews/`**, because a fresh
  checkout is not guaranteed to have it. Living contract documents live in
  `docs/` proper: the authz coverage matrix is `docs/authz-coverage-matrix.md`,
  tracked, and parsed by
  `tests/test_architecture_guardrails.py::test_coverage_matrix_document_matches_the_registry`.

## Security & authorization review process

Mandatory for any change touching authentication, authorization, or access-scope (tokens, sharing, per-object/per-resource access). A BOLA audit once shipped a "fix" that closed four endpoints and left three siblings of the same severity open (whole-library leaks via `/pictures/{id}/{field}`, `/stacks/{id}/pictures`, and a `character_id=UNASSIGNED` bypass). The misses were completeness and verification failures, not knowledge gaps.

- **Coverage matrix, not a findings list.** Enumerate *every* endpoint that returns or mutates resource data and record, per endpoint, where its access check is. Empty cells are the bug list. Completeness must be arithmetic before an authz audit is called done.
- **Mind the decomposition seams.** A risk class that spans files (read-BOLA in a CRUD module assigned to the "uploads" reviewer) falls between mandates. Assign by risk class as well as by file, and explicitly cover the read endpoints in every module.
- **Trace the whole input space of a touched endpoint.** Exercise alternate branches and parameters of the same handler (`character_id=UNASSIGNED`, `?fields=grid`, stream vs list).
- **Independent adversarial sign-off before "done".** The author of a security fix must not certify it complete. Spawn a separate reviewer tasked to *refute* and to hunt sibling and leftover holes; run it before merge and reproduce each finding.
- **Tests assert both directions and fail-closed.** Cover the negative (out-of-scope blocked) and the positive (in-scope still works; over-blocking is its own regression), across sibling vectors.
- **Prefer deny-by-default, centralised authz.** Flag every new ad-hoc per-endpoint check.

### Endpoint scope enforcement (SHIPPED: the central authz gate)

**Object authorization is centralised and deny-by-default. Do NOT add per-handler scope checks.** The `AuthzGate` router dependency (`pixlstash/authz/gate.py`) enforces every data route from its declared `AccessPolicy` in `pixlstash/authz/registry.py` (`ROUTE_POLICIES`), calling the membership helpers in `pixlstash/authz/membership.py`. With `AUTHZ_GATE_ENFORCING = True` an **undeclared data route is denied (403) at runtime AND fails the startup assertion + CI guardrail** (`tests/test_architecture_guardrails.py::test_all_routes_declare_access_policy`, allowlist zero). Safe by omission is a machine fact, which closes the BOLA-by-omission class that recurred three times here.

- **A new or modified data endpoint's only required action is to add its `(method, effective_path) → RoutePolicy(...)` entry to `ROUTE_POLICIES`.** Pick the `AccessPolicy` that fits: `PICTURE_SCOPED` / `SET_SCOPED` / `CHARACTER_SCOPED` / `PROJECT_SCOPED` (+ `id_param=` or `body_ids=`), `SCOPED_LIST`, `OWNER_ONLY`, `LOCAL_OWNER_ONLY` / `LOOPBACK_OWNER_ONLY` (§16.3 host-capability, `justification=` mandatory), `PUBLIC` (`justification=` mandatory), or `ANY_TOKEN` (returns no per-object data). The enum is closed; a new access level is a deliberate edit to `policy.py` + tests.
- **Do NOT** add inline `enforce_picture_scope` / `require_unscoped_owner` / `token_scope` ladders in handlers; the gate owns them, and a duplicate check is debt to be removed.
- **The only surviving inline object checks** are the 4 name-derived `resolved_inline=True` routes (by-name set/character/project), which also carry an inline `enforce_project_path_scope` call on the project named in their path. It must run on the resolved id *before* any membership query, or the route's 404 branches become a project-existence oracle (#708). Do not remove either inline check until a shared name→id resolver exists. See `docs/backend_architecture.md` §16.1 / §16.6 and the coverage matrix.
- **The disciplines still apply.** The coverage matrix (`docs/authz-coverage-matrix.md`) must stay arithmetically complete (CI enforces it); tests in both directions are mandatory for any authz change; and an independent adversarial sign-off gates any change to the gate, the registry's scope declarations, or the membership helpers. See `docs/backend_architecture.md` §16.2 and §16.3.
- **Rollback:** `AUTHZ_GATE_ENFORCING = False` in `pixlstash/authz/gate.py` reverts both object-enforcement and unknown-route fail-closed in one line.

## Conventions & Patterns

- **Throughput & batching:** Always think about throughput and concurrency. Evaluate whether a piece of work is best handled as a batch following ML best practices; for images this usually means sorting and grouping by size so each batch is composed of equally-sized tensors (e.g. image and face-crop quality calculation).
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
