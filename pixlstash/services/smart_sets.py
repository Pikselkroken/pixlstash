"""Smart picture sets (#2148): a set whose members come from a saved filter.

A smart set is an ordinary ``PictureSet`` with ``smart`` set:
``{"filter": <query string>, "summary": <text>, "removed": [ids]}``, the
summary being the filter in the screen's words, for its row and editor.

- **The rule is the grid's own filter**, stored as the query string the grid
  sends to ``GET /pictures``, so a smart set holds what that view showed.
  :func:`normalise_filter` keeps only the keys that say which pictures (no
  sort, paging, search or per-viewer key) and drops ``project_id``: a smart
  set's own projects scope its rule instead.
- **Members are ordinary rows.** :func:`apply_members_in_session` writes the
  rule's pictures as ``PictureSetMember`` rows marked ``smart_auto``, so share
  links, export, training and the Run popup read a smart set as any other.
- **The owner's own edits win** (the workflow-set pattern, #1972). A row added
  any other way (a drag, an import into the set, a run's output, a plugin's) is
  the owner's and the refresh never takes it away; a picture the owner took out
  is kept in ``removed`` and the rule does not put it back. A set with either is
  *Custom*; *Reset to automatic* (:func:`reset_to_automatic_in_session`) drops
  both.
- **A locked set is a snapshot**: the refresh skips it.

The query that evaluates a rule lives with the picture listing, outside
``services/`` (``utils/service/smart_set_refresher.py``); everything here works
on a session it is handed (§10.1 of the backend architecture).
"""

from typing import Iterable
from urllib.parse import parse_qsl, urlencode

from sqlmodel import Session, select

from pixlstash.db_models import PictureSet, PictureSetMember
from pixlstash.pixl_logging import get_logger
from pixlstash.services.project_membership_service import (
    picture_set_project_ids,
    reconcile_entity_projects_change,
)
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks

logger = get_logger(__name__)

# The `GET /pictures` keys a rule may keep: the ones that say which pictures,
# as `PictureListFilters` documents them. Left out on purpose: sort, paging,
# fields and search (`query`) describe a view, not a set of pictures;
# `project_id` is the set's own projects; `shared_only`, `apply_tag_filter`
# and `guest_session_id` read whoever is asking, and a rule has nobody asking;
# `only_deleted` would make a set of the Scrapheap.
RULE_KEYS = frozenset(
    {
        "character_id",
        "unassigned_by",
        "character_ids",
        "character_mode",
        "set_id",
        "set_ids",
        "set_mode",
        "base_set_id",
        "min_score",
        "max_score",
        "unscored",
        "favourite",
        "smart_score_bucket",
        "resolution_bucket",
        "file_path_prefix",
        "face_filter",
        "stack_state",
        "format",
        "tag",
        "rejected_tag",
        "tag_confidence_above",
        "tag_confidence_below",
        "comfyui_model",
        "comfyui_lora",
        "workflow",
        "workflow_lora",
        "reference_folder_id",
        "import_source_folder",
        "inbox",
        "id",
        "face_cluster",
        "face_cluster_threshold",
        "impossible_tag_source",
    }
)

# The most a rule may hold, encoded: a few dozen chips fit in a few hundred
# bytes, and an id list in a few KB.
MAX_FILTER_CHARS = 64 * 1024
# The summary is a line of chips; anything past this is cut.
MAX_SUMMARY_CHARS = 500


class SmartFilterError(ValueError):
    """A rule that cannot be saved: empty, or too large."""


def normalise_filter(query: str) -> str:
    """The rule a query string describes, as the query string that is stored.

    Keeps the :data:`RULE_KEYS` pairs in the order given and drops the rest.

    Raises:
        SmartFilterError: Nothing that narrows the library is left, or the
            rule is larger than :data:`MAX_FILTER_CHARS`.
    """
    if not isinstance(query, str):
        raise SmartFilterError("smart_filter must be a query string")
    pairs = [
        (key, value)
        for key, value in parse_qsl(query.lstrip("?"), keep_blank_values=False)
        if key in RULE_KEYS
    ]
    if not pairs:
        raise SmartFilterError(
            "smart_filter has nothing a smart set can keep: pick a filter first"
        )
    encoded = urlencode(pairs)
    if len(encoded) > MAX_FILTER_CHARS:
        raise SmartFilterError("smart_filter is too large")
    return encoded


def new_smart(query: str, summary=None) -> dict:
    """A new smart set's ``smart`` value: the rule, nothing taken out."""
    return {
        "filter": normalise_filter(query),
        "summary": str(summary or "").strip()[:MAX_SUMMARY_CHARS] or None,
        "removed": [],
    }


def record_hand_edit(
    picture_set: PictureSet,
    *,
    added: Iterable[int] = (),
    removed: Iterable[int] = (),
) -> None:
    """Remember what the owner took out of a smart set, or put back.

    A no-op for an ordinary set. Only removals need remembering: a row the
    owner added is already theirs (``smart_auto`` false).
    """
    smart = picture_set.smart
    if not smart:
        return
    kept = (set(smart.get("removed") or []) - {int(i) for i in added}) | {
        int(i) for i in removed
    }
    if kept != set(smart.get("removed") or []):
        # A new dict: the JSON column does not see an in-place change.
        picture_set.smart = {**smart, "removed": sorted(kept)}


def custom_smart_set_ids_in_session(
    session: Session, set_ids: Iterable[int]
) -> set[int]:
    """The smart sets among ``set_ids`` the owner has edited by hand.

    An ordinary set is never one: every row of it is the owner's.
    """
    smart = {
        int(s.id): s.smart
        for s in session.exec(
            select(PictureSet).where(
                PictureSet.id.in_(list(set_ids)), PictureSet.smart.is_not(None)
            )
        ).all()
    }
    if not smart:
        return set()
    ids = list(smart)
    custom = {sid for sid, value in smart.items() if (value or {}).get("removed")}
    custom.update(
        int(sid)
        for sid in session.exec(
            select(PictureSetMember.set_id)
            .where(
                PictureSetMember.set_id.in_(ids),
                PictureSetMember.smart_auto.is_(False),
            )
            .distinct()
        ).all()
    )
    return custom


def reset_to_automatic_in_session(session: Session, picture_set: PictureSet) -> bool:
    """Drop the owner's own edits; True when there were any.

    The owner's rows go and nothing stays taken out; the refresh that follows
    puts back what the rule matches. Does not commit. An ordinary set is left
    alone: all its rows are the owner's.
    """
    if not picture_set.smart:
        return False
    changed = bool((picture_set.smart or {}).get("removed"))
    if changed:
        picture_set.smart = {**picture_set.smart, "removed": []}
    for member in session.exec(
        select(PictureSetMember).where(
            PictureSetMember.set_id == picture_set.id,
            PictureSetMember.smart_auto.is_(False),
        )
    ).all():
        session.delete(member)
        changed = True
    return changed


def claim_rows_in_session(
    session: Session, picture_set: PictureSet, picture_ids: Iterable[int]
) -> None:
    """Make the owner's own any rule rows among ``picture_ids`` (a hand add).

    A picture added by hand stays when the rule stops matching it, also when
    the rule had already put it in. A no-op for an ordinary set.
    """
    if not picture_set.smart:
        return
    ids = {int(i) for i in picture_ids}
    for member in session.exec(
        select(PictureSetMember).where(
            PictureSetMember.set_id == picture_set.id,
            PictureSetMember.smart_auto.is_(True),
        )
    ).all():
        if int(member.picture_id) in ids:
            member.smart_auto = False
            session.add(member)


def smart_sets_to_refresh_in_session(
    session: Session, set_ids: Iterable[int] | None = None
) -> list[tuple[int, str, list[int]]]:
    """Every unlocked smart set as ``(id, filter, project ids)``.

    A locked smart set is left out: locking freezes its members.
    """
    query = select(PictureSet).where(
        PictureSet.smart.is_not(None), PictureSet.locked.is_(False)
    )
    if set_ids is not None:
        query = query.where(PictureSet.id.in_(list(set_ids)))
    return [
        (
            int(s.id),
            str((s.smart or {}).get("filter") or ""),
            picture_set_project_ids(session, int(s.id)),
        )
        for s in session.exec(query).all()
        if (s.smart or {}).get("filter")
    ]


def apply_members_in_session(
    session: Session, set_id: int, rule_ids: Iterable[int]
) -> list[int]:
    """Make a smart set's rule rows what its rule matches now.

    The rule's pictures, whole stacks at a time like every set, less what the
    owner took out, are added as ``smart_auto`` rows; a ``smart_auto`` row the
    rule no longer matches goes. The owner's own rows are never touched. A new
    member joins every project the set is in, as a hand add does. Returns the
    ids that joined or left, empty when nothing changed, and commits only then.
    """
    picture_set = session.get(PictureSet, set_id)
    if picture_set is None or not picture_set.smart or picture_set.locked:
        return []
    wanted = set(expand_picture_ids_to_stacks(session, list(rule_ids)))
    wanted -= set(picture_set.smart.get("removed") or [])
    rows = {
        int(m.picture_id): m
        for m in session.exec(
            select(PictureSetMember).where(PictureSetMember.set_id == set_id)
        ).all()
    }
    joining = sorted(wanted - set(rows))
    leaving = sorted(
        pid for pid, row in rows.items() if row.smart_auto and pid not in wanted
    )
    if not joining and not leaving:
        return []
    for pid in leaving:
        session.delete(rows[pid])
    for pid in joining:
        session.add(PictureSetMember(set_id=set_id, picture_id=pid, smart_auto=True))
    reconcile_entity_projects_change(
        session,
        picture_ids=joining,
        ensure_project_ids=picture_set_project_ids(session, set_id),
        remove_project_ids=[],
    )
    session.commit()
    logger.info(
        "Smart set %s: %d picture(s) joined, %d left",
        set_id,
        len(joining),
        len(leaving),
    )
    return joining + leaving
