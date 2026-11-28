"""The Studio's scene shots (#2819): what one shoot of a scene sends.

A shot is a prompt and the reference pictures it names, written from the
scene: the location's view the camera takes, each cast member in their outfit
doing what the scene says, the props in their state, and what the location is
doing. The run itself is ``POST /workflows/run`` with the workflow the owner
picks; the shot fills its prompt and its picture inputs, in the order given.

Angles follow the sheets (#2817): a location's panels are Wide, Reverse, Left,
Right and an outfit's or prop's Front, ¾, Side, Back, cut and imported in that
order, so a set's members by ascending id are its angles.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlmodel import Session, select

from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
)
from pixlstash.db_models.adapter_attachment import (
    ENTITY_CHARACTER,
    AdapterAttachment,
)
from pixlstash.db_models.character_reference import CharacterReferenceMember
from pixlstash.db_models.studio import Scene
from pixlstash.pixl_logging import get_logger
from pixlstash.services.character_sheet import PANEL_COUNT
from pixlstash.training.dataset_picker import face_room, framing_label

logger = get_logger(__name__)

LOCATION_VIEWS = ("Wide", "Reverse", "Left", "Right")
SHOTS = {
    "wide": "A wide shot",
    "medium": "A medium shot",
    "close": "A close-up",
}
NEGATIVE = "blurry, distorted faces, extra people, extra limbs, text, watermark"


@dataclass
class Item:
    """A set the Studio uses: its name, its description and its angles."""

    name: str
    description: str = ""
    picture_ids: list[int] = field(default_factory=list)


@dataclass
class Person:
    """A cast member as a shot sees them."""

    name: str
    description: str = ""
    reference_ids: list[int] = field(default_factory=list)
    loras: list[str] = field(default_factory=list)


def angles(session: Session, set_id: int | None) -> list[int]:
    """The set's members in angle order (ascending id), or none."""
    if set_id is None:
        return []
    return list(
        session.exec(
            select(PictureSetMember.picture_id)
            .where(PictureSetMember.set_id == set_id)
            .order_by(PictureSetMember.picture_id)
        ).all()
    )


def item(session: Session, set_id: int | None) -> Item | None:
    """The set *set_id* as an item, or None when it is gone."""
    if set_id is None:
        return None
    found = session.get(PictureSet, set_id)
    if found is None:
        return None
    return Item(
        name=found.name,
        description=(found.description or "").strip(),
        picture_ids=angles(session, set_id),
    )


def person(session: Session, character_id: int) -> Person | None:
    """Who the cast member is: words, reference pictures and LoRAs."""
    found = session.get(Character, character_id)
    if found is None:
        return None
    refs = list(
        session.exec(
            select(CharacterReferenceMember.picture_id)
            .where(CharacterReferenceMember.character_id == character_id)
            .order_by(CharacterReferenceMember.picture_id)
        ).all()
    )
    if not refs and found.thumbnail_picture_id:
        refs = [found.thumbnail_picture_id]
    loras = list(
        session.exec(
            select(AdapterAttachment.adapter_sha256).where(
                AdapterAttachment.entity_type == ENTITY_CHARACTER,
                AdapterAttachment.entity_id == character_id,
            )
        ).all()
    )
    return Person(
        name=found.name,
        description=(found.description or "").strip(),
        reference_ids=refs,
        loras=loras,
    )


def place_words(box) -> str:
    """Where a box stands, in words: left, centre or right, near or far."""
    try:
        x, _y, w, h = (float(v) for v in box)
    except (TypeError, ValueError):
        return ""
    centre = x + w / 2
    side = (
        "on the left"
        if centre < 0.36
        else "on the right"
        if centre > 0.64
        else "in the centre"
    )
    depth = " in the foreground" if h > 0.6 else " in the background" if h < 0.3 else ""
    return side + depth


def _angle(pose: str, view: int) -> int:
    """Which of an outfit's or prop's angles the camera sees."""
    if view == 1:
        return 3  # the Reverse view sees the Back
    words = (pose or "").lower()
    if "¾" in words or "three-quarter" in words or "3/4" in words:
        return 1
    if "side" in words or "profile" in words:
        return 2
    return 0


def _pick(ids: list[int], index: int) -> int | None:
    if not ids:
        return None
    return ids[index] if index < len(ids) else ids[0]


def compose_shot(
    scene: Scene,
    *,
    location: Item | None,
    people: dict[int, Person],
    outfits: dict[int, Item],
    props: dict[int, Item],
    kind: str = "still",
    from_picture_id: int | None = None,
    max_references: int = 8,
) -> dict:
    """The prompt and the reference pictures for one shot of *scene*.

    Each reference is named in the prompt by its number ("picture 2"), so only
    as many as the workflow takes (*max_references*) are named; past that the
    words alone carry the item. A clip from a still has the still as its one
    reference: it already holds everyone where they stand.
    """
    camera = scene.camera or {}
    view = int(camera.get("view") or 0)
    if view not in range(len(LOCATION_VIEWS)):
        view = 0
    setting = scene.setting or {}
    references: list[int] = []
    loras: list[str] = []

    def cite(picture_id: int | None) -> str:
        if picture_id is None or from_picture_id is not None:
            return ""
        if picture_id in references:
            return f" (picture {references.index(picture_id) + 1})"
        if len(references) >= max_references:
            return ""
        references.append(picture_id)
        return f" (picture {len(references)})"

    if from_picture_id is not None:
        references.append(from_picture_id)
    lines: list[str] = []
    opening = SHOTS.get(camera.get("shot") or "medium", SHOTS["medium"])
    if kind == "clip":
        opening = (
            "Bring picture 1 to life as a short video clip"
            if from_picture_id is not None
            else f"A short video clip, {opening[0].lower()}{opening[1:]}"
        )
    if location is not None:
        where = location.description or location.name
        cited = cite(_pick(location.picture_ids, view))
        side = (
            "" if view == 0 else f", seen from its {LOCATION_VIEWS[view].lower()} view"
        )
        lines.append(f"{opening} in {where}{cited}{side}.")
    else:
        lines.append(f"{opening}.")
    conditions = [
        setting.get(key, "").strip()
        for key in ("weather", "time")
        if (setting.get(key) or "").strip()
    ]
    if conditions:
        lines.append(f"{', '.join(conditions).capitalize()}.")
    if (setting.get("happening") or "").strip():
        lines.append(f"Around them: {setting['happening'].strip()}.")
    if (setting.get("light") or "").strip():
        lines.append(f"Light on the people: {setting['light'].strip()}.")

    cast = [m for m in (scene.cast or []) if m.get("character_id") in people]
    if cast:
        count = "One person" if len(cast) == 1 else f"{len(cast)} people"
        lines.append(
            f"{count}, each a different person, everyone fully in the picture."
        )
    names: dict[int, str] = {}
    for member in cast:
        who = people[member["character_id"]]
        names[member["character_id"]] = who.name
        brings = member.get("brings") or "refs"
        parts = [who.name]
        place = place_words(member.get("box"))
        if place:
            parts[0] = f"{place.capitalize()}, {who.name}"
        if who.description:
            parts.append(who.description)
        if brings == "refs":
            for ref in who.reference_ids[:1]:
                parts[-1] += cite(ref).replace("(picture", "(the person in picture")
        elif brings == "lora":
            loras.extend(sha for sha in who.loras if sha not in loras)
        outfit = outfits.get(member.get("outfit_set_id"))
        if outfit is not None:
            wear = outfit.description or outfit.name
            pick = _pick(outfit.picture_ids, _angle(member.get("pose"), view))
            parts.append(f"wearing {wear}{cite(pick)}")
        if (member.get("doing") or "").strip():
            parts.append(member["doing"].strip())
        line = ", ".join(parts) + "."
        if (member.get("pose") or "").strip():
            line += f" Pose: {member['pose'].strip()}."
        lines.append(line)

    for entry in scene.props or []:
        prop = props.get(entry.get("set_id"))
        if prop is None:
            continue
        what = prop.description or prop.name
        pick = _pick(prop.picture_ids, _angle(entry.get("state"), view))
        line = f"{what[0].upper()}{what[1:]}{cite(pick)}"
        if (entry.get("state") or "").strip():
            line += f", {entry['state'].strip()}"
        holder = names.get(entry.get("held_by"))
        if holder:
            line += f", held by {holder}"
        else:
            place = place_words(entry.get("box"))
            if place:
                line += f", {place}"
        lines.append(line + ".")

    if kind == "clip":
        lines.append(
            "Everyone keeps their face, hair and clothes from start to end; "
            "the place stays the same place."
        )
    return {
        "prompt": " ".join(lines),
        "negative": NEGATIVE,
        "references": references,
        "loras": loras,
    }


def shot_for(
    session: Session,
    scene: Scene,
    *,
    kind: str = "still",
    from_picture_id: int | None = None,
    max_references: int = 8,
) -> dict:
    """``compose_shot`` with the scene's location, cast, outfits and props read."""
    people: dict[int, Person] = {}
    outfits: dict[int, Item] = {}
    for member in scene.cast or []:
        cid = member.get("character_id")
        if cid is not None and cid not in people:
            found = person(session, cid)
            if found is not None:
                people[cid] = found
        oid = member.get("outfit_set_id")
        if oid is not None and oid not in outfits:
            found_outfit = item(session, oid)
            if found_outfit is not None:
                outfits[oid] = found_outfit
    props = {
        entry["set_id"]: found
        for entry in scene.props or []
        if entry.get("set_id") is not None
        and (found := item(session, entry["set_id"])) is not None
    }
    return compose_shot(
        scene,
        location=item(session, scene.location_set_id),
        people=people,
        outfits=outfits,
        props=props,
        kind=kind,
        from_picture_id=from_picture_id,
        max_references=max_references,
    )


# ── a person's sheet tile ───────────────────────────────────────────────────

HEADSHOT_SLOTS = 4
BODY_SLOTS = 2
_HEADSHOT_FRAMINGS = ("close-up", "portrait")
_BODY_FRAMINGS = ("upper body", "full body")


@dataclass
class TilePicture:
    """One of a person's pictures, as their sheet tile weighs it."""

    picture_id: int
    framing: str | None = None
    source_picture_id: int | None = None
    stack_id: int | None = None
    rating: int | None = None
    smart_score: float | None = None


def tile_framing(
    box: tuple[float, float, float, float] | None,
    width: int | None,
    height: int | None,
    orientation: int | None,
) -> str | None:
    """The framing the training-set picker reads off the person's face box."""
    if box is None:
        return None
    return framing_label(set(), face_room(box, width, height, orientation))


def sheet_tile(
    pictures: list[TilePicture], thumbnail_id: int | None = None
) -> tuple[list[int], list[int]]:
    """A person's Studio card: up to four headshots and two body shots.

    The chosen thumbnail is the first headshot. A drawn character sheet's six
    panels (pictures cut from one source, passport photos first) fill the
    slots next, the newest sheet's. Then close-ups and portraits fill the
    headshots and upper and full body the body shots, best rated first, one
    picture per stack. A picture whose framing is unknown, or that its own
    kind has no room for, fills whatever slot is still empty, so the card has
    no hole while the person has pictures left.
    """

    def rank(p: TilePicture):
        smart = p.smart_score if p.smart_score is not None else float("-inf")
        return (p.picture_id != thumbnail_id, -(p.rating or 0), -smart, -p.picture_id)

    # Panels are found before stacks thin the list: a stacked panel is still
    # one of its sheet's six.
    cuts: dict[int, list[int]] = {}
    for p in pictures:
        if p.source_picture_id is not None:
            cuts.setdefault(p.source_picture_id, []).append(p.picture_id)
    sheets = [sorted(ids) for ids in cuts.values() if len(ids) == PANEL_COUNT]
    # The newest sheet: panels are imported as it is cut, so its ids are highest.
    panels = max(sheets, default=[], key=lambda ids: ids[0])

    ranked, stacks = [], set()
    for p in sorted(pictures, key=rank):
        if p.stack_id is not None:
            if p.stack_id in stacks:
                continue
            stacks.add(p.stack_id)
        ranked.append(p)

    heads = [p.picture_id for p in ranked[:1] if p.picture_id == thumbnail_id]
    heads += panels[:HEADSHOT_SLOTS]
    heads += [p.picture_id for p in ranked if p.framing in _HEADSHOT_FRAMINGS]
    bodies = panels[HEADSHOT_SLOTS:]
    bodies += [p.picture_id for p in ranked if p.framing in _BODY_FRAMINGS]
    heads = list(dict.fromkeys(heads))[:HEADSHOT_SLOTS]
    bodies = [pid for pid in dict.fromkeys(bodies) if pid not in heads][:BODY_SLOTS]
    taken = set(heads) | set(bodies)
    for p in ranked:
        if p.picture_id in taken:
            continue
        if len(heads) < HEADSHOT_SLOTS:
            heads.append(p.picture_id)
        elif len(bodies) < BODY_SLOTS:
            bodies.append(p.picture_id)
        else:
            break
    return heads, bodies


def sheet_tiles(
    session: Session, people: list[Character]
) -> dict[int, tuple[list[int], list[int], int]]:
    """Each person's :func:`sheet_tile` and how many pictures they are in.

    One read of every face the people have: a picture's framing is measured
    on the person's largest face in it.
    """
    if not people:
        return {}
    rows = session.exec(
        select(
            Face.character_id,
            Face.bbox_,
            Picture.id,
            Picture.width,
            Picture.height,
            Picture.orientation,
            Picture.source_picture_id,
            Picture.stack_id,
            Picture.score,
            Picture.smart_score,
        )
        .join(Picture, Face.picture_id == Picture.id)
        .where(
            Face.character_id.in_([c.id for c in people]),
            Picture.deleted.is_(False),
        )
    ).all()
    found: dict[int, dict[int, tuple[float, TilePicture]]] = {}
    for cid, bbox, pid, w, h, orientation, source, stack, score, smart in rows:
        box, area = None, -1.0
        try:
            x1, y1, x2, y2 = (
                (float(v) for v in json.loads(bbox)[:4]) if bbox else (0, 0, 0, 0)
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Unreadable face box %r on picture %s (person %s); its framing "
                "is unknown on their Studio card: %s",
                bbox,
                pid,
                cid,
                exc,
            )
            x1 = y1 = x2 = y2 = 0
        if x2 > x1 and y2 > y1:
            box, area = (x1, y1, x2, y2), (x2 - x1) * (y2 - y1)
        mine = found.setdefault(cid, {})
        if pid in mine and mine[pid][0] >= area:
            continue
        mine[pid] = (
            area,
            TilePicture(
                picture_id=pid,
                framing=tile_framing(box, w, h, orientation),
                source_picture_id=source,
                stack_id=stack,
                rating=score,
                smart_score=smart,
            ),
        )
    tiles = {}
    for person in people:
        pictures = [p for _area, p in found.get(person.id, {}).values()]
        heads, bodies = sheet_tile(pictures, person.thumbnail_picture_id)
        tiles[person.id] = (heads, bodies, len(pictures))
    return tiles
