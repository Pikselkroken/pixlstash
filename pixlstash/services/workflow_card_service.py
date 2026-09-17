"""Assembling a workflow CARD out of hub rows and vault counts (v1.12 B3).

**Computed per request, with no aggregate table.** The grid costs two vault
queries - one ``GROUP BY workflow_structural_hash`` and one ``ROW_NUMBER()``
window - plus a handful of primary-key reads in the hub, and everything else
here is arithmetic over their results. An aggregate table would have to be
invalidated by every rating, every import, every soft delete and every re-run
of the card backfill, and would be a second source of truth for numbers the
vault can already produce inside the frame budget.

Three orderings are decided here and nowhere else:

* **Cover rank** is the Bayesian mean ``(C·m + Σscore)/(C + n_rated)`` with
  ``C = 5`` and *m* the library's own mean rating, then picture count. A plain
  mean would put a card with one 5★ picture above a card with forty averaging
  4.5, which is the failure the prior exists to stop.
* **A card's cover pictures** are its best three, and they are picked in memory
  out of the best three of each of its variants - always a superset, because a
  card's pictures are the union of its variants'.
* **The effective stack** is a manual assignment, then an unstacking, then the
  automatic group by ``core_hash``. A stored member row for a card that has
  since left its group is ignored rather than honoured, and a card that joined
  a group after the owner last ordered it is appended in cover-rank order.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_card_reads import (
    Card,
    StackRows,
    card_index,
    chosen_covers,
    default_overrides,
    instance_documents,
    stack_rows,
    variant_documents,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import WorkflowGraphError, is_link
from pixlstash.services.workflow_identity import differs_by, topology_node_labels
from pixlstash.services.workflow_library_service import (
    CoverCandidate,
    VariantActivity,
    read_card_grid,
    read_chosen_cover_ids,
    read_instance_hashes,
)
from pixlstash.services.workflow_parameters import FEATURED_NAMES

logger = get_logger(__name__)

# How many pictures a card's cover strip shows, and therefore how deep the
# window pass goes per variant.
COVER_DEPTH = 3

# The prior's weight in the cover rank. Five is "about one card's worth of
# ratings": a card needs roughly that many before its own mean outweighs the
# library's, which is the point at which the number starts meaning something.
RANK_PRIOR_WEIGHT = 5

# What "your best pictures" means when a default is read off them.
BEST_SCORE = 4

# Where a default came from. ``best`` is the mode over instances of pictures
# rated ``BEST_SCORE`` and up, ``all`` the same over every picture of the card
# (a card nobody has rated still has to offer a default), and ``edited`` is the
# owner's own value, which outranks both.
FROM_BEST = "best"
FROM_ALL = "all"
EDITED = "edited"

# A card nobody is going to look for: too few pictures to be a habit, never
# rated, and never imported as a file of its own.
#
# ponytail: the design's fourth clause, "no saved recipe", is not tested here
# because saved recipes have no table yet (they arrive with their own step).
# When they do, this predicate is where the clause goes - a card the owner has
# saved a recipe against is by definition not a one-off.
ONE_OFF_PICTURES = 3

_EPOCH = datetime.min


@dataclass
class CardFigures:
    """One card's counts, its rank and the pictures that would cover it."""

    card: Card
    pictures: int = 0
    rated: int = 0
    score_total: int = 0
    last_used: Optional[datetime] = None
    rank: float = 0.0
    cover_picture_ids: list[int] = field(default_factory=list)
    stack_id: Optional[str] = None
    differs_by: list[str] = field(default_factory=list)

    @property
    def one_off(self) -> bool:
        """Too small, unrated and never imported: folded into a count."""
        return (
            self.pictures < ONE_OFF_PICTURES
            and self.rated == 0
            and not self.card.imported
        )


@dataclass
class Stack:
    """An effective stack: its members in order, the first one the cover."""

    stack_id: str
    kind: str
    member_keys: list[str]
    differs_by: list[str] = field(default_factory=list)

    @property
    def cover_key(self) -> str:
        return self.member_keys[0]


@dataclass
class Grid:
    """``GET /workflows/cards``: what the Workflows view opens on.

    ``cards`` is what the grid draws - visible, in cover-rank order, hidden
    cards and one-offs removed. ``figures`` is every card including those,
    because a card the grid does not draw still has to open by its own URL:
    hiding one is a decision about the grid, not a deletion.
    """

    cards: list[CardFigures]
    stacks: list[Stack]
    one_offs: int
    hidden: int
    figures: list[CardFigures] = field(default_factory=list)

    def figure(self, workflow_key: str) -> Optional[CardFigures]:
        """One card's figures by key, hidden and one-off cards included."""
        return next(
            (f for f in self.figures if f.card.workflow_key == workflow_key), None
        )


def _cover_order(candidate: CoverCandidate) -> tuple:
    """The window's ORDER BY, re-expressed so the per-card pick matches it."""
    return (
        candidate.score or 0,
        candidate.smart_score or 0.0,
        candidate.used_at or _EPOCH,
        candidate.picture_id,
    )


def _figures(
    cards: list[Card],
    activity: dict[str, VariantActivity],
    candidates: list[CoverCandidate],
) -> list[CardFigures]:
    """Fold each card's variants into one set of counts and one cover strip."""
    by_variant: dict[str, list[CoverCandidate]] = {}
    for candidate in candidates:
        by_variant.setdefault(candidate.structural_hash, []).append(candidate)

    figures = []
    for card in cards:
        figure = CardFigures(card=card)
        strip: list[CoverCandidate] = []
        for structural_hash in card.variants:
            seen = activity.get(structural_hash)
            if seen is not None:
                figure.pictures += seen.pictures
                figure.rated += seen.rated
                figure.score_total += seen.score_total
                if seen.last_used is not None and (
                    figure.last_used is None or seen.last_used > figure.last_used
                ):
                    figure.last_used = seen.last_used
            strip.extend(by_variant.get(structural_hash, ()))
        strip.sort(key=_cover_order, reverse=True)
        figure.cover_picture_ids = [c.picture_id for c in strip[:COVER_DEPTH]]
        figures.append(figure)
    return figures


def _rank(figures: list[CardFigures]) -> None:
    """Score every card by the Bayesian mean of its ratings, in place.

    *m* is the library's own mean rating rather than a constant, so a library
    that rates generously is not flattened towards somebody else's idea of
    average. With nothing rated anywhere the prior is zero and the rank falls
    back to being a picture count, which is the only signal there is.
    """
    rated = sum(figure.rated for figure in figures)
    total = sum(figure.score_total for figure in figures)
    mean = (total / rated) if rated else 0.0
    for figure in figures:
        figure.rank = (RANK_PRIOR_WEIGHT * mean + figure.score_total) / (
            RANK_PRIOR_WEIGHT + figure.rated
        )


def _rank_order(figure: CardFigures) -> tuple:
    """Cover rank, then picture count, then the key so ties are stable."""
    return (-figure.rank, -figure.pictures, figure.card.workflow_key)


def effective_stacks(
    figures: list[CardFigures], rows: StackRows
) -> tuple[list[Stack], dict[str, str]]:
    """Group the visible cards: manual assignment, unstacking, then the group.

    Returns the stacks and ``{workflow_key: stack_id}``. A group of one is not
    a stack - the card stands on its own - so the map only names cards that
    genuinely share a tile.
    """
    by_key = {figure.card.workflow_key: figure for figure in figures}
    manual: dict[str, str] = {}
    for stack_id, members in rows.members.items():
        if rows.kinds.get(stack_id) != "manual":
            continue
        for _, key in members:
            if key in by_key:
                manual[key] = stack_id

    # Positions the owner gave inside an automatic group, filed under the core
    # hash the row was written against.
    #
    # **That keying is what ignores a card which has left its group**, and it
    # is why nothing here compares the two. A card groups under its OWN core
    # hash, so a row written when it had another one is looked up in a dict it
    # is not in: it takes cover-rank order like any newcomer, and no stored
    # position can re-admit it or hand it a cover it no longer deserves. A
    # card the owner has since assigned to a manual stack is out of reach for
    # the same reason - a manual group reads `_manual_positions` instead.
    positions: dict[str, dict[str, int]] = {}
    for stack_id, members in rows.members.items():
        if rows.kinds.get(stack_id) != "auto":
            continue
        core = rows.core_hashes.get(stack_id)
        for position, key in members:
            positions.setdefault(core, {})[key] = position

    grouped: dict[tuple[str, str], list[CardFigures]] = {}
    for figure in figures:
        key = figure.card.workflow_key
        if key in manual:
            grouped.setdefault(("manual", manual[key]), []).append(figure)
        elif key in rows.unstacked or figure.card.core_hash is None:
            continue
        else:
            grouped.setdefault(("auto", figure.card.core_hash), []).append(figure)

    stacks, belongs = [], {}
    for (kind, identity), members in sorted(grouped.items()):
        placed = (
            positions.get(identity, {})
            if kind == "auto"
            else _manual_positions(rows, identity)
        )
        # Ordered ones first, in the owner's order; everything that joined
        # since is appended in cover-rank order rather than silently first.
        ordered = sorted(
            (m for m in members if m.card.workflow_key in placed),
            key=lambda m: placed[m.card.workflow_key],
        ) + sorted(
            (m for m in members if m.card.workflow_key not in placed), key=_rank_order
        )
        if len(ordered) < 2:
            continue
        stack_id = identity if kind == "manual" else f"auto:{identity}"
        stacks.append(
            Stack(
                stack_id=stack_id,
                kind=kind,
                member_keys=[m.card.workflow_key for m in ordered],
            )
        )
        for member in ordered:
            member.stack_id = stack_id
            belongs[member.card.workflow_key] = stack_id
    return stacks, belongs


def _manual_positions(rows: StackRows, stack_id: str) -> dict[str, int]:
    return {key: position for position, key in rows.members.get(stack_id, ())}


def describe_differences(
    hub: HubDatabase, figures: list[CardFigures], stacks: list[Stack]
) -> None:
    """Fill in each stacked member's "differs by" chips, and each stack's union.

    Only stacked cards are described: a card on its own has nothing to differ
    from, and reducing every document in the library to answer that would be
    the one expensive thing in the grid.
    """
    by_key = {figure.card.workflow_key: figure for figure in figures}
    wanted = {
        by_key[key].card.variants[0]: key
        for stack in stacks
        for key in stack.member_keys
        if by_key.get(key) and by_key[key].card.variants
    }
    documents = variant_documents(hub, list(wanted))
    for_key = {
        key: documents[structural_hash]
        for structural_hash, key in wanted.items()
        if structural_hash in documents
    }
    for stack in stacks:
        cover = for_key.get(stack.cover_key)
        union: list[str] = []
        for key in stack.member_keys[1:]:
            member = for_key.get(key)
            if cover is None or member is None:
                continue
            try:
                chips = differs_by(cover, member)
            except WorkflowGraphError as exc:
                logger.info(
                    "Card %s cannot be compared with its stack cover %s, so it "
                    "shows no difference chips: %s",
                    key,
                    stack.cover_key,
                    exc,
                )
                continue
            by_key[key].differs_by = chips
            union.extend(chip for chip in chips if chip not in union)
        stack.differs_by = union


def read_grid(hub: HubDatabase, vault) -> Grid:
    """Everything ``GET /workflows/cards`` answers, in two vault queries."""
    cards = card_index(hub)
    activity, candidates = read_card_grid(vault, COVER_DEPTH)
    figures = _figures(cards, activity, candidates)
    _rank(figures)

    hidden = sum(1 for figure in figures if figure.card.hidden)
    visible = [figure for figure in figures if not figure.card.hidden]
    one_offs = sum(1 for figure in visible if figure.one_off)
    visible = [figure for figure in visible if not figure.one_off]

    _apply_chosen_covers(hub, vault, visible)
    stacks, _ = effective_stacks(visible, stack_rows(hub))
    describe_differences(hub, visible, stacks)
    visible.sort(key=_rank_order)
    return Grid(
        cards=visible,
        stacks=stacks,
        one_offs=one_offs,
        hidden=hidden,
        figures=figures,
    )


def _apply_chosen_covers(hub: HubDatabase, vault, figures: list[CardFigures]) -> None:
    """Move the owner's chosen cover to the front of its card's strip.

    One extra query, and only when the owner has actually chosen something: a
    library where nobody has picked a cover pays nothing for the feature.
    """
    library_uuid = getattr(vault, "library_uuid", None)
    if not library_uuid:
        return
    chosen = chosen_covers(hub, library_uuid)
    if not chosen:
        return
    ids = read_chosen_cover_ids(vault, sorted(set(chosen.values())))
    for figure in figures:
        picture_id = ids.get(chosen.get(figure.card.workflow_key, ""))
        if picture_id is None:
            continue
        strip = [picture_id] + [
            other for other in figure.cover_picture_ids if other != picture_id
        ]
        figure.cover_picture_ids = strip[:COVER_DEPTH]


@dataclass(frozen=True)
class Default:
    """One parameter a card starts from, and where the value came from."""

    slot_label: str
    input_name: str
    value: object
    provenance: str


def card_defaults(hub: HubDatabase, vault, card: Card) -> list[Default]:
    """The value each featured parameter most often had, and on which pictures.

    The mode over the instance documents of this card's pictures rated
    ``BEST_SCORE`` and up, falling back to every picture of the card when
    nothing is rated. An owner's override replaces the value and says so.

    Addressed by ``(slot label, input name)`` and never by node id, which is
    what ``workflow_default_override`` is keyed on: node ids are renumbered by
    every re-serialisation, and the same card's variants do not agree about
    them.
    """
    library_uuid = getattr(vault, "library_uuid", None)
    overrides = default_overrides(hub, card.workflow_key)
    if not library_uuid:
        return _overrides_only(overrides)

    provenance = FROM_BEST
    instance_hashes = read_instance_hashes(vault, card.variants, BEST_SCORE)
    if not instance_hashes:
        provenance = FROM_ALL
        instance_hashes = read_instance_hashes(vault, card.variants, None)
    documents = instance_documents(hub, library_uuid, instance_hashes)

    labels = {}
    for structural_hash, document in variant_documents(
        hub, sorted({structural_hash for structural_hash, _ in documents})
    ).items():
        try:
            labels[structural_hash] = topology_node_labels(document)
        except WorkflowGraphError as exc:
            logger.info(
                "Variant %s of card %s will not reduce, so its instances "
                "contribute no default: %s",
                structural_hash,
                card.workflow_key,
                exc,
            )

    seen: dict[tuple[str, str], Counter] = {}
    for structural_hash, document in documents:
        label_of = labels.get(structural_hash)
        if label_of is None:
            continue
        for node_id, node in document.items():
            label = label_of.get(str(node_id))
            if label is None or not isinstance(node, dict):
                continue
            for name, value in (node.get("inputs") or {}).items():
                if name not in FEATURED_NAMES or is_link(value):
                    continue
                if not isinstance(value, (bool, int, float, str)):
                    continue
                seen.setdefault((label, name), Counter())[value] += 1

    defaults = []
    for address in sorted(set(seen) | set(overrides)):
        if address in overrides:
            defaults.append(Default(*address, overrides[address], EDITED))
            continue
        counter = seen[address]
        # Most often, and on a tie the value that sorts first: a mode read off
        # a dict's insertion order would differ between two reads of the same
        # library, which is a card whose defaults move when nothing changed.
        value = max(counter.items(), key=lambda item: (item[1], str(item[0])))[0]
        defaults.append(Default(*address, value, provenance))
    return defaults


def _overrides_only(overrides: dict[tuple[str, str], str]) -> list[Default]:
    """A vault with no library uuid can still say what the owner set."""
    return [
        Default(slot_label, input_name, value, EDITED)
        for (slot_label, input_name), value in sorted(overrides.items())
    ]
