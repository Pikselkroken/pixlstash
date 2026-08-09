"""Generate the model-shelf fixture tree on demand.

The shelf (v1.10) has to look right against 1,800 adapters, a real training run
and a folder that is not there. The real thing is ~100 GB, so it is generated
rather than committed, and two things keep that honest:

* **Real headers.** Every ``.safetensors`` carries a genuine length prefix and
  header JSON, with tensor names and shapes taken from real files, so
  :mod:`pixlstash.utils.adapter_header` reads them as it reads a download. Kind,
  parameter count and the adapter/checkpoint split come out unfaked.
* **Sparse payloads.** The tensor payload is a hole, so ``getsize`` reports the
  170 MB the adapter really weighs while the disk pays for the header alone.
  :func:`_check_sparse_support` refuses to run where the holes would be
  allocated for real, rather than filling the user's disk.

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
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

# Sizes are in bytes throughout, and every "reported" size below is what a real
# adapter of that shape weighs: fp16, two bytes per parameter.
_BYTES_PER_PARAM = 2

# Adapter shapes seen in the wild, as (base label, hidden dim, rank, layers).
# The parameter count follows from the shape, and the file size follows from
# the parameter count, so nothing here is an invented number.
_SHAPES: tuple[tuple[str, int, int, int], ...] = (
    ("flux.1-dev", 3072, 16, 152),
    ("flux.1-dev", 3072, 32, 152),
    ("sdxl", 2048, 32, 264),
    ("sdxl", 2048, 64, 264),
    ("sd15", 768, 32, 192),
    ("qwen-image", 3584, 16, 120),
)

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

# Distribution measured against a real adapter folder on 2026-08-07: a model
# site download usually carries nothing but `format`, and rich ai-toolkit
# metadata is the minority case. Getting this backwards makes the shelf look
# far more informative than it will be.
_METADATA_MIX = (
    [_METADATA_FORMAT_ONLY] * 70 + [_METADATA_AITOOLKIT] * 25 + [_METADATA_NONE] * 5
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

    A 1,800-adapter folder claims roughly 100 GB. On ext4/btrfs/xfs/APFS that
    costs a few megabytes because the payloads are holes; on a filesystem
    without sparse files it would cost 100 GB of real disk, which is not a
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
        # stop rather than quietly writing 100 GB.
        raise RuntimeError(
            f"Cannot verify sparse-file support under {root} ({exc}). These "
            "fixtures rely on it: without holes the adapter folder is ~100 GB."
        ) from exc
    finally:
        if probe.exists():
            probe.unlink()

    if allocated >= 1024 * 1024:
        raise RuntimeError(
            f"{root} is on a filesystem without sparse files (a 1 MiB hole "
            f"allocated {allocated} bytes). The adapter folder would really "
            "weigh ~100 GB here. Point --root at ext4/btrfs/xfs/APFS."
        )


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
            tensors[f"{stem}.lokr_w1"] = [rank, rank]
            tensors[f"{stem}.lokr_w2"] = [dim // rank, dim // rank]
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


def write_safetensors(
    path: Path,
    tensors: dict[str, list[int]],
    metadata: dict[str, str] | None = None,
) -> int:
    """Write one adapter: a real header, a sparse payload.

    The header is genuine and self-consistent — offsets follow the declared
    shapes — so the file reads back through
    :func:`pixlstash.utils.adapter_header.describe_adapter` unchanged. The
    payload after it is a hole, so the file reports its true size without
    occupying it.

    Args:
        path: Destination file.
        tensors: Tensor name → shape.
        metadata: ``__metadata__`` block, or ``None`` to omit it entirely.

    Returns:
        The file's reported size in bytes.
    """
    header: dict[str, object] = {}
    if metadata is not None:
        header["__metadata__"] = metadata
    offset = 0
    for name, shape in tensors.items():
        count = 1
        for dim in shape:
            count *= dim
        end = offset + count * _BYTES_PER_PARAM
        header[name] = {"dtype": "F16", "shape": shape, "data_offsets": [offset, end]}
        offset = end

    raw = json.dumps(header).encode()
    total = 8 + len(raw) + offset
    with open(path, "wb") as handle:
        handle.write(struct.pack("<Q", len(raw)))
        handle.write(raw)
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

    But ``root`` is a caller-supplied path — the CLI takes it positionally with
    no default — and a bare ``glob("*.safetensors")`` + ``unlink()`` is one
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


def generate_adapter_folder(root: Path, count: int = 1800) -> tuple[int, int]:
    """Write the big folder: ``count`` adapters with real names and sizes.

    Args:
        root: Folder to fill. Created if absent.
        count: How many adapters to write.

    Returns:
        ``(reported_bytes, disk_bytes)``.
    """
    root.mkdir(parents=True, exist_ok=True)
    _clean_generated_adapters(root)
    rng = random.Random(20261010)
    reported = 0
    disk = 0
    for index, name in enumerate(_adapter_names(count)):
        base, dim, rank, layers = _SHAPES[index % len(_SHAPES)]
        kind = rng.choice(_KIND_MIX)
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
        path = root / f"{name}.safetensors"
        reported += write_safetensors(
            path, _lora_tensors(kind, dim, rank, layers), metadata
        )
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
    """
    root.mkdir(parents=True, exist_ok=True)
    for version in (1, 2):
        write_safetensors(
            root / f"Cyanwood_v{version}.safetensors",
            _lora_tensors("lora", 2048, 32, 264),
            {"format": "pt"},
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
