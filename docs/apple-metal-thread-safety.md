# Apple Metal: torch crashes when two threads use it at once

**Summary.** PyTorch's MPS (Metal) backend is not safe to use from several threads
at once. When it happens, the process dies with no Python traceback, trips a
Metal assertion, or hangs. The triggers you are most likely to hit:

- loading a model onto `mps` with Hugging Face transformers, which copies
  weights on four threads;
- two models running their first passes on different threads;
- one thread calling `torch.mps.empty_cache()` while another runs a model.

Two rules avoid it:

- Load models on one thread: set `HF_DEACTIVATE_ASYNC_LOAD=1` before
  transformers loads anything, or load on the CPU and move the model to Metal.
- Never run Metal work on two threads at the same time.

Every combination tested fails the same way, including torch 2.14.0 with
transformers 5.17.0, the newest releases as of 2026-09-13.

## Symptoms

- The process exits with `SIGSEGV`, or occasionally `SIGBUS` or `SIGTRAP`.
  There is no Python exception, so no `try`/`except` sees it.
  `PYTHONFAULTHANDLER=1` prints the Python frame for a `SIGSEGV`.
- The heap is corrupted: libmalloc stops the process with `SIGTRAP` and
  `BUG IN LIBMALLOC: asking for start of chunk with invalid kind`.
- A Metal assertion aborts the process (`SIGABRT`), with one of:
  - `failed assertion _status < MTLCommandBufferStatusCommitted at line 323 in -[IOGPUMetalCommandBuffer setCurrentCommandEncoder:]`
  - `-[IOGPUMetalCommandBuffer validate]:214: failed assertion 'commit an already committed command buffer'`
  - `-[IOGPUMetalCommandBuffer validate]:215: failed assertion 'commit command buffer with uncommitted encoder'`
  - `-[_MTLCommandBuffer commit]:691: failed assertion 'commit command buffer with uncommitted encoder'`
- The Objective-C runtime aborts with `Cannot form weak reference to instance
  (0x…) of class MPSGraph. It is possible that this object was over-released`.
  This happens when one thread flushes the cache while another runs a model;
  see path 3.
- A model load hangs forever at `Loading weights 0/N`, often with threads
  spinning at full CPU.
- It is intermittent. Loading with a single loader thread avoided it in every
  run.

## Cause

Apple's rules: a Metal command queue may be shared across threads, but a
command buffer or encoder may only be used by one thread at a time
([Metal Programming Guide](https://developer.apple.com/library/archive/documentation/Miscellaneous/Conceptual/MetalProgrammingGuide/Cmd-Submiss/Cmd-Submiss.html)).
torch serialises most Metal work onto one dispatch queue per device. Two paths
skip it, in both the v2.13.0 and v2.14.0 source. A third was found by
measurement.

### 1. The kernel-name set (dtype casts, other unary ops)

- `MetalShaderLibrary::hasFunction()` (`aten/src/ATen/native/mps/OperationUtils.mm`)
  fills a `std::unordered_set<std::string> functionNames` on first use. Its only
  guard is a plain `bool functionNamesPopulated`.
- `exec_unary_kernel` calls it *before* entering the stream's queue. Two
  threads that reach it together insert into the set at the same time and
  corrupt it.
- Native samples of hung processes show the loader threads in
  `copy_cast_kernel_mps → MetalShaderLibrary::exec_unary_kernel →
  std::__hash_table<std::string>::__emplace_unique_key_args`, running rather
  than blocked. Most crash reports end in the same frame: 27 of the 28 local
  `SIGSEGV`, `SIGBUS` and `SIGTRAP` reports crashed in
  `__emplace_unique_key_args`, 24 of them under `exec_unary_kernel`, and one
  of the rest in the dispatcher's operator table (a `c10::OperatorName`
  hash). The 28th, a `cast` crash, faulted in Metal's encoder code (path 2
  below).
- `exec_unary_kernel` also calls `getPipelineStateForFunc` before entering the
  queue, and that writes two more unlocked caches, `cplMap` and `libMap`. The
  file's `kernelCache` has no lock either.

How it got into 2.13.0 (neither change is in 2.12.x; 2.12 was not measured):

- `hasFunction()` arrived in [pytorch#184743](https://github.com/pytorch/pytorch/pull/184743).
- Dtype casts were routed through `exec_unary_kernel` in
  [pytorch#184740](https://github.com/pytorch/pytorch/pull/184740).

### 2. `torch.mps.synchronize()`

`MPSHooks::deviceSynchronize` commits the default stream's shared command
buffer without entering the queue. Plain host-to-device copies from four
threads were clean 40 times out of 40. Adding a `torch.mps.synchronize()` per
thread made them fail about half the time.

This path does not only assert. In one `cast` crash report the process died
with `SIGSEGV` in `MPSStream::commandEncoder()`, inside Apple's
`AGXG13XFamilyCommandBuffer` encoder code, while another thread was in
`torch.mps.synchronize()`.

### 3. `torch.mps.empty_cache()` while another thread runs a model

One thread ran warm SBERT query encodes. Another ran CLIP image batches and
called `torch.mps.empty_cache()` after each one. The process aborted within
2–6 s, after 32–93 flushes, with the `MPSGraph` weak-reference message above.
At the crash, the flushing thread was in `empty_cache()` and the other was
inside `F.embedding`. The torch source for this path has not been audited; the
evidence is the measurement alone.

### Why transformers hits it

- **The thread pool.** transformers 5.x loads weights on a thread pool of
  `min(4, cpu_count)` workers (`core_model_loading.py`).
- **Tensors land on Metal first.** When `device_map` names `mps`, it opens the
  safetensors file directly on Metal (`modeling_utils.py`, `backend="pread"`),
  and each worker calls `tensor.to(device, dtype)`.
- **A dtype mismatch means concurrent casts.** If the requested `dtype` differs
  from the checkpoint's, the workers cast on Metal at the same time. For
  example, a bf16 checkpoint loaded with `dtype=torch.float32` does this.
- **When the pool is skipped:** `HF_DEACTIVATE_ASYNC_LOAD=1` is set, weights are
  offloaded to disk, or a quantiser quantises the model on the fly
  (bitsandbytes NF4/INT8, for one). A checkpoint that is already quantised
  still loads on the pool.
- **5.17.0 changes nothing here** compared with 5.16.1.

Not every hang during a threaded load is this race. One sampled hang had no
Metal frames at all: a safetensors slice read was waiting on the Python GIL
inside a one-time initialiser. Single-threaded loads never hung.

## Reproduce

No download needed. Run every attempt in a fresh process.

`repro_threads.py`, torch only:

```python
import sys, threading
import torch

mode = sys.argv[1]  # cast | cast-nosync | cast-warm | copy-sync | copy
tensors = [torch.randn(256, 256, dtype=torch.float16) for _ in range(64)]
if mode.startswith("cast"):
    tensors = [t.to("mps") for t in tensors]
    if mode == "cast-warm":
        tensors[0].to(torch.float32)
        torch.mps.synchronize()

def work(chunk):
    for t in chunk:
        t.to(torch.float32) if mode.startswith("cast") else t.to("mps")
    if mode not in ("copy", "cast-nosync"):
        torch.mps.synchronize()

threads = [threading.Thread(target=work, args=(tensors[i::4],)) for i in range(4)]
[t.start() for t in threads]
[t.join() for t in threads]
torch.mps.synchronize()
print("ok")
```

`repro_hf_load.py`, transformers:

```python
import sys
import torch
from transformers import LlamaConfig, LlamaForCausalLM

mode, path = sys.argv[1], sys.argv[2]
if mode == "make":  # a random ~220M-parameter bf16 checkpoint, 111 tensors
    config = LlamaConfig(hidden_size=1024, intermediate_size=2816,
                         num_hidden_layers=12, num_attention_heads=16, vocab_size=32000)
    LlamaForCausalLM(config).to(torch.bfloat16).save_pretrained(path)
    raise SystemExit
if mode == "mps-cast":        # casts on Metal, on the loader's threads
    model = LlamaForCausalLM.from_pretrained(path, dtype=torch.float32, device_map="mps")
elif mode == "mps-nocast":    # checkpoint dtype, no cast
    model = LlamaForCausalLM.from_pretrained(path, dtype=torch.bfloat16, device_map="mps")
elif mode == "cpu-then-move": # load on the CPU, one move to Metal
    model = LlamaForCausalLM.from_pretrained(path, dtype=torch.float32, device_map="cpu")
    model.to("mps")
torch.mps.synchronize()
print("ok")
```

```sh
python repro_hf_load.py make ckpt
for i in $(seq 10); do timeout 60 python repro_hf_load.py mps-cast ckpt; echo "exit $?"; done
for i in $(seq 10); do HF_DEACTIVATE_ASYNC_LOAD=1 timeout 60 python repro_hf_load.py mps-cast ckpt; echo "exit $?"; done
```

## What reproduces where

Failed runs (crash or 60 s hang) out of 10 fresh processes. Measured on an M1
Pro, macOS 26.6.2, Python 3.12, 2026-09-13.

| Mode | torch 2.13.0, transformers 5.16.1 | 2.13.0, 5.17.0 | 2.14.0, 5.16.1 | 2.14.0, 5.17.0 |
|---|---|---|---|---|
| threads `cast` | 10 | 10 | 10 | 10 |
| threads `cast-warm` | 10 | 9 | 10 | 10 |
| threads `copy-sync` | 4 | 5 | 4 | 6 |
| threads `copy` | 0 | 0 | 0 | 0 |
| transformers `mps-cast` | 4 | 6 | 4 | 6 |
| transformers `mps-cast`, second run | – | 3 | 7 | 7 |
| transformers `mps-nocast` | 0 | 0 | 0 | 0 |
| transformers `cpu-then-move` | 0 | 0 | 0 | 0 |
| transformers `mps-cast` with `HF_DEACTIVATE_ASYNC_LOAD=1` | 0 | 0 | 0 | 0 |

What each thread mode does:

- `cast`: four threads cast float16 tensors that are already on Metal, then
  each calls `torch.mps.synchronize()`, so a failure can come from path 1 or 2.
- `cast-nosync`: the same casts with no per-thread synchronize, which leaves
  path 2 out. It failed 10 of 10 on torch 2.13.0 (segfaults and hangs); the
  other versions were not run.
- `cast-warm`: the same, after one cast on the main thread. Its failures were
  almost all the `setCurrentCommandEncoder` assertion: a cast encoding while
  another thread's `synchronize()` commits the buffer. That is path 2. Whether
  a warm-up alone protects against path 1 was not measured.
- `copy-sync`: four threads copy CPU tensors to Metal, then each synchronizes.
- `copy`: the same copies, with no synchronize.

The transformers `mps-cast` failures involve no `synchronize()` on the loader
threads, which makes them the cleanest evidence for path 1. The four columns
ran at the same time on the same GPU.

## What to do

- **Loading with transformers:** set `HF_DEACTIVATE_ASYNC_LOAD=1` before the
  first load, so weights load on the calling thread (0 failures in 40 loads
  across four torch/transformers pairs). It is read on every load and applies
  to the whole process. Two alternatives:
  - Load on the CPU and call `model.to("mps")`: the move is one serial copy.
    The CPU copy lives in the same memory the GPU uses on Apple Silicon, so
    choose the dtype you will run in before the move.
  - Ask for the checkpoint's own dtype, so nothing is cast during the load.
    The loader threads still run, so this holds only while nothing else they
    do touches Metal.
- **Inference:** let only one thread at a time touch Metal. Either run every
  model on one worker thread, or take one process-wide lock around forward
  passes, `.to("mps")`, `torch.mps.synchronize()` and `torch.mps.empty_cache()`.
- **Error handling cannot help.** These failures kill or hang the process
  before Python sees anything, so no retry or CPU fallback reaches them. Keep
  the threads apart instead.

## What PixlStash does

Two layers, one for threads PixlStash does not own and one for every thread it
does.

- **transformers' loader threads.** `InferenceEngine.create` calls
  `configure_metal_model_loading()` (`utils/device_utils.py`) before any
  service exists. Whenever Metal is present it sets
  `HF_DEACTIVATE_ASYNC_LOAD=1`, so transformers loads weights on the calling
  thread instead of its four-thread pool. A value the owner already set is
  kept. `pixlstash-cli plugins test --image` calls it too, before the plugin's
  `setup()` and `init()`. Florence-2 loads straight onto Metal in fp16, the
  dtype its checkpoint is stored in, so that load has nothing to cast.
- **PixlStash's own threads.** The task runner's single GPU worker is the only
  thread that uses Metal. Every GPU-queue task already runs there. Other work
  that is not a task reaches the worker through
  **`Vault.run_inference(fn, *args, **kwargs)`**:
  - On Metal, the call runs on the GPU worker through
    `TaskRunner.run_on_gpu_worker`, as an `URGENT` `GpuCallTask`, and the caller
    waits for it. From the GPU worker itself it runs inline. A vault whose
    engine build failed has no device to read, so where Metal is present its
    calls go to the worker as well: an image plugin picks its own device, and
    would pick Metal.
  - On CUDA and the CPU it runs inline, unchanged. So does a Vault built with
    `disable_background_workers`: it has no task runner, no worker thread to
    race, and no engine unless a caller builds one.
  - A runner that is stopped, was never started, or whose GPU worker has died
    raises `TaskRunnerNotRunningError` (a `RuntimeError`). Nothing falls back
    to running on the calling thread. A caller already waiting stops waiting,
    within `TaskRunner.GPU_CALL_LIVENESS_POLL_S`, when the worker dies.
  - Whatever the call raises reaches the caller unwrapped, `SystemExit` and
    `KeyboardInterrupt` included, and the worker keeps running: a plugin's
    `sys.exit()` would otherwise end the one thread GPU work runs on.
  - `run_inference` waits `TaskRunner.GPU_CALL_TIMEOUT_S` (60 s).
    **`Vault.run_long_inference`** routes the same way with no timeout, for a
    run the user started and watches progress for. It does not retry a GPU
    out-of-memory error (`retry_vram_oom=False`): the retry would run the call
    again from the start and report its progress twice.
  - The runner does not flush the device cache or run `gc.collect()` after a
    call task (`GpuCallTask.FLUSH_DEVICE_CACHE_AFTER_RUN = False`), so a short
    call does not throw away the buffers the next one reuses. After an
    ordinary GPU task the runner collects garbage, then flushes: a tensor held
    in a reference cycle is freed only by the collection.
  - What a call leaves behind is still freed on the worker, before the caller
    wakes: a call that raises clears the frames its error holds and collects
    garbage, and `run_long_inference` collects after the run. Dropped on the
    caller's thread, the error's frames or a model in a reference cycle would
    free their Metal tensors there.

What goes through it:

| Path | Thread it starts on | On Metal |
|---|---|---|
| Anomaly region (`GET /pictures/{id}/anomaly_region`): on-demand tagger load and Grad-CAM pass | request thread | one `run_inference` call for both, so an idle unload queued on the worker cannot land between them |
| Image plugin runs (`POST /pictures/plugins/{name}`): `ImagePlugin.run` and `run_video` | an `asyncio.to_thread` worker | `run_long_inference`. The plugin API does not say whether a plugin uses the GPU, so every plugin goes, a PIL filter included: on a Mac it waits behind the GPU task already running, and an anomaly region queued behind a long plugin run can get a 503. Progress and error callbacks run on the GPU worker and publish through `Vault.notify`, which any thread may call. A plugin that raises `SystemExit` or `KeyboardInterrupt` fails its run with a 500, on every platform: re-raised on the event loop, asyncio would stop the server |
| Idle unload (`Vault._maybe_aggressive_unload`, from the worker-progress poll and the keep-models-in-memory setting) | request thread | queued as a `GpuCallTask` without waiting, so the poll returns at once; its busy checks are unchanged. Off Metal it runs on the poll thread, where an unload flushes Metal's cache only for a service on Metal, so a Mac forced onto the CPU flushes nothing there |
| `TagTask` model preload at queue time | a `TagModelPreload` thread started by the planner's submit | skipped when the engine is on Metal; the worker loads the model when the task runs |
| InsightFace release (`FaceExtractionTask.release_detection_models`), also run by the face finder's drain on the planner thread | planner thread | flushes the CUDA cache only. InsightFace runs on ONNX Runtime's CPU provider on a Mac, so a Metal flush freed nothing it held |

The CPU-queue flush in `TaskRunner._run` (`elif vram_reserved_mb > 0`) cannot
reach Metal. `_wait_for_vram_budget` reserves nothing for a task whose
`estimated_vram_mb()` is 0, and every task that overrides it is a GPU-queue
task.

**A guard turns a missed path into an exception.** `TaskRunner.start()`
registers its GPU worker as the Metal thread (`register_metal_thread`).
`ensure_metal_thread(device)` raises `RuntimeError` naming the calling thread
and pointing at `Vault.run_inference` when the device is `mps`, a worker is
registered, and the caller is not it. It is silent on CUDA, the CPU, and with
no running runner. A registered worker that has died (an exception the runner's
loop does not catch, raised outside `BaseTask.run`, which records a task's
`SystemExit` or `KeyboardInterrupt` as its failure) still refuses every other
thread, with a message saying it is no longer running: its runner has not
stopped, and letting request threads in would put them on Metal together. It is
called at the entry points code outside a task reaches, not in inner loops:

- `SBertService.encode`
- `ClipService.encode_text`, `encode_image_batch`, `encode_image_crops`
- `ModelLifecycleManager.aggressive_unload` and `safe_idle_unload`, before
  anything is unloaded or flushed
- `PixlStashTaggerService.localize_anomaly`

**The registration outlives a worker that will not stop.** `stop()` clears it
only once the worker has exited, and a worker leaving its loop clears it
itself, so one still running a task after the runner's
`STOP_JOIN_TIMEOUT_S` (60 s) join stays the Metal thread:

- `Vault.stop()` does not unload or close models while such a worker runs on
  Metal. It logs a warning naming the worker and keeps the engine referenced,
  with the worker, in `Vault._engines_left_loaded`: dropping the last
  reference frees its tensors on the stopping thread, which is Metal work on a
  second thread too. The database still closes, without the `gc.collect()` its
  close otherwise runs: that would free the worker's garbage, Metal tensors
  included, on the stopping thread. The next `Vault.start()`
  queues the close of each such engine whose worker has exited onto its own
  GPU worker, and the entry is dropped once the close has run there; a close
  cancelled before it ran leaves the engine held for a later start.
- `Vault.start()` waits up to `PREVIOUS_METAL_WORKER_WAIT_S` (60 s) for that
  worker to exit before starting its own, then refuses with `RuntimeError`
  (`Vault.wait_for_previous_metal_worker`). It waits wherever Metal is present,
  whatever its engine: an engine whose build failed can be built later, and a
  plugin run before then picks Metal itself. A library switch waits before it
  builds the new engine too, since building it reads Metal's working set. A
  bounded wait rather than an immediate refusal: the worker is usually
  finishing a batch or a load that ignored the cancel, and a refused library
  switch would recover by starting the previous library's vault, into the same
  worker.
- A failed `Vault.start()` stops what it had started and closes the vault,
  including its database, before re-raising.

**A busy GPU or a missing worker is a 503, not a 500.** A routed call waits up
to 60 s behind the task already on the worker. The anomaly region turns a
`TimeoutError` into HTTP 503 saying the GPU is busy, and a `TaskCancelledError`
or `TaskRunnerNotRunningError` into 503 saying the GPU worker is not running.
`POST /pictures/plugins/{name}` answers the same 503 for those two. The
not-running 503 asks for a restart if it persists rather than a retry: a dead
worker does not come back. A `RuntimeError` from the model or the plugin
itself keeps the route's own answer. Neither retries. How long a routed call
waits is the rest of the task on the worker.

Not covered:

- WD14 runs on ONNX Runtime with CoreML, not torch's Metal backend. It runs
  inside GPU tasks anyway.
- A GPU worker that outlives its runner's stop leaves its models loaded until
  a later vault starts after that worker has exited (above), or the process
  exits.
- The runner does not flush the device cache after an image plugin run, which
  is a call task; the next ordinary GPU task's flush releases what it cached.
- Python's automatic garbage collection can run on any thread. Ordinary GPU
  tasks, failed calls and plugin runs collect on the worker as they finish,
  and a failed call's frames are cleared there, so a free off the worker needs
  garbage the worker made and has not collected yet: a short call that
  succeeded, or a task still running. Measured without those collections, 19
  of 19 failed calls freed their Metal tensors on request threads; none
  crashed.

### Measured with PixlStash's own services

Without the routing above, several paths used Metal outside the GPU worker
while that worker ran models and flushed the cache after every GPU task. They
were reproduced with PixlStash's own SBERT (`all-MiniLM-L6-v2`), CLIP
(`ViT-B-32`) and Florence-2 base services on two threads, not through the
running server. Ten fresh processes per row:

| Situation | torch 2.13.0 | torch 2.14.0 | Same work on one thread |
|---|---|---|---|
| First search while the worker runs CLIP's first pass (cold kernels) | 8 failed | 10 failed | 0 failed |
| Warm search while the worker flushes the cache after each batch | 10 failed, then 9 on a repeat | 3 failed | 0 failed |
| Warm search while the worker runs CLIP, no flush | 0 failed | – | – |
| Two warm searches at once | 0 failed | – | – |
| Warm search while the worker loads, captions with and unloads Florence-2 | 0 failed | – | – |

The clean rows are clean in ten runs, not proven safe. The Florence-2 row
flushed only three times per run, below the 32–93 flushes the flush row
needed to fail, and it never tested a *first* search during a load.

## Upstream status (checked 2026-09-13)

- [pytorch#167541](https://github.com/pytorch/pytorch/pull/167541) adds mutexes
  to `MetalShaderLibrary`.
  - Open. Labelled Stale on 2026-01-25 and `no-stale` on 2026-01-26, its last
    activity.
  - It predates `hasFunction()` and does not guard `functionNames`, so as
    written it would not fix the cast crash above.
- [pytorch#100285](https://github.com/pytorch/pytorch/issues/100285) is
  `torch.nonzero` crashing on several threads.
  [#108996](https://github.com/pytorch/pytorch/pull/108996) fixed it in 2023 by
  serialising `synchronize()` at that call site only.
- The torch 2.14.0 release notes have nothing on MPS thread safety.
- [transformers#48029](https://github.com/huggingface/transformers/issues/48029)
  is this load crash. **Open.**
  - A maintainer could not reproduce it. Two other reporters confirm it, one
    on torch 2.13.0 with safetensors 0.8.0.
  - The latest comment (2026-08-29) proposes a process-wide lock around
    `tensor.to(...)` for MPS destinations.
  - [#48196](https://github.com/huggingface/transformers/pull/48196)
    (disable the pool on MPS) and
    [#48410](https://github.com/huggingface/transformers/pull/48410) (a lock
    around MPS weight materialisation) were both closed unmerged.
- The PyTorch MPS documentation does not mention threads.

When a fix lands in either project, rerun both scripts on the new release
before relaxing the rules above.
