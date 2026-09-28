# Apple Metal: torch crashes when two threads use it at once

**Summary.** PyTorch's MPS (Metal) backend is not safe to use from several threads
at once. When two threads collide, the process dies with no Python traceback,
trips a Metal assertion, or hangs. The measured triggers, each tied to a cause
below:

- loading a model onto `mps` with Hugging Face transformers, whose loader reads
  and converts weights on up to four threads (cause 1): 3–10 of 10 loads failed;
- two threads running their first passes while the kernel caches are cold
  (most likely cause 1): 8 of 10 on torch 2.13.0 and 10 of 10 on 2.14.0, in
  one setup; two models' first loads together were clean 12 times in another;
- one thread calling `torch.mps.empty_cache()` while another runs a model
  (cause 3): 3–10 of 10 in one setup, 0 of 6 in another;
- several threads calling `torch.mps.synchronize()` (cause 2): about half of
  runs;
- several threads running inference at high density: six threads at about 100
  encodes a second crashed 1 run in 3. Which cause this is was not established.

Every figure has its date and conditions, under "What reproduces where" or
"Measured in PixlStash". Rates vary between days and setups, and some pairings
that look alike ran clean.

Two rules avoid it:

- Keep model loading on one thread: set `HF_DEACTIVATE_ASYNC_LOAD=1` before
  transformers loads anything, or load on the CPU and move the model to Metal.
- Never run Metal work on two threads at the same time.

Every combination tested failed, including torch 2.14.0 with transformers
5.17.0, the newest releases as of 2026-09-13. Upstream `main` has since fixed
cause 1 and most of cause 2, but no release contains the fixes, two cause-2
sites remain, and cause 3 is not fixed (see "Upstream status").

## Symptoms

- The process exits with `SIGSEGV`, or occasionally `SIGBUS` or `SIGTRAP`.
  There is no Python exception, so no `try`/`except` sees it.
  `PYTHONFAULTHANDLER=1` prints the Python frames of every thread when it
  happens.
- The heap is corrupted: libmalloc stops the process with `SIGTRAP` and
  `BUG IN LIBMALLOC: asking for start of chunk with invalid kind`.
- A Metal assertion aborts the process (`SIGABRT`), with one of:
  - `failed assertion _status < MTLCommandBufferStatusCommitted at line 323 in -[IOGPUMetalCommandBuffer setCurrentCommandEncoder:]`
  - `-[IOGPUMetalCommandBuffer validate]:214: failed assertion 'commit an already committed command buffer'`
  - `-[IOGPUMetalCommandBuffer validate]:215: failed assertion 'commit command buffer with uncommitted encoder'`
  - `-[_MTLCommandBuffer commit]:691: failed assertion 'commit command buffer with uncommitted encoder'`
- The Objective-C runtime aborts with `Cannot form weak reference to instance
  (0x…) of class MPSGraph. It is possible that this object was over-released`,
  when one thread flushes the cache while another runs a model (cause 3).
- An uncaught `NSInvalidArgumentException: attempt to insert nil object`,
  raised from inside a `matmul`, aborts the process. This was the crash seen in
  a running PixlStash server with several threads running inference at once.
- A model load hangs forever at `Loading weights 0/N`, often with threads
  spinning at full CPU.
- It is intermittent. Loading with a single loader thread avoided it in every
  run.

## Cause

Apple's rules: "in general, command queues are thread-safe", but only one CPU
thread may access a command buffer at a time, and only one command encoder at a
time may append commands to it
([Metal Programming Guide](https://developer.apple.com/library/archive/documentation/Miscellaneous/Conceptual/MetalProgrammingGuide/Cmd-Submiss/Cmd-Submiss.html),
an archived page last updated in 2016).

torch sends most Metal work through a serial dispatch queue: one per device in
2.13, and one per stream in 2.14, which adds a pool of 32 streams (a thread uses
the default stream unless it picks another). Three kinds of work bypass that
queue in the v2.13.0 and v2.14.0 source. Causes 1 and 2 were read in the source;
cause 3 was found by measurement, and its likely mechanism read in the source
afterwards.

### Cause 1: unlocked caches, written outside the queue

`MetalShaderLibrary` (`aten/src/ATen/native/mps/OperationUtils.mm`, members
declared in `MetalShaderLibrary.h`) keeps caches with no lock:

- `functionNames`, a `std::unordered_set<std::string>` that `hasFunction()`
  fills on first use, guarded only by a plain `bool functionNamesPopulated`;
- `cplMap`, the compiled pipelines that `getPipelineStateForFunc` writes, and
  the `library` member it sets on first use;
- `libMap`, written by `getLibrary(params)`, and `kernelCache`, reached through
  `getCachedKernelFunctionPtr`.

Many ops look their pipeline up *before* entering the queue: `exec_unary_kernel`
(which from 2.13 also calls `hasFunction` there), the MPS-to-CPU cast
(`exec_unary_kernel_raw`), and `matmul` and `addmm` in `LinearAlgebra.mm`,
among others. They share one bundled library, so its caches are written from
whichever threads run those ops. In 2.14.0, `contiguous_copy_kernel_mps` and
`exec_inner_contiguous_scatter` (`operations/Copy.mm`) also reach `kernelCache`
before the queue. The binary-op helper looks its pipeline up inside the queue,
which does not protect a cache other ops write from outside it, and 2.14's
per-stream queues could not guard a shared cache in any case.

The lookup outside the queue is older than 2.13: v2.12.0's `exec_unary_kernel`
already does it. What 2.13 added put every dtype cast on this path:

- `hasFunction()` and its set arrived in
  [pytorch#184743](https://github.com/pytorch/pytorch/pull/184743).
- Dtype casts moved from MPSGraph onto `exec_unary_kernel`, through
  `copy_cast_kernel_mps` (`operations/Copy.mm`), in
  [pytorch#184740](https://github.com/pytorch/pytorch/pull/184740).

Neither change is in 2.12.x. 2.12 was not measured.

Native samples of hung processes show the loader threads in
`copy_cast_kernel_mps → MetalShaderLibrary::exec_unary_kernel →
std::__hash_table<std::string>::__emplace_unique_key_args`, running rather than
blocked. Of the 28 local `SIGSEGV`, `SIGBUS` and `SIGTRAP` reports collected
with the 2026-09-13 runs, 27 crashed in `__emplace_unique_key_args`: 24 under
`exec_unary_kernel`, one under the dispatcher's operator table (a
`c10::OperatorName` hash), and two whose caller was not recorded. The 28th, a
`cast` crash, faulted in Metal's encoder code (cause 2).

### Cause 2: the stream's command buffer, used outside the queue

- `torch.mps.synchronize()` reaches `MPSHooks::deviceSynchronize`, and from
  there `MPSStream::synchronize`, which ends and releases the stream's shared
  command encoder (`endKernelCoalescing`) and then commits the command buffer,
  without entering the queue. This holds from v2.12 through v2.14.
  `torch.accelerator.synchronize()` does the same through
  `MPSGuardImpl::synchronizeDevice`.
- Reading a tensor back with a dtype change (MPS to CPU, `copy_from_mps_` in
  `operations/Copy.mm`) casts with `exec_unary_kernel_raw` and then commits
  with `stream->synchronize(...)` outside the queue, from 2.13 (2.12 cast
  through a queued MPSGraph).
- `eye` and `renorm` (`Eye.mm`, `RenormKernel.mm`, from v2.12 through v2.14),
  `arange` and `linspace` in 2.14 (`RangeFactories.mm`; 2.13 built them with
  MPSGraph), and in 2.13 the tiled `bmm` used for outputs over 2^32 elements
  (`LinearAlgebra.mm`) take the stream's command encoder before entering the
  queue.

Plain host-to-device copies from four threads were clean 40 times out of 40.
Adding a `torch.mps.synchronize()` per thread made them fail 19 times in 40.
The `setCurrentCommandEncoder` assertion above is a cast encoding while another
thread's `synchronize()` commits the buffer. In one `cast` crash report the
process died with `SIGSEGV` in `MPSStream::commandEncoder()`, inside Apple's
`AGXG13XFamilyCommandBuffer` encoder code, while another thread was in
`torch.mps.synchronize()` — most likely using the encoder that
`endKernelCoalescing` had just released.

### Cause 3: `torch.mps.empty_cache()` frees graphs another thread is using

From 2.13.0, `MPSHooks::emptyCache` also calls `MPSGraphCache::clear()`, which
deletes every cached `MPSGraph`. It does so on the graph cache's own queue, not
the stream's, while another thread may be running an op with one of those
graphs. That fits the `MPSGraph` over-release abort above. In v2.12.0,
`emptyCache()` only emptied the allocator. This mechanism was read in the
source, not confirmed in a debugger.

When it crashed (see "What reproduces where"), the process aborted within
2–6 s, after 32–93 flushes, with the flushing thread in `empty_cache()` and the
other inside `F.embedding`.

### Upstream status (checked 2026-09-28)

- [pytorch#167541](https://github.com/pytorch/pytorch/pull/167541) (landed
  2026-09-21) puts a lock around the `MetalShaderLibrary` caches, including
  `functionNamesPopulated`;
  [pytorch#197837](https://github.com/pytorch/pytorch/pull/197837) (2026-09-23)
  moved that lock out of the class. This fixes cause 1 as described here. A
  reviewer also read two smaller unguarded spots on `main` (a cached kernel's
  `encoder` member, and two lazily created singletons); they were not
  confirmed.
- [pytorch#197836](https://github.com/pytorch/pytorch/pull/197836) keeps the
  stream's command-buffer state on the serial queue: on `main`,
  `deviceSynchronize`, `eye`, `renorm` and the range ops all enter it. It
  landed on 2026-09-22, was reverted, and relanded on 2026-09-23. Two cause-2
  sites are still outside the queue on `main`: the MPS-to-CPU cast in
  `copy_from_mps_`, and `MPSGuardImpl::synchronizeDevice`
  (`torch.accelerator.synchronize()`).
- Cause 3 is unchanged on `main`.

None of these is in v2.14.0 or v2.14.1-rc1, the newest tag. Until PixlStash
requires a torch that contains them, the rules in "What to do" still apply, and
cause 3 applies regardless.

### Why transformers hits it

This describes transformers 5.13 and later. Earlier versions, which
`pyproject.toml` still allows (`transformers~=5.12`), read the file on the CPU
and have the workers copy it to Metal instead.

- **The thread pool.** Each load reads and converts weights on a pool of
  `min(4, cpu_count)` workers (`core_model_loading.py`).
- **Tensors land on Metal first.** When any entry of the load's device map is
  `mps`, transformers opens the whole safetensors file directly on Metal
  (`modeling_utils.py`, `backend="pread"`), and each worker reads its tensors
  there and calls `tensor.to(device, dtype)`. With a mixed map, weights bound
  for the CPU are therefore read on Metal and copied back, which with a dtype
  change is cause 2's MPS-to-CPU cast. The map names `mps` when the caller
  passes one that does; when no `device_map` is given but the default torch
  device is `mps` (`torch.set_default_device`, or a `with torch.device()`
  block); when the Metal quantiser picks it; and with `device_map="auto"` on a
  Mac (see "Loading" below). `.bin` checkpoints, `disable_mmap` loads and files
  on an HF mount are read on the CPU.
- **The calling thread works too.** While the workers are still reading, the
  calling thread applies weight conversions (`mapping.convert`) to the tensors
  already read, so even the pool's own load has more than one thread on
  Metal.
- **A dtype mismatch means concurrent casts.** A worker casts whenever a
  tensor's stored dtype differs from its parameter's dtype. That is usually the
  requested `dtype`, but modules a model pins to fp32 stay fp32, so they cast
  under a bf16 or fp16 request. A bf16 checkpoint loaded with
  `dtype=torch.float32` casts every tensor.
- **When the pool is skipped:** `HF_DEACTIVATE_ASYNC_LOAD=1` is set, the device
  map sends weights to `"disk"`, or a quantiser quantises the model on the fly
  (bitsandbytes NF4/INT8, for one). An already-quantised checkpoint still loads
  on the pool. With the pool off, the same reads and casts run on the calling
  thread, one tensor at a time.
- **5.17.0 against 5.16.1:** the pool and its per-tensor work are unchanged.
  GGUF loads on a Mac with no `device_map` now default onto Metal, but a GGUF
  file is read on the CPU, so the workers only copy host tensors to Metal (the
  pattern measured clean) and the dequantising runs on the calling thread. And
  parameters a weight converter forces onto the CPU are moved back to their
  device on the main thread while the workers run.

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

All on one M1 Pro with macOS 26.6.2 and Python 3.12. Each count is failed runs
(a crash, or a hang past the timeout) out of fresh processes.

### The reproduction scripts (2026-09-13)

Ten processes per cell.

| Mode | torch 2.13.0, transformers 5.16.1 | 2.13.0, 5.17.0 | 2.14.0, 5.16.1 | 2.14.0, 5.17.0 |
|---|---|---|---|---|
| threads `cast` | 10 | 10 | 10 | 10 |
| threads `cast-warm` | 10 | 9 | 10 | 10 |
| threads `copy-sync` | 4 | 5 | 4 | 6 |
| threads `copy` | 0 | 0 | 0 | 0 |
| transformers `mps-cast` | 4 | 6 | 4 | 6 |
| transformers `mps-cast`, second batch of ten | – | 3 | 7 | 7 |
| transformers `mps-nocast` | 0 | 0 | 0 | 0 |
| transformers `cpu-then-move` | 0 | 0 | 0 | 0 |
| transformers `mps-cast` with `HF_DEACTIVATE_ASYNC_LOAD=1` | 0 | 0 | 0 | 0 |

The four columns ran at the same time on the same GPU, so a process running
alone may fail at a different rate. The same `mps-cast` load failed 10 of 10
when run on 2026-09-21 (see "Measured in PixlStash").

What each thread mode does:

- `cast`: four threads cast float16 tensors that are already on Metal, then
  each calls `torch.mps.synchronize()`, so a failure can come from cause 1 or 2.
- `cast-nosync`: the same casts with no per-thread synchronize, which leaves
  cause 2 out. It failed 10 of 10 on torch 2.13.0 (segfaults and hangs); the
  other versions were not run.
- `cast-warm`: the same, after one cast on the main thread. Its failures were
  almost all the `setCurrentCommandEncoder` assertion, which is cause 2.
  Whether a warm-up alone protects against cause 1 was not measured.
- `copy-sync`: four threads copy CPU tensors to Metal, then each synchronizes.
- `copy`: the same copies, with no synchronize.

The transformers `mps-cast` failures involve no `synchronize()` on the loader
threads (the script maps every weight to `mps`), which makes them the cleanest
evidence for cause 1.

Re-checked on 2026-09-28 with torch 2.13.0: threads `cast` failed 3 of 3 (exit
codes 134 with the `setCurrentCommandEncoder` assertion, 139 with a crash
report for the `SIGSEGV`, and 124 for a hang killed at the 60 s timeout), and
threads `copy` failed 0 of 3.

### PixlStash's services on two threads, PR #1447's branch (2026-09-14)

PixlStash's own SBERT (`all-MiniLM-L6-v2`), CLIP (`ViT-B-32`) and Florence-2
base services, called on two threads directly rather than through a running
server, on the branch of PR #1447 before its routing. Ten processes per row:

| Situation | torch 2.13.0 | torch 2.14.0 | Same work on one thread |
|---|---|---|---|
| First search while the worker runs CLIP's first pass (cold kernels) | 8 failed | 10 failed | 0 failed |
| Warm search while the worker flushes the cache after each batch | 10 failed, then 9 on a repeat | 3 failed | 0 failed |
| Warm search while the worker runs CLIP, no flush | 0 failed | – | – |
| Two warm searches at once | 0 failed | – | – |
| Warm search while the worker loads, captions with and unloads Florence-2 | 0 failed | – | – |

The clean rows are clean in ten runs, not proven safe. The Florence-2 row
flushed only three times per run, below the 32–93 flushes the flush row needed
to fail, and it never tested a *first* search during a load.

### The same pairings on `develop` (2026-09-21)

Through PixlStash's services on `develop`, torch 2.13.0, three pairings ran
clean: SBERT encodes against CLIP batches with a cache flush after each (6 of
6), two threads both in `encode_image_batch` (6 of 6), and CLIP loading and
unloading on one thread against SBERT encodes on another (5 of 5). Eight SBERT
loads on Metal, on one thread, were also clean. The flush pairing is the one
that failed 19 of 20 times on the #1447 branch; what separates the two setups
was not established.

### The tagger preload (2026-09-27)

`TagTask`'s preload thread (see "Not covered") loaded the PixlStash tagger on
Metal while another thread ran CLIP, using the real services in a harness.
Both threads reached Metal in 65 runs: 48 with the preload overlapping CLIP
inference, 5 overlapping only CLIP's load, and 12 against cache flushes alone.
None failed, and neither did 18 serial controls. Twelve of the 48 started both
models' first loads and CLIP's first batches together, which is close to the
2026-09-14 "first search" row. The flush runs flushed 6 or 16–17 times during
each preload, below the 32–93 flushes the failing flush pairing needed, so they
say little about cause 3. Seven further cold starts hit the `torchvision`
import race described under "Searching" before any Metal work, and are not
counted.

The same overlap in the app itself, driven by a harness: a real `Server` on a
throwaway library of 24 pictures, every finder but face extraction, tagging and
image embedding detached, and `ensure_ready` called as the app does. On
restarts with work already pending, the first preload overlapped a CLIP batch
on the GPU worker in 8 of 8 with the CPU copies on (and in both of two other
runs), and in 3 of 8 with them patched out, with a cache flush inside it in 8
of 8 and 1 of 8. No Metal crash occurred. Three of the 16 restarts, and two of
the runs that only seeded a library, aborted at exit in onnxruntime's telemetry
teardown, which is unrelated.

## What to do

- **Loading with transformers:** set `HF_DEACTIVATE_ASYNC_LOAD=1` before the
  first load, so the loader's reads and casts run on the calling thread (0
  failures in 40 loads across four torch/transformers pairs on 2026-09-13, and
  0 of 10 on 2026-09-21). It is read on every load and applies to the whole
  process. Two alternatives:
  - Load on the CPU and call `model.to("mps")`. The pool still runs, but its
    casts stay on the CPU, and the move is one copy per tensor on the calling
    thread. The CPU copy lives in the same memory the GPU uses on Apple Silicon,
    so choose the dtype you will run in before the move.
  - Ask for the checkpoint's own dtype. This was clean in every run
    (`mps-nocast`, 0 of 40), but the workers still create Metal tensors, and a
    model with modules pinned to fp32 still casts. Rely on it only for
    checkpoints like the one measured.
- **Inference:** let only one thread at a time touch Metal. Either run every
  model on one worker thread, or take one process-wide lock around forward
  passes, `.to("mps")`, reading results back with a dtype change
  (`.to("cpu", dtype)`), `torch.mps.synchronize()`,
  `torch.accelerator.synchronize()` and `torch.mps.empty_cache()`.
- **Error handling cannot help.** These failures kill or hang the process
  before Python sees anything, so no retry or CPU fallback reaches them. Keep
  the threads apart instead.

## What PixlStash does

Terms used below:

- **GPU worker**: the task runner's single thread for GPU-queue tasks.
- **Planner**: the work planner's thread. Its **model finders** queue tagging,
  embedding and face-extraction tasks, but only once the inference engine
  exists.
- **`URGENT`**: the highest task priority. It goes ahead of queued tasks but
  does not interrupt the one running.
- **The CPU copies**: `CpuQueryEncoders`, a second CLIP and SBERT held on the
  CPU for search queries (see "Searching").

Two sources of a second Metal thread are closed at their source rather than
synchronised: transformers' loader pool and search queries. The rest are listed
under "Not covered".

### Loading: one loader thread wherever Metal exists

`accelerator.configure_metal_model_loading()` sets `HF_DEACTIVATE_ASYNC_LOAD=1`,
which turns transformers' loader pool off (`core_model_loading.py`,
`is_env_variable_true(...)` → `thread_pool = None`). It is called from
`InferenceEngine.create` before any service is built, and from `plugin_check`
before a plugin's `setup()` and `init()`, because a plugin that loads a model at
init would otherwise take the command down the way it would the server.

It is set whenever Metal is **present**, not only when it is the inference
device, because several routes put a load on Metal whatever PixlStash chose
(see "Why transformers hits it"). `device_map="auto"` is one: transformers sizes
the map with accelerate's `get_max_memory`, which, unless the caller passes
`max_memory`, lists only `mps` and no `cpu` on a Mac, so every weight goes to
Metal. transformers reads the variable on every load, so setting it before the
first load is enough — import order does not matter. A value already in the
environment is the owner's and is kept, with a warning when transformers would
read it as false.

### Searching: the CPU copies

A query is encoded on a request thread — text search and export by query before
their database task, likeness search on a threadpool worker — while the GPU
worker runs the embedding and tagging batches. So on Metal, and only on Metal,
`InferenceEngine.create` also builds `CpuQueryEncoders`
(`inference/cpu_query_encoders.py`): the same classes, weights and
preprocessing on the `cpu` device.

`TextEmbeddingWorkflow.encode_query` / `encode_clip_query` and
`ClipEmbeddingWorkflow.encode_query_image` route to them.
`engine.query_encoders is None` on every other host, which is what those three
branch on. The worker's own `encode` / `encode_images` deliberately do **not**
route: moving those to the CPU would take the whole library's indexing off the
GPU, which is its own regression.

`create` builds the copies but does not load them. Every engine service is lazy,
so `create` reads no weights and, once the tagger files are downloaded, returns
in milliseconds (on first run it downloads the built-in taggers). Loading these
two inline made it take 7.3 s, because they were then the first models in the
process and paid the whole cold-import cost; on a real library, boot went from
1.95 s to 9.11 s. The weights load on a `CpuQueryEncoderLoadTask` instead.

That task is `URGENT` and runs on the **GPU** queue although it loads onto the
CPU. That looks wrong and is the point: the queue serialises it against the
worker's own model loads. Loading a model on a request thread beside one of
those races transformers' and accelerate's *imports* rather than Metal; it
failed with `ImportError: cannot import name 'AcceleratorState' from partially
initialized module 'accelerate.state'`. The queue does not serialise it against
the tagger, which `TagTask` loads on its own thread (see "Not covered").

`torchvision` has the same import problem. Its package imports itself in a
cycle, so when two threads import it for the first time from different entry
points (`open_clip` for CLIP, `torchvision.models` for the tagger), one raises
`_DeadlockError` and the other gets a half-built `torchvision.ops`. A harness
hit it in 7 of 12 cold starts, and in 0 of 12 once `open_clip` and
`torchvision` had been imported on one thread first (2026-09-27). Neither side
is left broken: each load succeeds when retried.

`Vault._queue_cpu_query_encoder_load` is called from `ensure_ready`, from
`start`, and from the lazy engine build in `get_worker_future`; it is
idempotent. The call in `ensure_ready` is the one that queues the load in both
production sequences. At boot `Server.__init__` calls `start()` before `app`
builds the engine, so `start` finds nothing to load. On a library switch
`_bring_up` calls `ensure_ready()` first, and a runner accepts tasks before it
starts. The call in `start` changes nothing in either sequence and remains as a
safety net.

The load has to be queued before the planner has work to queue: `URGENT` heads
the queue but cannot preempt a running task, and one planner-queued batch held
the load for over 86 s — long enough for a search to time out. On a library
switch the planner has not started yet. At boot it has, but the model finders
queue nothing until the engine exists, and `ensure_ready` queues the load right
after building it. That is an ordering, not a guarantee: the planner can see
the engine a moment before the load is queued, and a tagger preload can start
while the load is still importing. On restarts with work already pending, the
load ran first in 8 of 8, and the first tagger preload began 1.1–1.6 s after it
finished (2026-09-27). Running first also meant the load imported `torchvision`
alone, so the `torchvision` race did not occur in those restarts. With the CPU
copies patched out, as a stand-in for a CUDA or CPU host measured on the same
Mac, it occurred in 1 of 8.

A text or likeness search that arrives while the load is still running waits
for it, up to 60 s, and answers 503 only if it has not finished by then; an
export by query fails its job instead, with the reason on its status. None of
them falls back to the Metal services while a worker runs, because that
fallback is the crash. The wait is also why no encode runs inside a database
task or on the event loop: the load can sit behind a running batch that commits
through the single DB writer, so a wait holding that writer stalls every write
until it times out, and a wait on the event loop stalls every request.

When the runner refuses the load task, `CpuQueryEncoders.ensure_serving`
returns `False` and the caller encodes on the Metal services. The reasoning is
that the crash needs a second thread on Metal, and a runner that refuses tasks
has no worker doing Metal work. Refusing instead broke search in configurations
with an engine but no running worker, which the multi-project authz suite
caught. Searches falling back take turns (`CpuQueryEncoders.device_fallback`),
so two of them are never two threads on Metal. One gap remains, listed under
"Not covered".

The pair serves only when **both** copies loaded — the services call
`ensure_ready()` outside their own `try`, so a half-loaded pair would raise out
of every search instead.

`engine.close()` releases the copies *before* handing the device services to
`ModelLifecycleManager`, so the `gc.collect()` in that call frees them too.
(The `trim_process_memory()` after it only acts on Linux, and the copies exist
only on Macs.) The idle sweep (`Vault._maybe_aggressive_unload`) reaches
them through `engine.close()`, and that is deliberate: it runs only when the
owner chose memory over speed, and on unified memory the copies sit in the very
pool it is freeing. `unload()` clears the loaded flag, so the next search queues
a reload and waits rather than encoding against models that are no longer
there.

`unload()` also waits for encodes already running, because releasing a model
under one makes the service reload it lazily on the search thread, the import
race above. While it waits, new encodes refuse (503) and a load may not flag
the pair, so searches that keep arriving cannot hold the unload open: they
queue a reload that waits for the unload to finish. The wait is bounded
(`UNLOAD_DRAIN_S`, 10 s): an encode still running then is left alone, the
models are kept, and the next sweep tries again. A load that an unload
overtakes, releasing what it had just loaded, loads once more instead of
leaving its waiters to time out.

### Not covered

These do, or may do, Metal work on a thread other than the GPU worker:

- **The tagger preload.** `TagTask.on_queued` runs on whichever thread submits
  the task (the planner, or a request thread for an interactive retag) and
  loads the active tag plugin on its own `TagModelPreload` thread. For the
  built-in tagger that is `load_file` onto Metal, `load_state_dict` back into a
  model built on the CPU, then `.to(mps)` and `.half()` over its 344 weights; a
  third-party tag plugin's `setup()` and `init()` run there too. Measured on
  2026-09-27 (see "What reproduces where"): it overlapped CLIP on the worker in
  8 of 8 restarts with the CPU copies on, and nothing crashed.
- **`GET /pictures/{id}/anomaly_region`.** On the request thread it loads the
  tagger if it is not resident, then runs Grad-CAM, which casts the shared
  model to fp32 and back around a forward and backward pass. Not measured.
  Because the cast is in place on the shared model, a tag batch on the worker
  at that moment would meet fp32 weights, on CUDA as well as Metal (the CPU
  model is fp32 throughout).
- **The idle sweep.** `Vault._maybe_aggressive_unload` runs `engine.close()`,
  which flushes with `torch.mps.empty_cache()` (cause 3). It runs from the
  worker-progress poll, on a request thread, and from `PATCH /users/me/config`
  when "keep models in memory" is switched off, on the event loop, where the
  CPU copies' unload can wait for a running encode (at most `UNLOAD_DRAIN_S`).
  It runs only with that setting off. Its idle check reads the planner's
  progress and whether a GPU task is running at that instant, so a batch the
  worker starts just after the check can meet the flush, and it sees neither
  the tagger preload nor the anomaly route. Nothing stops two polls sweeping at
  once. Not measured.
- **The face finder's end-of-work flush.** With "keep models in memory" off,
  `MissingFaceExtractionFinder.on_all_tasks_complete` runs
  `FaceExtractionTask.release_detection_models()` on the planner thread, which
  ends in `empty_device_cache()`: another cause-3 flush, with no check on the
  GPU worker. Not measured.
- **Stopping the vault.** `TaskRunner.stop()` gives the GPU worker up to 60 s
  to finish, then carries on. `Vault.stop()` then releases the face models and
  runs `engine.close()`, flushing and freeing on the stopping thread, which a
  worker that outlived the wait may still be using. Not measured.
- **Search with no worker.** The fallback above encodes on Metal whenever the
  runner refuses tasks. A stopping runner refuses from the first line of
  `TaskRunner.stop()`, while its worker may still be finishing a batch, and may
  outlive the stop. Only `Vault.stop()` stops the runner (a library switch or
  shutdown), where admission control probably keeps searches out; that was not
  verified.
- **Image plugins.** They run on `asyncio.to_thread`
  (`services/plugin_service.py`), so a torch plugin that uses Metal is a second
  thread on it. No built-in image plugin imports torch.
- **Allocator and device calls.** The worker-progress poll reads
  `torch.mps.driver_allocated_memory()` on a request thread, and
  `torch.mps.set_per_process_memory_fraction` runs when `max_vram_gb` changes
  (from `PATCH /users/me/config`, on the event loop) and when an engine is
  built (on whichever thread builds it). Neither uses a command buffer, and
  both are believed harmless; that was not verified.

Closing any of these means routing the work onto the GPU worker, which this
does not do.

### Measured in PixlStash

| Measure | Date | Result |
|---|---|---|
| `repro_hf_load.py mps-cast`: a transformers load on Metal, unguarded | 2026-09-21 | 10 of 10 processes failed (6 hangs, 3 SIGSEGV, 1 SIGBUS). The 2026-09-13 table has 4 of 10 for the same pair, with four processes running at once |
| The same load with `HF_DEACTIVATE_ASYNC_LOAD=1` | 2026-09-21 | 0 of 10 failed |
| A real server driven through the HTTP routes at ~2 encodes/s, before the CPU copies | 2026-09-21 | 4 of 4 runs clean |
| A real server with six threads calling the encoders directly at ~100 encodes/s while the worker embedded an upload, before the CPU copies | 2026-09-21 | 1 of 3 runs died with `NSInvalidArgumentException` raised from inside a `matmul` |
| The same, with the CPU copies | not recorded in the notes | The Metal services received no query call at all, and 4 of 4 runs were clean. At a 1-in-3 rate, four clean runs would happen by chance about one time in five, so the first half is the evidence |
| Boot, real app and real library, before the CPU copies existed | 2026-09-21 | 1.95 s (`create` builds the engine in 0.003 s) |
| Boot with the copies loaded inline in `create` | 2026-09-21 | **9.11 s** — they became the first models in the process and paid every import; `create` alone took 7.3 s |
| Boot with the load queued onto the GPU worker | 2026-09-23 | 1.87–2.06 s |
| When the queued load finishes, in the harness on throwaway libraries | 2026-09-27 | 6.1–8.8 s after the harness started; the load task itself took 4.9–7.1 s, cold imports included |
| Memory the CPU copies hold, Metal hosts only | 2026-09-28 | About 0.7 GB: 696 MB of fp32 weights by parameter count (CLIP 605 MB, SBERT 91 MB); the process grew by 610 MB loading them. Released by the idle sweep |
| Do fp32 CLIP query vectors rank differently from the fp16 ones they replace? (SBERT was fp32 on Metal already.) | 2026-09-23 | No. 20 queries over a real library of several thousand pictures: top-1 identical 20/20, top-10 identical 20/20, largest rank move 1 place, max per-dim delta 4.91e-04 |
| A search arriving before the queued load finishes | 2026-09-23 | Waits 8.6 s and then answers; the next query takes 0.05 s |
| A library switch | 2026-09-23 | The new vault gets its own copies; its first query waits 4.5 s and then answers |

torch 2.13.0, transformers 5.16.1, macOS 26.6.2, arm64, unless a row says
otherwise. The crash is a race, so its rate depends on load and timing: several
pairings ran clean in one setup and failed in another, which is why each result
carries its date and conditions.
