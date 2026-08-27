"""Read an ai-toolkit training run off disk and describe it, without touching it.

The model shelf needs to show a user what a training run produced before they
commit to importing any of it: which steps were saved, what each step looked
like, and what the run was trained against. All of that is already on disk in
ai-toolkit's output folder, so this module reads it and nothing else. It does
not hash, copy, move, or write anything, which is what makes it safe to run
against a folder the user has merely pointed at.

Layout it understands::

    output/
      MyCharacter/
        MyCharacter_000000250.safetensors
        MyCharacter_000002750.safetensors
        MyCharacter.safetensors          <- the bare final, no step in the name
        config.yaml
        samples/
          1712345678901__000000250_0.jpg
          1712345678901__000000250_1.jpg

**Scoped to ai-toolkit deliberately.** Other trainers lay their output out
differently, and a reader that tries to cover all of them ends up recognising
none of them reliably. Other trainers get their own reader later, or not at all.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

CHECKPOINT_SUFFIX = ".safetensors"
SAMPLE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
CONFIG_NAMES = ("config.yaml", "config.yml")
SAMPLES_DIRNAME = "samples"

# `<run>_000002750.safetensors`. The step is zero-padded and always trails, so
# anchoring on the end avoids tripping over a run whose own name ends in digits
# (`sdxl_v2_000000500` is run "sdxl_v2" at step 500, not run "sdxl" at v2).
_STEP_RE = re.compile(r"^(?P<name>.+)_(?P<step>\d{4,})$")

# `1712345678901__000002750_3.jpg`, i.e. <timestamp>__<step>_<index>. The
# separator between timestamp and step is a DOUBLE underscore, which is what
# lets the timestamp itself be parsed unambiguously.
_SAMPLE_RE = re.compile(r"^(?P<ts>\d+)__(?P<step>\d+)_(?P<index>\d+)$")


@dataclass(frozen=True)
class Checkpoint:
    """One saved adapter file from a run."""

    path: str
    filename: str
    step: int | None
    """``None`` for the bare final checkpoint, which carries no step in its name."""

    @property
    def is_final(self) -> bool:
        return self.step is None


@dataclass(frozen=True)
class Sample:
    """One preview image ai-toolkit rendered at a given step."""

    path: str
    filename: str
    step: int
    index: int
    timestamp: str


@dataclass
class TrainingRun:
    """Everything a run says about itself, read from disk only."""

    name: str
    path: str
    checkpoints: list[Checkpoint] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    base_model: str | None = None
    trigger_words: list[str] = field(default_factory=list)
    rank: int | None = None
    config_error: str | None = None
    """Why the config could not be read, when it could not. The run is still
    usable without it: steps and samples come from filenames."""

    @property
    def steps(self) -> list[int]:
        """Every step that has a checkpoint, ascending. Excludes the final."""
        return sorted(c.step for c in self.checkpoints if c.step is not None)

    def samples_for_step(self, step: int) -> list[Sample]:
        """Previews for one step, in the order ai-toolkit rendered them."""
        return sorted(
            (s for s in self.samples if s.step == step), key=lambda s: s.index
        )

    def samples_for(self, checkpoint: Checkpoint) -> list[Sample]:
        """Previews that belong to one checkpoint, final included.

        A stepped checkpoint takes the samples naming its step. **The bare final
        takes the highest sample step's**, because it carries no step of its own
        and is the stack cover: a rule that left it blank would make the most
        visible row of a fresh import the only empty one. When a stepped
        checkpoint is imported at that same step the previews are taken twice,
        which is 15 MB of duplication against a cover that reads.

        Returns an empty list for a run with no samples at all.
        """
        if not checkpoint.is_final:
            return self.samples_for_step(checkpoint.step)
        if not self.samples:
            return []
        return self.samples_for_step(max(sample.step for sample in self.samples))


def is_sample_filename(filename: str) -> bool:
    """Whether a filename is one ai-toolkit wrote as a step preview.

    The same two tests :func:`_read_samples` applies - an image extension and
    the ``<timestamp>__<step>_<index>`` shape - exposed because a *caller* needs
    to ask it of a file it did not read here: the delete verb decides whether a
    ``<stem>_samples/`` directory holds only the trainer's previews, and so is
    the model's to remove, or something the owner put there.
    """
    stem, ext = os.path.splitext(filename)
    return ext.lower() in SAMPLE_SUFFIXES and _SAMPLE_RE.match(stem) is not None


def _split_step(stem: str, run_name: str) -> int | None:
    """Read the step out of a checkpoint stem, or ``None`` for the final save.

    A stem with no trailing step is the run's final save, so the step is
    ``None`` rather than 0. Zero is a real step ai-toolkit can emit, and
    conflating "final" with "step 0" would sort the finished adapter to the
    front of the list.

    The parsed name has to equal ``run_name`` or the digits are part of the run
    name, not a step: a run ``Archive_2025`` saves its final as
    ``Archive_2025.safetensors``, which the pattern alone reads as run
    "Archive" at step 2025.
    """
    match = _STEP_RE.match(stem)
    if not match or match.group("name") != run_name:
        return None
    return int(match.group("step"))


def _read_checkpoints(run_dir: str, run_name: str) -> list[Checkpoint]:
    found: list[Checkpoint] = []
    for entry in os.scandir(run_dir):
        if not entry.is_file() or not entry.name.endswith(CHECKPOINT_SUFFIX):
            continue
        stem = entry.name[: -len(CHECKPOINT_SUFFIX)]
        step = _split_step(stem, run_name)
        found.append(Checkpoint(path=entry.path, filename=entry.name, step=step))
    # Ascending by step with the final last: that is the order a user reads a
    # run in, and the final is the one they most often want.
    return sorted(
        found, key=lambda c: (c.step is None, c.step if c.step is not None else 0)
    )


def _read_samples(run_dir: str) -> list[Sample]:
    samples_dir = os.path.join(run_dir, SAMPLES_DIRNAME)
    if not os.path.isdir(samples_dir):
        return []

    found: list[Sample] = []
    for entry in os.scandir(samples_dir):
        if not entry.is_file():
            continue
        stem, ext = os.path.splitext(entry.name)
        if ext.lower() not in SAMPLE_SUFFIXES:
            continue
        match = _SAMPLE_RE.match(stem)
        if not match:
            # Not a failure. A user may well have dropped their own images in
            # here, and they simply are not step previews.
            logger.debug(
                "Ignoring unrecognised sample filename %r in %s",
                entry.name,
                samples_dir,
            )
            continue
        found.append(
            Sample(
                path=entry.path,
                filename=entry.name,
                step=int(match.group("step")),
                index=int(match.group("index")),
                timestamp=match.group("ts"),
            )
        )
    return sorted(found, key=lambda s: (s.step, s.index))


def _find_key(node: object, key: str) -> object | None:
    """First value for ``key`` anywhere in a nested mapping or list.

    ai-toolkit nests these under ``config.process[0].model`` and friends, and
    that path has moved between versions. Searching by key rather than by path
    means a layout change costs nothing here.
    """
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_key(item, key)
            if found is not None:
                return found
    return None


def _config_path(run_dir: str) -> str | None:
    for name in CONFIG_NAMES:
        candidate = os.path.join(run_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _apply_config(run: TrainingRun) -> None:
    """Fill in base model, triggers and rank from ``config.yaml``, best effort.

    Every failure here is recorded on the run and logged, never raised: the
    checkpoints and samples are the point, and they come from filenames. A run
    whose config is missing or malformed is still worth showing, it just shows
    less.
    """
    path = _config_path(run.path)
    if path is None:
        run.config_error = "no config.yaml in the run folder"
        return

    # Local import, and optional on purpose. PyYAML is present transitively but
    # is not a declared dependency, and this is the only thing in the reader
    # that wants it. Per the project's import rule, a clearly optional import
    # may be local.
    try:
        import yaml
    except ImportError as exc:
        run.config_error = f"PyYAML unavailable, config not read ({exc})"
        logger.warning(
            "PyYAML is unavailable, so %s was not read. The run still lists its "
            "checkpoints and samples; only base model, trigger words and rank "
            "are missing.",
            path,
        )
        return

    try:
        with open(path, "r", encoding="utf-8") as handle:
            parsed = yaml.safe_load(handle)
    except (OSError, UnicodeDecodeError) as exc:
        run.config_error = f"could not read {os.path.basename(path)}: {exc}"
        logger.warning("Could not read ai-toolkit config %s: %s", path, exc)
        return
    except yaml.YAMLError as exc:
        run.config_error = f"could not parse {os.path.basename(path)}: {exc}"
        logger.warning("Could not parse ai-toolkit config %s: %s", path, exc)
        return

    base_model = _find_key(parsed, "name_or_path")
    if isinstance(base_model, str) and base_model.strip():
        run.base_model = base_model.strip()

    trigger = _find_key(parsed, "trigger_word")
    if isinstance(trigger, str) and trigger.strip():
        run.trigger_words = [trigger.strip()]
    elif isinstance(trigger, list):
        run.trigger_words = [
            item.strip() for item in trigger if isinstance(item, str) and item.strip()
        ]

    rank = _find_key(parsed, "linear")
    if isinstance(rank, bool):
        # `linear: true` is a different setting that happens to share the name.
        # bool is a subclass of int, so this check has to come first or a rank
        # of 1 appears out of nowhere.
        logger.debug("Ignoring boolean `linear` in %s; it is not a rank.", path)
    elif isinstance(rank, int):
        run.rank = rank


def read_run(run_dir: str) -> TrainingRun:
    """Describe one ai-toolkit run folder.

    Args:
        run_dir: A single run's output folder, the one holding the
            ``.safetensors`` files.

    Returns:
        The run. A folder with no checkpoints still returns a ``TrainingRun``;
        callers decide whether an empty run is worth showing.

    Raises:
        NotADirectoryError: If ``run_dir`` is not a directory. Anything else on
            disk is reported on the run rather than raised.
    """
    if not os.path.isdir(run_dir):
        raise NotADirectoryError(f"Not an ai-toolkit run folder: {run_dir}")

    run = TrainingRun(name=os.path.basename(os.path.normpath(run_dir)), path=run_dir)
    try:
        run.checkpoints = _read_checkpoints(run_dir, run.name)
        run.samples = _read_samples(run_dir)
    except OSError as exc:
        # A run folder that becomes unreadable mid-scan is worth surfacing, but
        # it must not take the whole output listing down with it.
        run.config_error = f"could not list {run_dir}: {exc}"
        logger.warning("Could not list ai-toolkit run folder %s: %s", run_dir, exc)
        return run

    _apply_config(run)
    return run


def read_output_root(root: str) -> list[TrainingRun]:
    """Describe every run under an ai-toolkit ``output/`` folder.

    Args:
        root: The ``output/`` folder itself, whose children are run folders.

    Returns:
        Runs that hold at least one checkpoint, sorted by name. A child folder
        with no ``.safetensors`` in it is not a training run, so it is skipped
        rather than listed as an empty one.

    Raises:
        NotADirectoryError: If ``root`` is not a directory.
    """
    if not os.path.isdir(root):
        raise NotADirectoryError(f"Not an ai-toolkit output folder: {root}")

    runs: list[TrainingRun] = []
    try:
        children = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError as exc:
        logger.warning("Could not list ai-toolkit output folder %s: %s", root, exc)
        raise

    for entry in children:
        if not entry.is_dir():
            continue
        run = read_run(entry.path)
        if run.checkpoints:
            runs.append(run)
        else:
            logger.debug(
                "Skipping %s: no %s files, so it is not a training run.",
                entry.path,
                CHECKPOINT_SUFFIX,
            )
    return runs
