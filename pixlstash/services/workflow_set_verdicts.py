"""Workflow sets: PixlStash's check, and the owner's verdict (``docs/ideas/workflow-set-verdicts.md``).

**Two answers, never merged.** The *check* is PixlStash's: of the kept pictures
that used every member of a set together, how many look like their prompt at
all (``picture.prompt_match``, :mod:`pixlstash.scoring.prompt_match`). It is a
sanity check for noise and unrelated output, never a quality claim. The
*verdict* is the owner's yes or no, stored in the hub. Nothing here derives one
from the other, and nothing clears a verdict when the check later moves.

**A verdict belongs to one exact combination**: :func:`combo_key`, the sorted
member model ids. A set whose members change has a different key, so it has no
verdict; the old row stays where it was and is never shown for the new set.
``model.id`` is ``AUTOINCREMENT``, so an id is never reissued to another file.

**Evidence is per recipe, summed per set.** One vault ``GROUP BY`` gives every
recipe's picture counts, and a set's evidence is the sum over the recipes whose
resolved models include all of its members, so the cost is one query per
request whatever the number of sets or members. The recipes are the same
``workflow_structural_hash`` co-occurrence the Workflow sets grid reads (#1438):
a picture a manual workflow's run made still carries its variant's hash, so it
counts here too.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from fractions import Fraction
from typing import Iterable, Optional

from sqlalchemy import case, func
from sqlmodel import Session, select

from pixlstash.db_models.picture import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.scoring.prompt_match import PROMPT_MATCH_THRESHOLD
from pixlstash.services.model_shelf_service import _SET_BASE_KINDS, _set_kind_rank
from pixlstash.tasks.task_type import TaskType

logger = get_logger(__name__)

#: The check says nothing about a set with fewer checked pictures than this.
MIN_CHECKED_FOR_CHECK = 3
#: The share of checked pictures, in percent, that must look like their prompt
#: for the set to pass. Integer percent so a boundary is exact, not a float.
PASS_PERCENT = 50
#: A member is a suspect only with at least this many checked pictures made
#: with the whole set ...
SUSPECT_MIN_WITH = 5
#: ... and this many made with every other member but without it ...
SUSPECT_MIN_WITHOUT = 5
#: ... when its failure rate is at least this many percentage points above the
#: rate without it ...
SUSPECT_MIN_GAP_POINTS = 40
#: ... and at least this many pictures made with it fail the check.
SUSPECT_MIN_FAILURES = 3

CHECK_NONE = "none"
CHECK_PENDING = "pending"
CHECK_TOO_FEW = "too_few"
CHECK_PASS = "pass"
CHECK_FAIL = "fail"
CHECK_UNAVAILABLE = "unavailable"

SET_VERDICTS = ("yes", "no")
MEMBER_VERDICTS = ("problem", "not_problem")

_COMBO_KEY_RE = re.compile(r"^[1-9][0-9]*(,[1-9][0-9]*)*$")


class VerdictTargetError(LookupError):
    """A verdict named a model that is not on the shelf or not in the set. 404."""


def combo_key(model_ids: Iterable[int]) -> str:
    """The key a verdict is stored under: the member ids, sorted, comma-joined."""
    return ",".join(str(model_id) for model_id in sorted(set(model_ids)))


def parse_combo_key(key: str) -> list[int]:
    """The member ids of a canonical *key*, or ValueError for any other spelling.

    Canonical only (ascending, no repeats), so one combination can never be
    stored under two keys.
    """
    if not _COMBO_KEY_RE.match(key or ""):
        raise ValueError(f"{key!r} is not a list of model ids")
    ids = [int(part) for part in key.split(",")]
    if combo_key(ids) != key:
        raise ValueError(f"{key!r} is not sorted ascending without repeats")
    return ids


def recipe_evidence(session: Session) -> dict[str, tuple[int, int, int, int]]:
    """Per recipe: ``(kept, checked, passing, awaiting)`` pictures. One query.

    *checked* is a usable score (not NULL, not the -1.0 failure marker);
    *passing* agrees with :func:`~pixlstash.scoring.prompt_match.looks_like_prompt`
    (``score >= PROMPT_MATCH_THRESHOLD``); *awaiting* is what
    ``MissingPromptMatchFinder`` would still score (NULL with a prompt). A
    picture with no prompt is kept but never checked and never awaited.
    """
    score = Picture.prompt_match
    rows = session.exec(
        select(
            Picture.workflow_structural_hash,
            func.count(Picture.id),
            func.sum(case((score >= 0, 1), else_=0)),
            func.sum(case((score >= PROMPT_MATCH_THRESHOLD, 1), else_=0)),
            func.sum(
                case(
                    (
                        score.is_(None) & Picture.comfyui_positive_prompt.is_not(None),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .where(Picture.workflow_structural_hash.is_not(None))
        .where(Picture.deleted.is_(False))
        .group_by(Picture.workflow_structural_hash)
    ).all()
    return {
        key: (int(kept), int(checked or 0), int(passing or 0), int(awaiting or 0))
        for key, kept, checked, passing, awaiting in rows
    }


def check_state(
    together: int, checked: int, passing: int, awaiting: int, scorer_available: bool
) -> str:
    """The spec's state table, in its order.

    *Pending* is "something is still to be scored and the scorer is running",
    not "checked < together": a picture with no prompt, or one stored as -1.0,
    is never checked, and counting it as pending would keep a set pending
    forever.
    """
    if together == 0:
        return CHECK_NONE
    if awaiting and scorer_available:
        return CHECK_PENDING
    if checked == 0:
        return CHECK_UNAVAILABLE
    if checked < MIN_CHECKED_FOR_CHECK:
        return CHECK_TOO_FEW
    return CHECK_PASS if passing * 100 >= PASS_PERCENT * checked else CHECK_FAIL


def is_suspect(
    with_failed: int, with_total: int, without_failed: int, without_total: int
) -> bool:
    """Every suspect threshold at once. The gap is compared in integers."""
    return (
        with_total >= SUSPECT_MIN_WITH
        and without_total >= SUSPECT_MIN_WITHOUT
        and with_failed >= SUSPECT_MIN_FAILURES
        # with_failed/with_total - without_failed/without_total >= gap/100
        and 100 * (with_failed * without_total - without_failed * with_total)
        >= SUSPECT_MIN_GAP_POINTS * with_total * without_total
    )


def set_checks(
    member_sets: Iterable[Iterable[int]],
    recipe_models: dict[str, set[int]],
    evidence: dict[str, tuple[int, int, int, int]],
    set_verdicts: dict[str, str],
    member_verdicts: dict[str, dict[int, str]],
    scorer_available: bool,
) -> list[dict]:
    """The check, the suspects and the owner's answers for each set of members.

    Args:
        member_sets: each set's on-shelf model ids; empty and repeated sets
            are dropped.
        recipe_models: ``{structural_hash: {model_id}}`` from
            :func:`~pixlstash.services.model_shelf_service.resolve_recipe_models`.
        evidence: :func:`recipe_evidence`.
        set_verdicts / member_verdicts: :func:`read_verdicts`.
        scorer_available: whether the prompt-match scorer is scheduled.

    Returns:
        One dict per distinct set, ordered by key.
    """
    # Only recipes with kept pictures can carry evidence, so the index is
    # built over those alone: a hub holds every library's recipes.
    by_model: dict[int, set[str]] = {}
    for recipe, models in recipe_models.items():
        if recipe in evidence:
            for model_id in models:
                by_model.setdefault(model_id, set()).add(recipe)

    def recipes_with(ids) -> set[str]:
        found: Optional[set[str]] = None
        for model_id in ids:
            mine = by_model.get(model_id, set())
            found = set(mine) if found is None else found & mine
            if not found:
                return set()
        return found if found is not None else set(evidence)

    def totals(recipes) -> tuple[int, int, int, int]:
        sums = [0, 0, 0, 0]
        for recipe in recipes:
            for index, value in enumerate(evidence[recipe]):
                sums[index] += value
        return tuple(sums)

    out = []
    for key in sorted({combo_key(ids) for ids in member_sets} - {""}):
        ids = [int(part) for part in key.split(",")]
        together_recipes = recipes_with(ids)
        together, checked, passing, awaiting = totals(together_recipes)
        answered = member_verdicts.get(key, {})
        suspects = []
        if len(ids) > 1 and checked:
            for model_id in ids:
                others = recipes_with(m for m in ids if m != model_id)
                _, without_total, without_passing, _ = totals(
                    others - by_model.get(model_id, set())
                )
                with_failed = checked - passing
                without_failed = without_total - without_passing
                if is_suspect(with_failed, checked, without_failed, without_total):
                    suspects.append(
                        {
                            "model_id": model_id,
                            "with_failed": with_failed,
                            "with_total": checked,
                            "without_failed": without_failed,
                            "without_total": without_total,
                            "verdict": answered.get(model_id),
                        }
                    )
            # Biggest gap first, so the three the panel shows are the strongest.
            suspects.sort(
                key=lambda s: (
                    Fraction(s["without_failed"], s["without_total"])
                    - Fraction(s["with_failed"], s["with_total"]),
                    s["model_id"],
                )
            )
        out.append(
            {
                "combo_key": key,
                "member_ids": ids,
                "evidence": {
                    "together": together,
                    "checked": checked,
                    "passing": passing,
                },
                "check": check_state(
                    together, checked, passing, awaiting, scorer_available
                ),
                "verdict": set_verdicts.get(key),
                "suspects": suspects,
                "member_verdicts": [
                    {"model_id": model_id, "verdict": verdict}
                    for model_id, verdict in sorted(answered.items())
                ],
            }
        )
    return out


def shown_member_sets(found: dict) -> list[set[int]]:
    """The member sets the Workflow sets grid draws, as the client groups them.

    Mirrors ``frontend/src/utils/workflowSets.js`` ``setGroups`` over the
    store's ``visibleCombinations`` with every kind shown: the combinations no
    hand-made set covers, grouped by their head (the first member when it is a
    base model, else the first missing base name), each group the union of its
    members. Plus every hand-made set's on-shelf members. A client whose
    ``Show`` filter drops a combination draws a smaller union, whose key is
    not here, and it then shows no check rather than another set's.
    """
    groups: dict[tuple[str, object], set[int]] = {}
    for combination in found["combinations"]:
        if combination.get("covered_by"):
            continue
        models = combination["models"]
        if models and models[0]["kind"] in _SET_BASE_KINDS:
            head = ("model", models[0]["id"])
        elif combination["missing"]:
            head = ("missing", combination["missing"][0]["name"])
        else:
            continue
        groups.setdefault(head, set()).update(m["id"] for m in models)
    sets = list(groups.values())
    for entry in found.get("hand_made", ()):
        sets.append(
            {m["id"] for m in entry["members"] if m["on_shelf"] and m["id"] is not None}
        )
    return sets


def fetch_set_checks(hub, vault, found: dict) -> list[dict]:
    """:func:`set_checks` for the sets the grid draws. One vault read, two hub reads.

    *found* is :func:`~pixlstash.services.model_shelf_service.fetch_workflow_sets`
    after :func:`~pixlstash.services.model_workflow_sets.attach_hand_made`; its
    ``recipe_models`` is reused rather than resolved a second time.
    """
    evidence = vault.db.run_immediate_read_task(recipe_evidence)
    set_verdicts, member_verdicts = read_verdicts(hub)
    return set_checks(
        shown_member_sets(found),
        found["recipe_models"],
        evidence,
        set_verdicts,
        member_verdicts,
        vault.is_worker_running(TaskType.PROMPT_MATCH),
    )


# ---------------------------------------------------------------------------
# The owner's answers
# ---------------------------------------------------------------------------


def read_verdicts(hub) -> tuple[dict[str, str], dict[str, dict[int, str]]]:
    """``({combo_key: verdict}, {combo_key: {model_id: verdict}})``. Two reads."""
    sets = {
        row["combo_key"]: row["verdict"]
        for row in hub.fetchall("SELECT combo_key, verdict FROM model_set_verdict")
    }
    members: dict[str, dict[int, str]] = {}
    for row in hub.fetchall(
        "SELECT combo_key, model_id, verdict FROM model_set_member_verdict"
    ):
        members.setdefault(row["combo_key"], {})[int(row["model_id"])] = row["verdict"]
    return sets, members


def _require_models(conn, ids: list[int], key: str) -> None:
    marks = ", ".join("?" for _ in ids)
    found = {
        int(row["id"])
        for row in conn.execute(f"SELECT id FROM model WHERE id IN ({marks})", ids)
    }
    gone = sorted(set(ids) - found)
    if gone:
        raise VerdictTargetError(
            f"Set {key} names model(s) {gone} the shelf does not hold."
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_set_verdict(hub, key: str, verdict: Optional[str]) -> Optional[str]:
    """Record (or with None, clear) the owner's verdict on set *key*.

    Returns the verdict it replaced, so writing that back undoes the call.
    """
    ids = parse_combo_key(key)
    with hub.transaction() as conn:
        _require_models(conn, ids, key)
        row = conn.execute(
            "SELECT verdict FROM model_set_verdict WHERE combo_key = ?", (key,)
        ).fetchone()
        previous = row["verdict"] if row else None
        if verdict is None:
            conn.execute("DELETE FROM model_set_verdict WHERE combo_key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO model_set_verdict (combo_key, verdict, answered_at) "
                "VALUES (?, ?, ?) ON CONFLICT(combo_key) DO UPDATE SET "
                "verdict = excluded.verdict, answered_at = excluded.answered_at",
                (key, verdict, _now()),
            )
    logger.info("Workflow set %s verdict: %s (was %s).", key, verdict, previous)
    return previous


def write_member_verdict(
    hub, key: str, model_id: int, verdict: Optional[str]
) -> Optional[str]:
    """Record (or with None, clear) whether *model_id* is a problem in set *key*.

    Returns the verdict it replaced. The model must be a member of *key*.
    """
    ids = parse_combo_key(key)
    if model_id not in ids:
        raise VerdictTargetError(f"Model {model_id} is not a member of set {key}.")
    with hub.transaction() as conn:
        _require_models(conn, ids, key)
        row = conn.execute(
            "SELECT verdict FROM model_set_member_verdict "
            "WHERE combo_key = ? AND model_id = ?",
            (key, model_id),
        ).fetchone()
        previous = row["verdict"] if row else None
        if verdict is None:
            conn.execute(
                "DELETE FROM model_set_member_verdict "
                "WHERE combo_key = ? AND model_id = ?",
                (key, model_id),
            )
        else:
            conn.execute(
                "INSERT INTO model_set_member_verdict "
                "(combo_key, model_id, verdict, answered_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(combo_key, model_id) DO UPDATE SET "
                "verdict = excluded.verdict, answered_at = excluded.answered_at",
                (key, model_id, verdict, _now()),
            )
    logger.info(
        "Workflow set %s member %d verdict: %s (was %s).",
        key,
        model_id,
        verdict,
        previous,
    )
    return previous


def fetch_model_marks(hub) -> dict[int, list[dict]]:
    """Per model, the owner's member answers about it, for its shelf card.

    ``{model_id: [{"combo_key", "names", "verdict"}]}``, ``names`` being the
    set's members in the shelf's names (a member no longer on the shelf is
    left out of them). One read when nobody has answered, two otherwise.
    """
    rows = hub.fetchall(
        "SELECT combo_key, model_id, verdict FROM model_set_member_verdict "
        "ORDER BY combo_key, model_id"
    )
    if not rows:
        return {}
    models = {
        int(row["id"]): row
        for row in hub.fetchall(
            "SELECT id, display_name, filename, file_kind FROM model"
        )
    }

    def name(model_id: int) -> str:
        row = models[model_id]
        return row["display_name"] or row["filename"] or f"model {model_id}"

    marks: dict[int, list[dict]] = {}
    for row in rows:
        # In the grid's member order (base model first), so a tooltip reads
        # "Base + LoRA" as the set's card does.
        members = sorted(
            (m for m in parse_combo_key(row["combo_key"]) if m in models),
            key=lambda m: (_set_kind_rank(models[m]), name(m).lower()),
        )
        marks.setdefault(int(row["model_id"]), []).append(
            {
                "combo_key": row["combo_key"],
                "names": [name(m) for m in members],
                "verdict": row["verdict"],
            }
        )
    return marks
