#!/usr/bin/env python3
"""Back-fill tag predictions for pictures that have tags but no predictions.

Tag predictions are only ever written by the PixlStash tagger, in the same pass
that applies tags. Pictures tagged before that became inline (or by a different
engine at the time) keep their confirmed tags but have zero ``tag_prediction``
rows, and the normal pipeline never revisits them (``MissingTagFinder`` only
re-tags pictures carrying a retag sentinel).

This runs the PixlStash tagger for the raw confidence scores only and writes
predictions against each picture's existing tags. It never adds, removes, or
rewrites a tag, so a curated tag set is left untouched. It is the exact code
path the in-server ``MissingTagPredictionFinder`` uses; this script just drives
it on demand so you can run the catch-up immediately and watch progress instead
of waiting for the background planner.

Requires the tagger models (GPU recommended; pass --force-cpu otherwise).

Examples:
    # See how many pictures need back-filling, without running inference.
    python scripts/backfill_tag_predictions.py /path/to/vault.db --dry-run

    # Back-fill everything.
    python scripts/backfill_tag_predictions.py /path/to/vault.db

    # Back-fill at most 500 pictures on CPU.
    python scripts/backfill_tag_predictions.py /path/to/vault.db --force-cpu --limit 500
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pixlstash.database import VaultDatabase  # noqa: E402
from pixlstash.inference.engine import InferenceEngine  # noqa: E402
from pixlstash.tasks.missing_tag_prediction_finder import (  # noqa: E402
    MissingTagPredictionFinder,
)
from pixlstash.tasks.tag_prediction_backfill_task import (  # noqa: E402
    TagPredictionBackfillTask,
)
from pixlstash.vault import Vault  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("db_path", help="Path to vault.db")
    parser.add_argument(
        "--force-cpu",
        action="store_true",
        help="Run tagger inference on CPU instead of CUDA.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Pictures per inference batch (default: tagger's suggested size).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of pictures to process (0 = all).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report how many pictures need back-filling; run no inference.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not Path(args.db_path).exists():
        raise FileNotFoundError(f"Database not found: {args.db_path}")

    vault_db = VaultDatabase(str(args.db_path))
    try:
        remaining = int(
            vault_db.run_immediate_read_task(Vault._count_missing_tag_predictions) or 0
        )
        print(f"Pictures with tags but no predictions: {remaining}")
        if args.dry_run or remaining == 0:
            return

        engine = InferenceEngine.create(
            image_root=vault_db.image_root,
            force_cpu=args.force_cpu,
        )
        try:
            workflow = engine.tagging_workflow
            if not workflow.is_pixlstash_tagger_enabled:
                raise SystemExit(
                    "The PixlStash tagger is not the active tagger, so it cannot "
                    "produce the scores predictions are built from. Aborting."
                )

            batch_size = args.batch_size or max(1, int(workflow.suggested_task_size()))
            processed = 0
            total_written = 0
            start = time.perf_counter()
            while True:
                fetch_limit = batch_size
                if args.limit:
                    if processed >= args.limit:
                        break
                    fetch_limit = min(batch_size, args.limit - processed)

                pictures = vault_db.run_immediate_read_task(
                    MissingTagPredictionFinder._fetch_missing_predictions,
                    fetch_limit,
                )
                if not pictures:
                    break

                task = TagPredictionBackfillTask(
                    database=vault_db,
                    tagging_workflow=workflow,
                    pictures=pictures,
                )
                result = task._run_task()
                processed += int(result.get("pictures", 0))
                total_written += int(result.get("backfilled", 0))
                elapsed = time.perf_counter() - start
                rate = processed / elapsed if elapsed > 0 else 0.0
                print(
                    f"  processed={processed} rows_written={total_written} "
                    f"({rate:.1f} pics/s)"
                )

            print(
                f"Done. Processed {processed} picture(s), "
                f"wrote {total_written} prediction row(s)."
            )
        finally:
            engine.close()
    finally:
        vault_db.close()


if __name__ == "__main__":
    main()
