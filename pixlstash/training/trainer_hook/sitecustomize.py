"""The trainer's CUDA allocator peaks, written for PixlStash to read.

PixlStash polls what a trainer holds every second or more, and a preview's VAE
decode holds gigabytes for less than that before ai-toolkit empties its cache,
so a poll misses the moment a run is most likely to run out of memory. The
trainer is started with this directory on ``PYTHONPATH``, so Python imports
this file at start-up. Once the process has a CUDA context, a thread appends a
line a few times a second to ``<TRAINER_PEAKS_DIR>/<pid>.log``: the most
the allocator reserved since the last line, then what it reserves now, in MB.
The allocator's own peak cannot miss a spike between two lines.

Nothing runs without ``TRAINER_PEAKS_DIR``. A ``sitecustomize`` this one
shadows (the interpreter's own) is imported after it, as it would have been.
"""

import importlib.util
import os
import sys
import threading
import time

_TICK_S = 0.25


def _write_peaks(path: str) -> None:
    while True:
        time.sleep(_TICK_S)
        torch = sys.modules.get("torch")
        try:
            if torch is None or not torch.cuda.is_initialized():
                continue
            peak = torch.cuda.max_memory_reserved()
            torch.cuda.reset_peak_memory_stats()
            now = torch.cuda.memory_reserved()
        except AttributeError:
            continue  # torch is still being imported
        except Exception as exc:  # never take the trainer down over a reading
            print(f"PixlStash stopped reading memory peaks: {exc}", file=sys.stderr)
            return
        try:
            with open(path, "a", encoding="ascii") as out:
                out.write(f"{peak >> 20} {now >> 20}\n")
        except OSError as exc:
            print(f"PixlStash cannot write {path}: {exc}", file=sys.stderr)
            return


def _start() -> None:
    folder = os.environ.get("TRAINER_PEAKS_DIR")
    if folder:
        path = os.path.join(folder, f"{os.getpid()}.log")
        threading.Thread(
            target=_write_peaks, args=(path,), name="pixlstash-peaks", daemon=True
        ).start()
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.curdir) != here]
    del sys.modules[__name__]
    if importlib.util.find_spec("sitecustomize") is not None:
        importlib.import_module("sitecustomize")


_start()
