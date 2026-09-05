"""Generate the model-shelf fixture tree on demand.

The shelf (v1.10) has to look right against 1,800 adapters, a real training run
and a folder that is not there. The real thing is hundreds of gigabytes, so it is
generated rather than committed, and three things keep that honest:

* **Real headers.** Every ``.safetensors`` carries a genuine length prefix and
  header JSON, with tensor names, shapes and dtypes taken from real files, so
  :mod:`pixlstash.utils.adapter_header` reads them as it reads a download. Kind,
  parameter count and the adapter/checkpoint split come out unfaked.
* **Sparse payloads.** Nearly all of the tensor payload is a hole, so ``getsize``
  reports the 180 MB the adapter really weighs while the disk pays for the
  header and one block behind it.
  :func:`_check_sparse_support` refuses to run where the holes would be
  allocated for real, rather than filling the user's disk.
* **Distinct bytes.** Real adapters cluster hard on shape - one measured folder
  of 91 files held 28 distinct name+shape signatures, rank 32 alone being two
  thirds of them - and they stay distinct because their *weights* differ. A hole
  is all zeroes, so shape-identical files would hash identically and 1,800 rows
  would collapse to a few hundred. :func:`_payload_slug` therefore writes
  :data:`_PAYLOAD_SLUG_BYTES` of seeded noise at the head of each payload: one
  filesystem block per file, which is what the header already cost.

**The two files in the manual-import stack are byte-identical on purpose** and
must stay that way - they share a payload seed. "The same file copied into two
registered folders" and "an interrupted move left two paths" are states the shelf
has to recognise, so the fixture set owes it one real duplicate pair. It is the
only one; everything in ``adapters/`` is distinct.

Proportions below (metadata mix, rank mix, dtype mix, size spread) were measured
against a real 91-file adapter folder on 2026-08-09. That is one owner's library:
self-trained files carry ``ss_*`` metadata while model-site downloads carry only
``format``, so the ratio moves with where the files came from. It is here to stop
the mix being *backwards*, not to be exact.

Run it::

    python scripts/generate_model_shelf_fixtures.py /tmp/shelf-fixtures

Everything is seeded, so two runs produce identical trees.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import struct
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

# Bytes per parameter, by the dtype the header declares.
_DTYPE_BYTES = {"BF16": 2, "F16": 2, "F32": 4}

# Dtype mix, measured 2026-08-09 (74 BF16, 11 F16, 7 F32 of 91). Trainers have
# largely moved to bf16, and an F32 file is twice the bytes of the same shape,
# which is most of why two adapters of one rank can differ in size.
#
# The measured folder also held four files carrying I64 tensors. Those are
# auxiliary index/alpha tensors rather than weights, which nothing here models,
# so they are left out rather than faked as an I64 weight tensor.
_DTYPE_MIX = ["BF16"] * 74 + ["F16"] * 11 + ["F32"] * 7

# Adapter shapes seen in the wild, as (base label, hidden dim, rank, layers).
# Repetition is the weighting, and it is deliberately lumpy: the 91-file folder
# measured on 2026-08-09 held only 28 distinct name+shape signatures, and rank 32
# was 62 of the 91. A folder of 1,800 differently-shaped adapters would be far
# less realistic than this. Files stay distinct through their payload, not their
# shape.
#
# The parameter count follows from the shape and the file size follows from the
# parameter count, so nothing here is an invented number.
_SHAPES: tuple[tuple[str, int, int, int], ...] = (
    ("flux.1-dev", 3072, 32, 456),
    ("flux.1-dev", 3072, 32, 456),
    ("flux.1-dev", 3072, 64, 456),
    ("flux.1-dev", 3072, 32, 456),
    ("sdxl", 2048, 32, 264),
    ("flux.1-dev", 3072, 32, 304),
    ("flux.1-dev", 3072, 16, 456),
    ("qwen-image", 3584, 32, 480),
    ("flux.1-dev", 3072, 32, 456),
    ("flux.1-dev", 3072, 64, 456),
    ("sdxl", 2048, 16, 264),
    ("flux.1-dev", 3072, 32, 456),
    ("flux.1-dev", 3072, 128, 456),
    ("flux.1-dev", 3072, 32, 304),
    ("sdxl", 2048, 32, 264),
    ("flux.1-dev", 3072, 32, 456),
    ("qwen-image", 3584, 32, 480),
    ("flux.1-dev", 3072, 8, 456),
    ("flux.1-dev", 3072, 32, 456),
    ("flux.1-dev", 3072, 16, 456),
    ("flux.1-dev", 3072, 32, 304),
    ("flux.1-dev", 3072, 64, 456),
    ("flux.1-dev", 3072, 32, 456),
    ("flux.1-dev", 3072, 32, 456),
)

# The rare high-rank adapter: multi-gigabyte, and the reason B4 defers hashing
# for large files. Two of the 91 measured files were rank 256 or above, so this
# lands on one file in 59 rather than in the regular rotation.
_LARGE_SHAPES: tuple[tuple[str, int, int, int], ...] = (
    ("flux.1-dev", 3072, 256, 456),
    ("flux.1-dev", 3072, 384, 456),
)
_LARGE_EVERY = 59
_LARGE_OFFSET = 13

# Seeded noise written at the head of every payload, so that two adapters of the
# same shape are still different files. Real weights differ; a hole is all
# zeroes, which is what collapsed 1,800 fixtures onto 478 digests. One block per
# file is the smallest write a filesystem can charge for.
_PAYLOAD_SLUG_BYTES = 4096

# 30 x 10 = 300 subject/qualifier pairs, so 1,800 files means six variants of
# each pair: exactly the near-duplicate clutter the shelf's grouping is for.
_SUBJECTS = (
    "aurora nightfall cyanwood harbourlight emberfall saltmarsh porcelain "
    "ironwake duskrunner glasshour moth-and-moon tidecaller brasslily "
    "quietvolt northerly paperlantern coldsnap velvetine hallowmere "
    "stonefruit midnight-oil riverkeep amberline foxglove winterthorn "
    "cobaltdrift lamplighter silkroad greyhaven sunbleach"
).split()

_QUALIFIERS = (
    "style char concept detail lighting texture outfit pose film ink"
).split()

_METADATA_NONE = "none"
_METADATA_FORMAT_ONLY = "format-only"
_METADATA_AITOOLKIT = "ai-toolkit"

# Distribution measured against a real 91-file adapter folder on 2026-08-09:
# 57 carried trainer `ss_*` metadata, 22 carried nothing but `format`, 9 carried
# no metadata block at all. Self-trained files are the ones with metadata, so a
# library of downloads inverts this; what the shelf must not assume is that the
# informative case is rare.
_METADATA_MIX = (
    [_METADATA_AITOOLKIT] * 63 + [_METADATA_FORMAT_ONLY] * 27 + [_METADATA_NONE] * 10
)

# Adapter algorithm mix. LoRA dominates; the rest exist so the shelf's kind
# column, and `unknown`-never-renders-as-checkpoint, have something to show.
_KIND_MIX = ["lora"] * 88 + ["dora"] * 4 + ["lokr"] * 3 + ["loha"] * 3 + ["oft"] * 2

_SAMPLE_PROMPT_COUNT = 26
"""Previews ai-toolkit renders per step: one per prompt in the config."""

_CONFIG_TEMPLATE = """\
job: extension
config:
  name: {name}
  process:
    - type: sd_trainer
      training_folder: output
      device: cuda:0
      network:
        type: lora
        linear: {rank}
        linear_alpha: {rank}
      save:
        dtype: float16
        save_every: {save_every}
        max_step_saves_to_keep: 8
      datasets:
        - folder_path: datasets/{name}
          caption_ext: txt
          resolution: [512, 768, 1024]
      train:
        steps: {total_steps}
        batch_size: 1
        lr: 0.0001
      model:
        name_or_path: {base_model}
        is_flux: true
        quantize: true
      sample:
        sampler: flowmatch
        sample_every: {save_every}
        prompts:
{prompt_lines}
      trigger_word: {trigger}
meta:
  name: "{name}"
  version: "1.0"
"""


@dataclass(frozen=True)
class AdapterPlan:
    """One adapter in the big folder: everything its bytes depend on.

    Attributes:
        name: Filename stem, also the payload seed.
        tensors: Tensor name → shape.
        metadata: ``__metadata__`` block, or ``None`` for a file without one.
        dtype: Safetensors dtype for every tensor.
        payload_seed: Seed for the payload slug. Two plans that agree on
            everything *including* this describe the same file, byte for byte.
    """

    name: str
    tensors: dict[str, list[int]]
    metadata: dict[str, str] | None
    dtype: str
    payload_seed: str


@dataclass
class FixtureTree:
    """Where every §5 fixture ended up, and what it cost."""

    root: Path
    adapter_folder: Path
    aitoolkit_output: Path
    full_run: Path
    no_final_run: Path
    manual_stack: Path
    offline_mount: Path
    adapter_count: int = 0
    reported_bytes: int = 0
    """Total size the fixtures claim, i.e. what a shelf would display."""
    disk_bytes: int = 0
    """Total size they actually occupy, holes excluded."""
    warnings: list[str] = field(default_factory=list)


def _check_sparse_support(root: Path) -> None:
    """Refuse to generate on a filesystem that would allocate the holes.

    A 1,800-adapter folder claims roughly 440 GB. On ext4/btrfs/xfs/APFS that
    costs a few megabytes because the payloads are holes; on a filesystem
    without sparse files it would cost 440 GB of real disk, which is not a
    surprise anyone should get from a fixture script.

    Args:
        root: A directory on the target filesystem.

    Raises:
        RuntimeError: If a 1 MiB hole was written out in full.
    """
    probe = root / ".sparse-probe"
    try:
        with open(probe, "wb") as handle:
            handle.truncate(1024 * 1024)
        allocated = os.stat(probe).st_blocks * 512
    except (AttributeError, OSError) as exc:
        # st_blocks is POSIX-only. On a platform that cannot answer, say so and
        # stop rather than quietly writing 440 GB.
        raise RuntimeError(
            f"Cannot verify sparse-file support under {root} ({exc}). These "
            "fixtures rely on it: without holes the adapter folder is ~440 GB."
        ) from exc
    finally:
        if probe.exists():
            probe.unlink()

    if allocated >= 1024 * 1024:
        raise RuntimeError(
            f"{root} is on a filesystem without sparse files (a 1 MiB hole "
            f"allocated {allocated} bytes). The adapter folder would really "
            "weigh ~440 GB here. Point --root at ext4/btrfs/xfs/APFS."
        )


def _adapter_shape(index: int) -> tuple[str, int, int, int]:
    """Return the shape of adapter *index*: base label, dim, rank, layers.

    Args:
        index: Position in the adapter folder.

    Returns:
        One entry of :data:`_SHAPES`, or of :data:`_LARGE_SHAPES` for the rare
        multi-gigabyte file.
    """
    if index % _LARGE_EVERY == _LARGE_OFFSET:
        return _LARGE_SHAPES[(index // _LARGE_EVERY) % len(_LARGE_SHAPES)]
    return _SHAPES[index % len(_SHAPES)]


def _lora_tensors(kind: str, dim: int, rank: int, layers: int) -> dict[str, list[int]]:
    """Tensor name → shape for one adapter, in the layout its algorithm uses.

    Args:
        kind: Adapter algorithm, one of the keys in ``_KIND_MIX``, or
            ``"none"`` for a marker-free file.
        dim: Hidden dimension of the base model.
        rank: Adapter rank.
        layers: How many attention projections carry an adapter.

    Returns:
        A mapping ready to be turned into a safetensors header.
    """
    tensors: dict[str, list[int]] = {}
    for index in range(layers):
        stem = (
            f"transformer.transformer_blocks.{index // 4}.attn.to_{'qkvo'[index % 4]}"
        )
        if kind == "lora":
            tensors[f"{stem}.lora_A.weight"] = [rank, dim]
            tensors[f"{stem}.lora_B.weight"] = [dim, rank]
        elif kind == "dora":
            tensors[f"{stem}.lora_A.weight"] = [rank, dim]
            tensors[f"{stem}.lora_B.weight"] = [dim, rank]
            tensors[f"{stem}.dora_scale"] = [dim, 1]
        elif kind == "lokr":
            # LyCORIS' decomposed form: the second Kronecker factor is itself
            # low-rank, which is why a real LoKr weighs the same order as the
            # LoRA it replaces rather than a few hundred kilobytes.
            tensors[f"{stem}.lokr_w1"] = [rank, rank]
            tensors[f"{stem}.lokr_w2_a"] = [rank, dim]
            tensors[f"{stem}.lokr_w2_b"] = [dim, rank]
        elif kind == "loha":
            tensors[f"{stem}.hada_w1_a"] = [rank, dim]
            tensors[f"{stem}.hada_w1_b"] = [dim, rank]
            tensors[f"{stem}.hada_w2_a"] = [rank, dim]
            tensors[f"{stem}.hada_w2_b"] = [dim, rank]
        elif kind == "oft":
            tensors[f"{stem}.oft_blocks"] = [rank, dim // rank, dim // rank]
        else:
            # Marker-free: a format we have not met. Adapter-sized on purpose,
            # so it lands well under the checkpoint parameter threshold and the
            # shelf has to call it `unknown` rather than guessing checkpoint.
            tensors[f"{stem}.weight"] = [dim, rank]
    return tensors


def _payload_slug(seed: str) -> bytes:
    """Return this file's slice of real payload bytes.

    Args:
        seed: Anything that identifies the file. Two calls with the same seed
            return the same bytes, which is how the deliberate duplicate pair in
            :func:`generate_manual_stack` stays a duplicate.

    Returns:
        :data:`_PAYLOAD_SLUG_BYTES` of seeded noise.
    """
    return random.Random(seed).randbytes(_PAYLOAD_SLUG_BYTES)


def leading_bytes(
    tensors: dict[str, list[int]],
    metadata: dict[str, str] | None = None,
    *,
    dtype: str = "BF16",
    payload_seed: str = "",
) -> tuple[bytes, int]:
    """Return one adapter's leading bytes and the file size they imply.

    Everything after these bytes is a hole whose length the header itself
    declares, so two adapters are byte-identical exactly when their leading
    bytes match. That makes this the cheap way to check a folder for duplicates:
    hash what this returns, write nothing.

    Args:
        tensors: Tensor name → shape.
        metadata: ``__metadata__`` block, or ``None`` to omit it entirely.
        dtype: Safetensors dtype for every tensor; sets bytes per parameter.
        payload_seed: Seed for the payload slug that keeps files distinct.

    Returns:
        ``(length prefix + header JSON + payload slug, total file size)``.
    """
    item_bytes = _DTYPE_BYTES[dtype]
    header: dict[str, object] = {}
    if metadata is not None:
        header["__metadata__"] = metadata
    offset = 0
    for name, shape in tensors.items():
        count = 1
        for dim in shape:
            count *= dim
        end = offset + count * item_bytes
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [offset, end]}
        offset = end

    raw = json.dumps(header, separators=(",", ":")).encode()
    slug = _payload_slug(payload_seed)[:offset]
    return struct.pack("<Q", len(raw)) + raw + slug, 8 + len(raw) + offset


def write_safetensors(
    path: Path,
    tensors: dict[str, list[int]],
    metadata: dict[str, str] | None = None,
    *,
    dtype: str = "BF16",
    payload_seed: str = "",
) -> int:
    """Write one adapter: a real header, a slug of payload, then a hole.

    The header is genuine and self-consistent - offsets follow the declared
    shapes - so the file reads back through
    :func:`pixlstash.utils.adapter_header.describe_adapter` unchanged. Most of
    the payload after it is a hole, so the file reports its true size without
    occupying it.

    Args:
        path: Destination file.
        tensors: Tensor name → shape.
        metadata: ``__metadata__`` block, or ``None`` to omit it entirely.
        dtype: Safetensors dtype for every tensor.
        payload_seed: Seed for the payload slug. Files sharing a seed are
            byte-identical; that is only wanted in the manual-import stack.

    Returns:
        The file's reported size in bytes.
    """
    return _write_leading(
        path,
        *leading_bytes(tensors, metadata, dtype=dtype, payload_seed=payload_seed),
    )


def _write_leading(path: Path, head: bytes, total: int) -> int:
    """Write *head* at the start of *path* and leave the rest of *total* a hole."""
    with open(path, "wb") as handle:
        handle.write(head)
        handle.truncate(total)
    return total


def _adapter_names(count: int) -> list[str]:
    """Deterministic, realistic-looking adapter filenames, without extension.

    Three naming conventions in the mix, because a real folder is three
    people's habits: a trainer's ``name_000002750``, a model site's
    ``subject-qualifier-base``, and a hand-renamed ``Subject Qualifier v2``.
    """
    names: list[str] = []
    for index in range(count):
        subject = _SUBJECTS[index % len(_SUBJECTS)]
        qualifier = _QUALIFIERS[(index // len(_SUBJECTS)) % len(_QUALIFIERS)]
        variant = index // (len(_SUBJECTS) * len(_QUALIFIERS))
        convention = index % 3
        if convention == 0:
            names.append(f"{subject}_{qualifier}_v{variant + 1}")
        elif convention == 1:
            names.append(f"{qualifier}-{subject}-xl-v{variant + 1}")
        else:
            names.append(
                f"{subject.replace('-', ' ').title()} {qualifier.title()} "
                f"{(variant + 1) * 250:09d}".replace("  ", " ")
            )
    return names


def _aitoolkit_metadata(name: str, base: str, step: int, rank: int) -> dict[str, str]:
    """The metadata block ai-toolkit really writes, as measured 2026-08-07."""
    return {
        "format": "pt",
        "ss_base_model_version": base,
        "ss_output_name": name,
        "ss_network_dim": str(rank),
        "ss_tag_frequency": json.dumps({f"1_{name}": {name.split("_")[0]: 1}}),
        "software": json.dumps({"name": "ai-toolkit", "version": "0.9.11"}),
        "training_info": json.dumps({"step": step, "epoch": max(1, step // 1000)}),
    }


# Written into the adapter folder the first time it is generated. Nothing is
# deleted from a folder that does not carry it.
_FIXTURE_MARKER = ".pixlstash-fixture"


def _clean_generated_adapters(root: Path) -> None:
    """Delete a previous run's adapters, and only ever a previous run's.

    Regenerating has to be idempotent, which means removing the last run's
    files: the names are drawn from a seeded list, so a shorter run would
    otherwise leave the longer run's tail behind and the folder would hold two
    generations at once.

    But ``root`` is a caller-supplied path - the CLI takes it positionally with
    no default - and a bare ``glob("*.safetensors")`` + ``unlink()`` is one
    mistyped argument away from deleting a real model library. So a folder is
    only ever cleaned if this generator created it, proven by the marker file
    it drops. An unmarked folder that already holds ``.safetensors`` is somebody
    else's, and the run stops rather than touching it.

    Args:
        root: The adapter folder, already created.

    Raises:
        RuntimeError: The folder holds ``.safetensors`` files this generator
            did not write.
    """
    marker = root / _FIXTURE_MARKER
    if not marker.exists():
        strays = sorted(path.name for path in root.glob("*.safetensors"))
        if strays:
            raise RuntimeError(
                f"{root} already holds {len(strays)} .safetensors file(s) and "
                f"carries no {_FIXTURE_MARKER} marker, so this generator did "
                "not create it. Refusing to delete files it does not own. "
                "Point --root at an empty or previously generated directory."
            )
        marker.write_text(
            "Generated by scripts/generate_model_shelf_fixtures.py.\n"
            "Its presence is what permits this folder to be cleaned and\n"
            "regenerated. Delete the folder, not this file.\n",
            encoding="utf-8",
        )
        return
    for old in root.glob("*.safetensors"):
        old.unlink()


def iter_adapters(count: int = 1800) -> Iterator[AdapterPlan]:
    """Yield the plan for every adapter in the big folder, in folder order.

    This is the single description of that folder:
    :func:`generate_adapter_folder` writes exactly what it yields, so a caller
    that only wants to know what the files *are* - whether any two of them would
    be identical, say - can run :func:`plan_bytes` over this and touch no disk.

    Args:
        count: How many adapters to describe.

    Yields:
        One :class:`AdapterPlan` per adapter.
    """
    rng = random.Random(20261010)
    for index, name in enumerate(_adapter_names(count)):
        base, dim, rank, layers = _adapter_shape(index)
        kind = rng.choice(_KIND_MIX)
        if index % _LARGE_EVERY == _LARGE_OFFSET:
            # The multi-gigabyte file is the point of this shape, and the other
            # algorithms cost a fraction of LoRA's parameters at the same rank,
            # so it does not get to draw one.
            kind = "lora"
        # A handful of marker-free files, so the shelf has to show `unknown`
        # rather than guessing checkpoint. Their parameter counts stay well
        # under the checkpoint threshold on purpose.
        if index % 211 == 0:
            kind = "none"
        style = rng.choice(_METADATA_MIX)
        if style == _METADATA_NONE:
            metadata = None
        elif style == _METADATA_FORMAT_ONLY:
            metadata = {"format": "pt"}
        else:
            metadata = _aitoolkit_metadata(name, base, (index % 12 + 1) * 250, rank)
        yield AdapterPlan(
            name=name,
            tensors=_lora_tensors(kind, dim, rank, layers),
            metadata=metadata,
            dtype=rng.choice(_DTYPE_MIX),
            # The filename: unique within the folder, so no two adapters here
            # end up as the same file.
            payload_seed=name,
        )


def plan_bytes(plan: AdapterPlan) -> tuple[bytes, int]:
    """Return the leading bytes and size of the file *plan* describes."""
    return leading_bytes(
        plan.tensors,
        plan.metadata,
        dtype=plan.dtype,
        payload_seed=plan.payload_seed,
    )


def generate_adapter_folder(root: Path, count: int = 1800) -> tuple[int, int]:
    """Write the big folder: ``count`` adapters with real names and sizes.

    Args:
        root: Folder to fill. Created if absent.
        count: How many adapters to write.

    Returns:
        ``(reported_bytes, disk_bytes)``.

    Raises:
        RuntimeError: *root* holds ``.safetensors`` files this generator did not
            write. See :func:`_clean_generated_adapters`.
    """
    root.mkdir(parents=True, exist_ok=True)
    _clean_generated_adapters(root)
    reported = 0
    disk = 0
    for plan in iter_adapters(count):
        path = root / f"{plan.name}.safetensors"
        reported += _write_leading(path, *plan_bytes(plan))
        disk += os.stat(path).st_blocks * 512
    return reported, disk


def _write_sample(path: Path, step: int, index: int) -> None:
    """Write one tiny preview JPEG, tinted by step so the strip is readable."""
    hue = (step // 250 * 37 + index * 11) % 256
    Image.new("RGB", (64, 96), (hue, (hue * 3) % 256, 200 - hue // 2)).save(
        path, "JPEG", quality=40
    )


def generate_run(
    output_root: Path,
    name: str,
    *,
    steps: tuple[int, ...],
    final: bool,
    base_model: str,
    rank: int,
    trigger: str,
    samples_per_step: int = _SAMPLE_PROMPT_COUNT,
) -> Path:
    """Write one ai-toolkit run folder in the layout the reader expects.

    Args:
        output_root: The ``output/`` folder the run sits under.
        name: Run name; also the checkpoint stem.
        steps: Steps that got a checkpoint saved.
        final: Whether to write the bare no-step final. Without it the shelf
            cannot confirm which step the run settled on, which is the state
            the "unconfirmed cover" fixture exists to show.
        base_model: ``name_or_path`` for the config.
        rank: ``linear`` for the config.
        trigger: ``trigger_word`` for the config.
        samples_per_step: Previews rendered per step.

    Returns:
        The run folder.
    """
    run = output_root / name
    samples = run / "samples"
    samples.mkdir(parents=True, exist_ok=True)

    dim, layers = 3072, 152
    for step in steps:
        write_safetensors(
            run / f"{name}_{step:09d}.safetensors",
            _lora_tensors("lora", dim, rank, layers),
            _aitoolkit_metadata(name, base_model, step, rank),
            payload_seed=f"{name}-{step}",
        )
        for index in range(samples_per_step):
            # <timestamp>__<step>_<index>.jpg; the double underscore is what
            # makes the timestamp unambiguous.
            _write_sample(
                samples / f"17123456789{step % 100:02d}__{step:09d}_{index}.jpg",
                step,
                index,
            )
    if final:
        write_safetensors(
            run / f"{name}.safetensors",
            _lora_tensors("lora", dim, rank, layers),
            _aitoolkit_metadata(name, base_model, steps[-1], rank),
            payload_seed=f"{name}-final",
        )

    prompt_lines = "\n".join(
        f'          - "{trigger} portrait, variation {index + 1}"'
        for index in range(samples_per_step)
    )
    (run / "config.yaml").write_text(
        _CONFIG_TEMPLATE.format(
            name=name,
            rank=rank,
            base_model=base_model,
            trigger=trigger,
            save_every=steps[0] if steps else 250,
            total_steps=steps[-1] if steps else 0,
            prompt_lines=prompt_lines,
        ),
        encoding="utf-8",
    )
    return run


def generate_manual_stack(root: Path) -> Path:
    """Two hand-imported adapters of one subject: a stack with no samples.

    Nothing here came from a run we can see, so there is no ``samples/`` and no
    ``config.yaml``. The shelf has to stack these on name alone and fall back
    to a placeholder cover.

    **The two files are byte-identical, deliberately** - one shared payload seed.
    This is the fixture set's one duplicate pair: a copy that landed twice, or a
    move interrupted between the write and the unlink. Everything in
    ``adapters/`` is distinct, so a deduplicating shelf that finds nothing here
    has nothing to find anywhere.
    """
    root.mkdir(parents=True, exist_ok=True)
    for version in (1, 2):
        write_safetensors(
            root / f"Cyanwood_v{version}.safetensors",
            _lora_tensors("lora", 2048, 32, 264),
            {"format": "pt"},
            payload_seed="Cyanwood-imported-twice",
        )
    return root


def generate(root: Path, adapters: int = 1800) -> FixtureTree:
    """Build the whole §5 fixture tree under *root*.

    Args:
        root: Destination. Created if absent; existing files are overwritten.
        adapters: Size of the big adapter folder.

    Returns:
        A :class:`FixtureTree` naming every fixture and what it cost.
    """
    root.mkdir(parents=True, exist_ok=True)
    _check_sparse_support(root)

    output = root / "aitoolkit" / "output"
    tree = FixtureTree(
        root=root,
        adapter_folder=root / "adapters",
        aitoolkit_output=output,
        full_run=output / "Aurora",
        no_final_run=output / "Nightfall",
        manual_stack=root / "manual_imports",
        # An unmounted network share: the mount point's parent is there, the
        # mount point is not. A scanner must mark this `unreachable`, which is
        # a different row state from `missing`.
        offline_mount=root / "mnt" / "nas-models",
    )
    (root / "mnt").mkdir(exist_ok=True)

    tree.reported_bytes, tree.disk_bytes = generate_adapter_folder(
        tree.adapter_folder, adapters
    )
    tree.adapter_count = adapters

    generate_run(
        output,
        "Aurora",
        steps=(250, 500, 750, 1000, 1250),
        final=True,
        base_model="black-forest-labs/FLUX.1-dev",
        rank=16,
        trigger="aur0ra",
    )
    generate_run(
        output,
        "Nightfall",
        steps=(500, 1000, 1500),
        final=False,
        base_model="Qwen/Qwen-Image",
        rank=32,
        trigger="n1ghtfall",
        samples_per_step=8,
    )
    generate_manual_stack(tree.manual_stack)

    if tree.offline_mount.exists():
        tree.warnings.append(
            f"{tree.offline_mount} exists; the offline-mount fixture needs it "
            "to be absent to read as unreachable."
        )
    return tree


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("root", type=Path, help="Directory to generate into.")
    parser.add_argument(
        "--adapters",
        type=int,
        default=1800,
        help="Adapters in the big folder (default: 1800).",
    )
    args = parser.parse_args(argv)

    tree = generate(args.root, adapters=args.adapters)
    print(f"adapters:      {tree.adapter_count} in {tree.adapter_folder}")
    print(f"  reported:    {tree.reported_bytes / 1e9:.1f} GB")
    print(f"  on disk:     {tree.disk_bytes / 1e6:.1f} MB")
    print(f"full run:      {tree.full_run}")
    print(f"no-final run:  {tree.no_final_run}")
    print(f"manual stack:  {tree.manual_stack}")
    print(f"offline mount: {tree.offline_mount} (absent on purpose)")
    for warning in tree.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
