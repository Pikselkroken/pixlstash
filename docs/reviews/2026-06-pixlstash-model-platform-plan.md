# PixlStash — diffusion dataset & training platform

**Status:** Design note · **Date:** 2026-06-17 · **Owner:** Gaute

PixlStash is being reframed from a tagging/asset tool into the **hub of a professional
diffusion workflow**: curate datasets (tags + captions), train **taggers**, train **LoRAs**,
and manage the resulting models, predictions and stats — all GPU-aware. The tagger
flywheel below is the **first vertical** (and the near-term build); LoRA training is the
headline for diffusion users and the **second client** of the same training machinery.

```
ingest / curate ─→ train tagger ─→ auto-tag better ─→ train LoRA ─→ [generate] ─→ curate ─→ …
(pictures, tags,      (pixl)         (predictions)      (sd-scripts/…)   (?)
 captions, embeds)                                      from captioned set
```
Captions + tags are the shared fuel: they feed both the tagger trainer and LoRA training.

## Status — where we are (2026-06-17)

Planning consolidated into this note. On the **tagger flywheel** vertical: the review queue +
merge-aware scan are committed (`23bdf73`). **This checkpoint** adds the **stats-ingest brick**
(`TaggerRun` + `POST/GET /tagger-runs`, stores rejected runs too — 2 tests green) and the
review-queue refinements (4-corner decision, pair dedupe, tag autocomplete, in-app scan).
**Next up:** build step 1 — per-project config (taxonomy + merges), de-hard-coding
`DEFAULT_TAG_MERGES`. Steps 3 (training-job runner) and 7+ (LoRA, registry, generation) are the
platform expansion and are still design-first.

## What PixlStash owns

- **Dataset** — pictures, **tags**, **captions**, **embeddings**.
- **Predictions** (TagPrediction) + **suggestions** (review queue).
- **Models & LoRAs** — registry + artifact hosting.
- **Training jobs** — managed GPU tasks (see below) + their **stats/reports** (TaggerRun).
- **Per-project config / recipes** (JSON for now).

## Training as a managed GPU task  *(from inspecting `../pixltagger`)*

Trainers run as **supervised subprocesses** owned by PixlStash's task runner — *not*
in-process. Rationale from the code: `pixl train` (`src/pixl/train.py:71`) already wraps a
`subprocess.run` → `legacy/finetune.py` and returns a result dict; the loop isn't a library;
heavy CUDA init; and we need clean cancel/kill + isolated stdout.

- **In the Task Manager** — a long-running job (a ConvNeXt-base run is hours). Resumable:
  leave the overlay and the job keeps running; reattach shows live progress; cancellable.
- **Blocks the GPU exclusively** — training consumes full VRAM (CUDA AMP, single GPU,
  no DDP; `finetune.py:1847`). Concurrent inference/embeddings would OOM, so a training job
  **takes the single GPU slot exclusively** and other GPU tasks queue behind it.
- **Progress** — parse subprocess stdout. PixlTagger emits per-epoch lines
  `Epoch N/M - loss=… val_f1=… (T.Ts)` (`finetune.py:1861`); per-epoch granularity, **no
  callback hook today**. For finer/structured progress (per-step, ETA, per-tag F1, LoRA
  sample images) we add a **JSON-lines progress** output to each trainer and parse that.
- **On completion** — model/LoRA `.safetensors` → registry; `report.json` + per-class
  metrics → TaggerRun/stats.

Key refs: entry `src/pixl/train.py:71` · loop `legacy/finetune.py:1832` · config
`src/pixl/config.py` · arch hard-coded to `convnext_tiny|base` in `build_model`.

## The training-job abstraction (tagger + LoRA share it)

A **TrainingJob** = `{ recipe, command, gpu_exclusive, progress_parser, on_done(artifact, report) }`.
Two first clients:

- **Tagger trainer** (`pixl`, ConvNeXt) — exists; wrap `pixl train` as a job.
- **LoRA trainer** (sd-scripts / diffusers / ai-toolkit — TBD) — new; same shape
  (GPU-exclusive subprocess, stdout/JSONL progress, `.safetensors` artifact). Consumes the
  captioned datasets PixlStash already holds; can stream **sample images** as progress.

This is also what makes the system **trainer-agnostic**: a new architecture or trainer is a
new recipe + command + progress parser, not a new pipeline.

## Config / recipes (JSON for now)

Per-project + per-job recipe, **read by both** PixlStash (scans need taxonomy + merges) and
the trainer. Replaces the hard-coded `DEFAULT_TAG_MERGES`.
- **Tagger recipe:** dataset sets, `arch` (ConvNeXt first), epochs/lr/batch/image_size,
  `tag_remap`, eval/golden set, thresholds.
- **LoRA recipe:** base model (SDXL/Flux/…), caption set, network dim/alpha, lr, steps,
  resolution, sample prompts.
- **Shared:** dataset selection from PixlStash + push target.

## Already built (tagger flywheel — the first vertical)

- Review overlay: 2×2 decision (both / left-only / right-only / neither / swap), per-image
  tagger confidence, click-to-zoom+pan, undo, bulk-resolve with preview, resumable queue.
- Merge-aware near-neighbour scan (CLI + in-app, shared kernel), pair dedupe, tag autocomplete.
- Stats ingest: `TaggerRun` + `POST/GET /tagger-runs` (stores every run incl. rejected).

## Review signals (the human loop)

- **Near-neighbour disagreement** — model-independent cold-start; near-identical images that
  disagree on a tag. The 2×2 card. *Finite* (bootstrap).
- **Version-disagreement** — flag where a retrained model's prediction **changed**, especially
  away from the current label. *Compounds* every round (the iteration engine).

## Build sequence

*Wedge first (tagger flywheel), then platform expansion.*
1. **Per-project config** (taxonomy + merges) — migrate `DEFAULT_TAG_MERGES`. *(next brick)*
2. **`pixl` push** — report + predictions to PixlStash after each eval.
3. **Training-job runner** — wrap `pixl train` as a GPU-exclusive task-manager subprocess job:
   stdout progress, resumable/cancellable, artifact + report on done. *(the explicit ask)*
4. **Review tabs + model-scan** — *Compare look-alikes* / *Tagger changes*.
5. **Streaming predictions task** — GPU job that refreshes predictions, captures old→new
   changes, streams flagged items into the Tagger-changes tab.
6. **Stats panel** — per-tag P/R/F1 + trend from ingested runs.
7. **LoRA trainer** — second training-job client (captioned set → LoRA → registry).
8. **Model & LoRA registry + artifact hosting.**
9. **(scope) Generation / diffusion inference** — if PixlStash hosts generation too.

## Heavy / design-before-build

- **Artifact hosting + registry** — checkpoints *and* LoRAs (size, location, retention,
  rejected runs too), metadata, runtime mapping. The riskiest piece, bigger now.
- **GPU scheduler** — one exclusive slot; training blocks inference/embeddings; queueing +
  visibility in the task manager.
- **Structured progress protocol** — JSON-lines from each trainer (small change per trainer).
- **Bundling trainers** — `pixl` + a LoRA backend shipped with PixlStash; heavy torch/CUDA
  deps and version pinning.
- **Stateless trainers** — dataset already lives in PixlStash; outputs (weights, run registry)
  move there. Dual-write (local + push) until the hub is proven, then drop local storage.
- **Promotion / selection** — pick the active model/LoRA → refresh predictions.

## Open questions

- Does "full diffusion pipeline" include image **generation** inside PixlStash, or is PixlStash
  the dataset+training hub feeding an external generator? (Scope boundary for item 9.)
- Which **LoRA backend** (sd-scripts/kohya, diffusers, ai-toolkit) and base-model targets
  (SDXL / Flux)?
- Artifact storage location + retention for (potentially large) LoRAs + checkpoints.
- How trainers are bundled/packaged with their GPU deps.
- Config/recipe schema across trainer types.
