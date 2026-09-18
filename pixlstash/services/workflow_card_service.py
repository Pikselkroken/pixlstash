"""Assembling a workflow CARD out of hub rows and vault counts (v1.12 B3).

**Computed per request, with no aggregate table.** The grid costs two vault
queries - one ``GROUP BY workflow_structural_hash`` and one ``ROW_NUMBER()``
window - and a third only on a library where somebody has actually chosen a
cover. Beside them are eight hub statements, of which one (``card_index``)
scans the variant table and the rest are small; everything else here is
arithmetic over their results. An aggregate table would have to be invalidated by every rating,
every import, every soft delete and every re-run of the card backfill, and
would be a second source of truth for numbers the vault can already produce
inside the frame budget.

Measured on the owner's library (13k kept pictures, 629 variants, 245 cards):
about 75 ms, of which the largest single part is ``describe_differences``
reducing one graph per stacked card.

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
from math import inf
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_card_reads import (
    Card,
    StackRows,
    asset_names,
    card_index,
    chosen_covers,
    default_overrides,
    instance_documents,
    slot_marks,
    stack_rows,
    variant_documents,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import WorkflowGraphError
from pixlstash.services.workflow_identity import (
    RECIPE,
    differs_by_reduced,
    reduce_stored_document,
    topology_node_labels,
)
from pixlstash.services.workflow_library_service import (
    CoverCandidate,
    VariantActivity,
    read_card_grid,
    read_chosen_covers,
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

# How many of a card's newest runs a default is read off. The mode over the
# newest few hundred is the mode, and the read is one stored graph per run.
DEFAULT_SAMPLE = 200

# What a model slot's widget makes it, in the vocabulary `workflowCard.js`
# reads (`kind === "checkpoint"` names the card's one headline model). A widget
# this does not know keeps its own name rather than being called a checkpoint.
_SLOT_KINDS = {
    "ckpt_name": "checkpoint",
    "unet_name": "unet",
    "vae_name": "vae",
    "clip_name": "clip",
    "clip_name1": "clip",
    "clip_name2": "clip",
    "model_name": "upscale",
    "style_model_name": "style",
    "control_net_name": "controlnet",
}

_EPOCH = datetime.min


@dataclass(frozen=True)
class SlotModel:
    """One model a card names: its file, and which slot it sits in."""

    name: Optional[str]
    kind: str
    mark: Optional[str] = None


@dataclass
class CardFigures:
    """One card's counts, its rank and the pictures that would cover it."""

    card: Card
    pictures: int = 0
    rated: int = 0
    score_total: int = 0
    last_used: Optional[datetime] = None
    rank: float = 0.0
    covers: list[CoverCandidate] = field(default_factory=list)
    stack_id: Optional[str] = None
    stack_size: int = 1
    member_keys: list[str] = field(default_factory=list)
    differs_by: list[str] = field(default_factory=list)
    models: list[SlotModel] = field(default_factory=list)
    loras: list[SlotModel] = field(default_factory=list)

    @property
    def rating(self) -> Optional[float]:
        """The mean of the stars this card has, or ``None`` when it has none.

        The PLAIN mean, deliberately, and not :attr:`rank`. The rank is
        smoothed towards the library's average so that cards can be ordered
        against each other; showing it as the card's rating would tell somebody
        their never-rated workflow is rated 4.0.
        """
        return (self.score_total / self.rated) if self.rated else None

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
    """The window's ORDER BY, re-expressed so the per-card pick matches it.

    **NULL sorts below every value, including a negative one**, because that is
    what ``nullslast`` on a descending column does and the two passes have to
    agree. ``smart_score or 0.0`` would not: this repo writes ``-1.0`` into a
    metric whose calculation failed (CLAUDE.md's own convention), so a picture
    whose quality score failed would outrank an unscored one in SQL and lose to
    it here, and a card's cover would depend on which pass last touched it.
    """
    return (
        -inf if candidate.score is None else candidate.score,
        -inf if candidate.smart_score is None else candidate.smart_score,
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
        figure.covers = strip[:COVER_DEPTH]
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
    # Reduced ONCE per card rather than once per comparison. A stack compares
    # every member against one cover, so re-reducing that cover per member is a
    # cost that grows with the stack rather than with the library - and an
    # automatic group is free to be large, since `STRIP_LORAS_FOR_STACKS` is
    # deliberately generous about what stacks together.
    for_key = {}
    for structural_hash, key in wanted.items():
        document = documents.get(structural_hash)
        if document is None:
            continue
        try:
            for_key[key] = reduce_stored_document(document)
        except WorkflowGraphError as exc:
            logger.info(
                "Card %s will not reduce, so it shows no difference chips: %s",
                key,
                exc,
            )
    for stack in stacks:
        cover = for_key.get(stack.cover_key)
        union: list[str] = []
        for key in stack.member_keys[1:]:
            member = for_key.get(key)
            if cover is None or member is None:
                continue
            try:
                chips = differs_by_reduced(cover, member)
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
        # The grid draws ONE card per stack -- the cover, wearing the stack's
        # size and the union of what its members differ by -- so the union has
        # to land on the cover's own figure. The cover has no chips of its own:
        # it is what the others are compared against.
        cover_figure = by_key.get(stack.cover_key)
        if cover_figure is not None:
            cover_figure.differs_by = union


def read_grid(hub: HubDatabase, vault) -> Grid:
    """Everything ``GET /workflows/cards`` answers. See the module docstring
    for what it costs."""
    cards = card_index(hub)
    activity, candidates = read_card_grid(vault, COVER_DEPTH)
    figures = _figures(cards, activity, candidates)
    _rank(figures)

    # Hidden cards and one-offs come out BEFORE the grouping, so a stack is
    # counted as the owner sees it: hiding one member of a pair leaves the
    # other standing alone, with `stack_size` 1. That is the grid telling the
    # truth about what it drew rather than a stack being destroyed - the
    # `workflow_stack_member` rows are untouched and unhiding restores it.
    hidden = sum(1 for figure in figures if figure.card.hidden)
    visible = [figure for figure in figures if not figure.card.hidden]
    one_offs = sum(1 for figure in visible if figure.one_off)
    visible = [figure for figure in visible if not figure.one_off]

    # Over every card and not only the drawn ones: a hidden card still opens
    # on the detail route, and it would otherwise show a cover the owner has
    # already replaced. The same single query either way.
    _apply_chosen_covers(hub, vault, figures)
    stacks, belongs = effective_stacks(visible, stack_rows(hub))
    describe_differences(hub, visible, stacks)
    # Every member, not only the cover. The grid draws the cover alone, so this
    # costs it nothing - but a member opened on its own carries the difference
    # chips it earned against that cover, and a card declaring `stack_size: 1`
    # while carrying "other checkpoint" is telling its reader it differs from
    # something the payload never names. `factChips` reads it exactly that way,
    # showing those chips as plain facts with no "differs by" before them.
    members = by_key(visible)
    for stack in stacks:
        for key in stack.member_keys:
            member = members.get(key)
            if member is not None:
                member.stack_size = len(stack.member_keys)
                member.member_keys = list(stack.member_keys)

    # One card per stack, and it is the cover: `stack_size` is what makes a
    # card a stack to its reader, so a grid that also listed the members would
    # draw each of them twice. The members are still reachable - the cover
    # carries their keys - and each opens on the detail route.
    covered = {key for stack in stacks for key in stack.member_keys[1:]}
    drawn = [figure for figure in visible if figure.card.workflow_key not in covered]
    drawn.sort(key=_rank_order)
    _describe_slots(hub, figures)
    return Grid(
        cards=drawn,
        stacks=stacks,
        one_offs=one_offs,
        hidden=hidden,
        figures=figures,
    )


def _describe_slots(hub: HubDatabase, figures: list[CardFigures]) -> None:
    """Fill in each card's models and LoRAs from the cached slot list.

    Two hub reads for the whole grid and no document reduced: the slot list and
    its marks are what B2 cached per topology precisely so a card read does not
    have to re-derive them, and the readable filenames come from the one table
    that holds them.

    **A recipe LoRA is a slot, not a file.** It is drawn as an anonymous dashed
    chip (`utils/workflowCard.js`), because which character LoRA happened to be
    in it is the recipe's business and not the workflow's - so its name is left
    off here rather than paired to a slot the hub cannot address (see
    :func:`~pixlstash.hub.workflow_card_reads.asset_names`).
    """
    marks = slot_marks(hub, [figure.card.topology_hash for figure in figures])
    names = asset_names(
        hub,
        [figure.card.variants[0] for figure in figures if figure.card.variants],
    )
    for figure in figures:
        card = figure.card
        by_widget: dict[str, list[str]] = {}
        for widget, filename in names.get(
            card.variants[0] if card.variants else "", ()
        ):
            by_widget.setdefault(widget, []).append(filename)
        taken: Counter = Counter()

        def next_name(widget: str) -> Optional[str]:
            found = by_widget.get(widget, ())
            index = taken[widget]
            taken[widget] += 1
            return found[index] if index < len(found) else None

        for slot in card.slots:
            widget = str(slot.get("widget") or "")
            if slot.get("is_lora"):
                mark = marks.get(
                    (card.topology_hash, str(slot.get("label") or "")), RECIPE
                )
                # The name is consumed either way, so a structural LoRA and the
                # recipe slot beside it do not both claim the first filename.
                name = next_name(widget)
                figure.loras.append(
                    SlotModel(
                        name=None if mark == RECIPE else name, kind="lora", mark=mark
                    )
                )
            else:
                figure.models.append(
                    SlotModel(
                        name=next_name(widget),
                        kind=_SLOT_KINDS.get(widget, widget or "model"),
                    )
                )


def by_key(figures: list[CardFigures]) -> dict[str, CardFigures]:
    """``{workflow_key: figures}``, for the joins this module makes in memory."""
    return {figure.card.workflow_key: figure for figure in figures}


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
    pictures = read_chosen_covers(vault, sorted(set(chosen.values())))
    for figure in figures:
        # By the key's own sha, and never by a default: `chosen.get(key, "")`
        # would look an unchosen card up under the empty string, so one row
        # written with an empty `pixel_sha` would cover every card at once.
        pixel_sha = chosen.get(figure.card.workflow_key)
        picked = pictures.get(pixel_sha) if pixel_sha else None
        if picked is None:
            continue
        strip = [picked] + [
            other for other in figure.covers if other.picture_id != picked.picture_id
        ]
        figure.covers = strip[:COVER_DEPTH]


@dataclass(frozen=True)
class Default:
    """One parameter a card starts from, and where the value came from.

    ``label`` is what a reader sees (``utils/workflowCard.js`` keys the ⓘ list
    on it, so it is unique within a card); ``slot_label`` and ``input_name``
    are the address ``workflow_default_override`` is keyed on and are what a
    later write route edits.
    """

    label: str
    slot_label: str
    input_name: str
    value: bool | int | float | str
    provenance: str


def card_defaults(hub: HubDatabase, vault, card: Card) -> list[Default]:
    """The value each featured parameter most often had, and on which pictures.

    The mode over the instance documents of this card's pictures rated
    ``BEST_SCORE`` and up, falling back to every picture of the card when
    nothing is rated. An owner's override replaces the value and says so.

    **Counted per distinct instance, not per picture.** Fifty pictures from one
    same-seed batch ran one instance and vote once between them, which is the
    intended reading: the mode is over settings a person chose, and choosing a
    setting once and generating fifty times is still choosing it once.

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
    instance_hashes = read_instance_hashes(
        vault, card.variants, BEST_SCORE, DEFAULT_SAMPLE
    )
    if not instance_hashes:
        provenance = FROM_ALL
        instance_hashes = read_instance_hashes(
            vault, card.variants, None, DEFAULT_SAMPLE
        )
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
                # The type check is also what rejects a connected input: an
                # API-format link is a two-element list, so it never reaches
                # the counter. No separate `is_link` guard, which would be a
                # branch no input can take and no test could hold.
                if name not in FEATURED_NAMES:
                    continue
                if not isinstance(value, (bool, int, float, str)):
                    continue
                seen.setdefault((label, name), Counter())[value] += 1

    addresses = sorted(set(seen) | set(overrides))
    labels = _labels(addresses)
    defaults = []
    for address in addresses:
        if address in overrides:
            defaults.append(
                Default(labels[address], *address, overrides[address], EDITED)
            )
            continue
        counter = seen[address]
        # Most often, and on a tie the value whose text sorts LAST, which is
        # what `max` over `(count, str(value))` picks: 30 over 20, but 9 over
        # 30, because the tie-break is lexical rather than numeric. Any total
        # order would do - what matters is that it is one, since a mode read
        # off a dict's insertion order would differ between two reads of the
        # same library, which is a card whose defaults move when nothing
        # changed.
        #
        # The counter is keyed on the raw value, so `True` and `1` share a
        # bucket (Python hashes them equal). Both render as the same control
        # and a graph does not mix them on one input, so this is left alone
        # rather than paid for with a type tag on every count.
        value = max(counter.items(), key=lambda item: (item[1], str(item[0])))[0]
        defaults.append(Default(labels[address], *address, value, provenance))
    return defaults


def _labels(addresses: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """A unique, readable label per address: the input name, disambiguated.

    ⓘ keys its list on the label, so two slots offering ``steps`` cannot both
    be called "steps" - that is a duplicate `v-for` key and one row silently
    replaces the other. A second one is numbered rather than named after its
    slot label, which is a 64-character digest and means nothing to a reader.
    """
    seen: Counter = Counter()
    labels = {}
    for address in addresses:
        input_name = address[1]
        seen[input_name] += 1
        labels[address] = (
            input_name if seen[input_name] == 1 else f"{input_name} {seen[input_name]}"
        )
    return labels


def _overrides_only(overrides: dict[tuple[str, str], str]) -> list[Default]:
    """A vault with no library uuid can still say what the owner set."""
    addresses = sorted(overrides)
    labels = _labels(addresses)
    return [
        Default(labels[address], *address, overrides[address], EDITED)
        for address in addresses
    ]
