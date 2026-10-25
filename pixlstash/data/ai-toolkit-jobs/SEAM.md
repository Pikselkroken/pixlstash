# ai-toolkit integration seam (the firewall contract)

This documents **everything PixlStash's `LocalAiToolkit` adapter relies on** about ai-toolkit, so a
version bump is a single-file change. Verified against the local clone
`~/ai-toolkit` @ commit `9490f03` (v0.10.11). Re-verify on every bump.

> **Maintenance rule:** no ai-toolkit-specific knowledge lives outside the adapter module. If a fact
> below changes, change the adapter, never the data model, API, or UI.

## Launch

```
<ai_toolkit_python> run.py <config.json> --log <training_folder>/<name>/log.txt
  cwd  = <ai_toolkit_repo_path>
  env  = parent env + {
           AITK_JOB_ID        = <our job uuid>        # enables soft UI mode (status/step/cancel)
           CUDA_VISIBLE_DEVICES = <gpu id(s)>
           CUDA_DEVICE_ORDER  = PCI_BUS_ID
           HF_TOKEN           = <secret, only if set> # NEVER logged
         }
```

`run.py` accepts `config… [-r/--recover] [-n/--name] [-l/--log <path>]`; raises (non-zero exit) on
job failure when `--recover` is absent. Spawn in **its own process group** so cancel can signal the
group and a restart can reconcile by PID.

## Config (skeleton: `built-in/zimage-turbo-lora.json`, values: `../training-presets/`)

One `JobConfig` tree (`ui/src/types.ts`). The template is the passthrough skeleton; the recipe in
`pixlstash/data/training-presets/` supplies every value it owns (locked model keys, defaults, the
choices' tables and the resolved memory profile, see `docs/backend_architecture.md` §25.16). The
placeholders are substituted last, recipe prompts included:

| Placeholder | Goes to | Notes |
|---|---|---|
| `{{JOB_NAME}}` | `config.name`, `meta.name` | our job uuid/slug; defines `save_root` |
| `{{TRAINING_FOLDER}}` | `process[0].training_folder` | per-job work dir |
| `{{SQLITE_DB_PATH}}` | `process[0].sqlite_db_path` | our per-job `Job`-table DB (below) |
| `{{TRIGGER_WORD}}` | `process[0].trigger_word` + sample prompts | character/set name |
| `{{DATASET_FOLDER}}` | `process[0].datasets[0].folder_path` | the exported image+`.txt` dataset |

`type: "diffusion_trainer"`, `logging.use_ui_logger: true`. A different base model is a new recipe
with its own locked `model.name_or_path` / `model.arch` / `model.assistant_lora_path` (see
`options.tsx`) and `requires_arch`, the `extensions_built_in/diffusion_models/<dir>` it needs. Send
`arch` literally (e.g. `"zimage:turbo"`); the backend strips the `:turbo` tag. ai-toolkit validates
the config: a bad config fails the run, so the adapter does not re-validate the full tree.

## Outputs (all under `save_root = <training_folder>/<name>`)

- **Checkpoints:** `<save_root>/<name>_<step:09d>.safetensors` every `save.save_every` (250) steps;
  pruned to newest `max_step_saves_to_keep` (4). Final (loop completes): `<save_root>/<name>.safetensors`.
- **Samples:** `<save_root>/samples/[time]__<step:09d>_<n>.<ext>`.
- **Loss DB:** `<save_root>/loss_log.db` (always written when `use_ui_logger=true`).

## Progress / status: TWO independent sqlite files

### 1. `loss_log.db` (`<save_root>/loss_log.db`): step + loss. WAL mode; read WAL-aware.
- `steps(step INTEGER PK, wall_time REAL)` → step progress: `SELECT MAX(step) FROM steps;`
- `metrics(step, key, value_real, value_text, PK(step,key))` → loss curve:
  `SELECT step, value_real FROM metrics WHERE key='loss/loss' ORDER BY step;` (LR key = `learning_rate`)

### 2. `Job` table in `sqlite_db_path`: status/info/speed + cooperative cancel.
`DiffusionTrainer` enables this ("soft UI mode") only when the DB file exists **and** `AITK_JOB_ID`
is set; otherwise it silently skips it (no raise, safe). **PixlStash must create the DB + row before
launch.** Minimal hand-rolled table (only the columns the trainer touches):

```sql
CREATE TABLE Job (
  id              TEXT    NOT NULL PRIMARY KEY,   -- = AITK_JOB_ID
  status          TEXT    NOT NULL DEFAULT 'stopped',  -- running|stopped|error|completed|queued
  info            TEXT    NOT NULL DEFAULT '',    -- human string ("Loading model", "Saving model", …)
  step            INTEGER NOT NULL DEFAULT 0,
  speed_string    TEXT    NOT NULL DEFAULT '',    -- e.g. "2.31 iter/sec" → ETA
  stop            INTEGER NOT NULL DEFAULT 0,     -- set to 1 to request cooperative stop
  return_to_queue INTEGER NOT NULL DEFAULT 0,
  save_now        INTEGER NOT NULL DEFAULT 0
);
INSERT INTO Job (id) VALUES ('<job uuid>');
```
- **Read progress/status:** `SELECT status, info, step, speed_string FROM Job WHERE id=?`.
- Trainer UPDATEs are keyed `WHERE id=?` (no upsert) → silently no-op if the row is absent, so the
  INSERT is mandatory.

## Cancellation

- **Cooperative (preferred):** `UPDATE Job SET stop=1 WHERE id=?`. Polled every step; trainer raises
  cleanly, records final `step`, sets status `stopped`. Does **not** write an extra checkpoint; only
  `save_every` checkpoints survive, so keep `save_every` modest.
- **Hard fallback / shutdown:** SIGTERM→SIGKILL the process group. Loses progress since the last
  `save_every` checkpoint.

## Restart reconciliation

A PixlStash row left `running` after a restart: check PID liveness; if dead, read `Job.status` (if it
reached `completed`/`error`) else mark `error`/`interrupted`. Collect any checkpoints already in
`save_root`.

## Known facts to re-verify on bump
- `loss/loss` metric key; `steps`/`metrics` schema in `toolkit/logging_aitk.py`.
- The `Job` columns `DiffusionTrainer`/`UITrainer` read/write (`extensions_built_in/sd_trainer/`).
- Z-Image-Turbo overrides + required `assistant_lora_path` (`ui/src/app/jobs/new/options.ts`).
- `save_root = training_folder/name` and checkpoint/sample naming (`jobs/process/BaseSDTrainProcess.py`).
