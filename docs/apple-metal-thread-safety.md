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

Two threads reach Metal in the product, and each is closed at its source rather
than synchronised. Nothing here is a retry or a fallback, because neither can
work: these failures kill or hang the process before Python sees anything.

### Loading: one loader thread wherever Metal exists

`accelerator.configure_metal_model_loading()` sets `HF_DEACTIVATE_ASYNC_LOAD=1`,
which is what turns transformers' loader pool off (`core_model_loading.py`,
`is_env_variable_true(...)` → `thread_pool = None`). It is called from
`InferenceEngine.create` before any service is built, and from `plugin_check`
before a plugin's `init()`, because a plugin that loads a model at init would
otherwise take the command down the way it would the server.

Set whenever Metal is **present**, not only when it is the inference device:
accelerate's `device_map="auto"` places weights on Metal whenever the host
offers it, whatever PixlStash chose for itself. transformers reads the variable
on every load, so setting it before the first one is enough — import order does
not matter. A value already in the environment is the owner's and is kept, with
a warning when transformers would read it as false.

### Searching: the query encoders are held a second time, on the CPU

A query is encoded on the thread handling the request — text search inside the
database task, likeness search inline in its async handler — while the GPU
worker runs the embedding and tagging batches. So on Metal, and only on Metal,
`InferenceEngine.create` also builds `inference/cpu_query_encoders.py`: the same
classes, weights and preprocessing on the `cpu` device.

`TextEmbeddingWorkflow.encode_query` / `encode_clip_query` and
`ClipEmbeddingWorkflow.encode_query_image` route to them.
`engine.query_encoders is None` on every other host, which is what those three
branch on. The worker's own `encode` / `encode_images` deliberately do **not**
route: moving those to the CPU would take the whole library's indexing off the
GPU, which is its own regression.

`create` builds them but does not load them: every engine service is lazy, so
it reads no weights and returns in milliseconds, and loading these two inline
made it take 7.3 s — they were then the first models in the process and paid
the whole cold-import cost. Measured on a real library, boot went 1.95 s to
9.11 s, which is why the weights load on a `CpuQueryEncoderLoadTask` instead.

That task is `URGENT` and runs on the **GPU** queue although it loads onto the
CPU. That looks wrong and is the point: the queue is what serialises it against
the worker's own model loads, and loading a model on a request thread beside
one of those races transformers' and accelerate's *imports* rather than Metal —
it failed with `ImportError: cannot import name 'AcceleratorState' from
partially initialized module 'accelerate.state'`.

`Vault` queues it from **both** `ensure_ready` and `start`, because neither is
reliably the later one: at boot `Server.__init__` calls `start()` before `app`
builds the engine, and on a library switch `_bring_up` calls `ensure_ready()`
before the new runner starts. Whichever runs second queues it, idempotently. In
`start` it goes in *before* the work planner: `URGENT` heads the queue but
cannot preempt a running task, and one planner-queued batch held the load for
over 86 s — long enough for a search to time out.

A search that arrives while the load is still running waits (60 s) and then
answers 503; it never falls back to the Metal services, because with the worker
running that fallback is the crash.

**With no GPU worker at all it does fall back, and that is correct.** The crash
needs two threads on Metal; a task runner that is not running has no worker
doing Metal work, so nothing is there to collide with. `ensure_serving` returns
`False` in that case — permission, not failure. Refusing instead broke search
in every configuration with an engine but no running worker (the e2e backend, a
runner stopped for a library switch, the authz suites).

The pair serves only when **both** copies loaded — the services call
`ensure_ready()` outside their own `try`, so a half-loaded pair would raise out
of every search instead.

`engine.close()` and `engine.safe_idle_unload()` release the copies, *before*
handing the device services to `ModelLifecycleManager`, so the
`trim_process_memory()` that ends that call returns their memory too. Including
them in the **idle** sweep is deliberate: it runs only when the owner chose
memory over speed, and on unified memory these sit in the very pool it is
freeing. `unload()` clears the loaded flag, so the next search queues a reload
and waits rather than encoding against models that are no longer there.

### Not covered

Image plugins run on `asyncio.to_thread` (`services/plugin_service.py`), so a
torch plugin that uses Metal is still a second thread on it. Closing that needs
the plugin run routed onto the GPU worker, which this does not do.

### Measured

| Measure | Result |
|---|---|
| A transformers load on Metal, unguarded | 10 of 10 processes failed (6 hangs, 3 SIGSEGV, 1 SIGBUS) |
| The same load with `HF_DEACTIVATE_ASYNC_LOAD=1` | 0 of 10 failed |
| A real server driven through the HTTP routes, ~2 encodes/s | 4 of 4 runs clean |
| The same server driven at ~100 encodes/s, before the CPU copies | 1 of 3 runs died with `NSInvalidArgumentException` raised from inside a `matmul` |
| The same, after | 4 of 4 runs clean, and the Metal services received no query call at all |
| Boot, real app and real library, before the copies existed | 1.95 s (`create` builds the engine in 0.003 s) |
| Boot with the copies loaded inline in `create` | **9.11 s** — they became the first models in the process and paid every import |
| Boot with the load queued onto the GPU worker | 1.87–2.06 s, copies warm at boot + 9–11 s |
| Memory the copies hold, Metal hosts only | ~630 MB, released by the idle sweep |
| Do fp32 query vectors rank differently from the fp16 ones they replace? | No. 20 queries over an 8,625-vector library: top-1 identical 20/20, top-10 identical 20/20, largest rank move 1 place, max per-dim delta 4.91e-04 |
| A search arriving before the queued load finishes | Waits 8.6 s and then answers; the next query takes 0.05 s |
| A library switch | The new vault gets its own copies; its first query waits 4.5 s and then answers |

torch 2.13.0, transformers 5.16.1, macOS 26.6.2, arm64. The crash is a race, so
the rate is load-dependent: a light workload will not show it, which is why the
density is quoted alongside every figure.
