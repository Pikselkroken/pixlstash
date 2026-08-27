#!/usr/bin/env python3
"""export_plausible_user_library.py - Copy a real PixlStash library out as a
folder a human could plausibly have organized by hand, for testing reference
folders against realistic data instead of a clean synthetic fixture.

Reads an existing library's ``vault.db`` **read-only** (plain SELECTs, no
migrations, no writes) and copies each picture's real bytes into a folder tree
using the picture's own ``original_file_name`` where one was recorded. Safe to
run against a library a server is currently using.

``--organize-by`` picks the folder scheme:

* ``date`` (default): ``<year>/<year-month-day>/<name>``, the same information
  a camera or phone would have given the file in the first place.
* ``people``: one folder per tagged Character, ``<Name>/<name>``. A picture
  with no tagged face falls back to the date scheme, same as a real owner who
  only got around to naming some of the faces.
* ``sets``: one folder per PictureSet, the same fallback for a picture in no
  set.
* ``mixed``: people first, then sets, for whichever picture each already
  belongs to; the rest fall back to date. Whenever the person or set has a
  Project, its folder is nested one level under the project's own name -
  since only some real characters/sets are actually in a project, this alone
  produces a library where some things sit inside a project folder and others
  don't, without any extra flag for it.

With ``--messiness`` above 0, some pictures get a plausible human mistake
instead of the tidy path: dumped in a flat "Camera Uploads" folder, filed
under the wrong month, renamed to something generic like "IMG_0001.jpg",
nested inside a stray "New folder", or copied a second time as an accidental
duplicate. ``--seed`` makes a given ``--messiness`` reproducible.

Usage:
    python scripts/export_plausible_user_library.py \\
        ~/.config/pixlstash/images /tmp/messy-user-library --messiness 0.15
    python scripts/export_plausible_user_library.py \\
        ~/.config/pixlstash/images /tmp/people-library --organize-by people
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
from datetime import timedelta
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session, create_engine, select

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pixlstash.db_models.entity_project import (  # noqa: E402
    CharacterProjectMember,
    PictureSetProjectMember,
)
from pixlstash.db_models.picture import Picture  # noqa: E402
from pixlstash.db_models.project import Project  # noqa: E402
from pixlstash.services import views_service  # noqa: E402
from pixlstash.services.views_service import safe_component  # noqa: E402
from pixlstash.utils.image_processing.image_utils import ImageUtils  # noqa: E402

#: Which project-membership join table backs each collect_in_session() kind.
_PROJECT_MEMBER_BY_KIND = {
    "people": (CharacterProjectMember, CharacterProjectMember.character_id),
    "sets": (PictureSetProjectMember, PictureSetProjectMember.set_id),
}

#: --organize-by value -> the kinds it draws folders from, in claim order
#: (a picture in more than one gets the first kind's folder).
_ORGANIZE_KINDS = {
    "date": (),
    "people": ("people",),
    "sets": ("sets",),
    "mixed": ("people", "sets"),
}

# Filenames a careless human actually reuses, over and over, across folders.
_GENERIC_NAMES = (
    "IMG_0001",
    "IMG_0002",
    "Photo",
    "Photo (1)",
    "New Doc",
    "download",
    "Screenshot",
    "image",
)
_STRAY_FOLDER_NAMES = ("New folder", "New folder (2)", "backup", "old", "sorted")


def _dated_folder(created_at, rng: Optional[random.Random] = None) -> tuple[str, ...]:
    if created_at is None:
        return ("Unsorted",)
    if rng is not None:
        created_at = created_at + timedelta(days=rng.randint(-75, 75))
    return (f"{created_at.year:04d}", created_at.strftime("%Y-%m-%d"))


def _quirk_flat_dump(folder, filename, rng, created_at):
    return ("Camera Uploads",), filename


def _quirk_wrong_date(folder, filename, rng, created_at):
    return _dated_folder(created_at, rng), filename


def _quirk_generic_rename(folder, filename, rng, created_at):
    ext = os.path.splitext(filename)[1]
    return folder, f"{rng.choice(_GENERIC_NAMES)}{ext}"


def _quirk_stray_subfolder(folder, filename, rng, created_at):
    return folder + (rng.choice(_STRAY_FOLDER_NAMES),), filename


def _quirk_doubled_folder(folder, filename, rng, created_at):
    return (folder + (folder[-1],) if folder else folder), filename


_QUIRKS: list[Callable] = [
    _quirk_flat_dump,
    _quirk_wrong_date,
    _quirk_generic_rename,
    _quirk_stray_subfolder,
    _quirk_doubled_folder,
]


def _fallback_name(picture_id: int, source: str) -> str:
    return f"IMG_{picture_id:05d}{os.path.splitext(source)[1]}"


def _unique_path(dest_dir: Path, filename: str, taken: set[str]) -> Path:
    stem, ext = os.path.splitext(filename)
    candidate = filename
    n = 2
    while candidate.lower() in taken:
        candidate = f"{stem} ({n}){ext}"
        n += 1
    taken.add(candidate.lower())
    return dest_dir / candidate


def _entity_folders(
    session: Session, kinds: tuple[str, ...]
) -> dict[int, tuple[str, ...]]:
    """Return ``{picture_id: folder_parts}`` for every picture an entity of
    *kinds* claims, nested under that entity's Project when it has one.

    Reuses ``views_service.collect_in_session`` - the same grouping the Views
    feature already does - rather than re-deriving it. First kind, then first
    entity within it, wins a picture that belongs to more than one; the
    fallback for everything unclaimed is the caller's job.
    """
    collected = views_service.collect_in_session(session, kinds)
    project_by_entity: dict[tuple[str, int], Optional[str]] = {}
    claimed: dict[int, tuple[str, ...]] = {}
    for kind in kinds:
        member_model, id_col = _PROJECT_MEMBER_BY_KIND[kind]
        for entity_id, name, pictures in collected.get(kind, []):
            key = (kind, entity_id)
            if key not in project_by_entity:
                project_by_entity[key] = session.exec(
                    select(Project.name)
                    .join(member_model, member_model.project_id == Project.id)
                    .where(id_col == entity_id)
                ).first()
            project_name = project_by_entity[key]
            entity_folder = safe_component(name, f"{kind} {entity_id}")
            parts = (
                (safe_component(project_name, entity_folder), entity_folder)
                if project_name
                else (entity_folder,)
            )
            for picture in pictures:
                claimed.setdefault(picture.id, parts)
    return claimed


def export_library(
    source_root: Path,
    dest_root: Path,
    messiness: float,
    seed: int,
    limit: Optional[int],
    organize_by: str = "date",
) -> dict:
    db_path = source_root / "vault.db"
    if not db_path.is_file():
        raise SystemExit(f"No vault.db in {source_root}")

    rng = random.Random(seed)
    engine = create_engine(f"sqlite:///{db_path}")
    stats = {"copied": 0, "duplicated": 0, "messy": 0, "skipped_missing": 0}
    taken_by_dir: dict[Path, set[str]] = {}

    with Session(engine) as session:
        query = select(Picture).where(Picture.deleted == False)  # noqa: E712
        if limit:
            query = query.limit(limit)
        pictures = session.exec(query).all()
        entity_folders = _entity_folders(session, _ORGANIZE_KINDS[organize_by])

    for picture in pictures:
        source = ImageUtils.resolve_picture_path(str(source_root), picture.file_path)
        if not source or not os.path.isfile(source):
            stats["skipped_missing"] += 1
            continue

        folder = entity_folders.get(picture.id) or _dated_folder(picture.created_at)
        name = picture.original_file_name or _fallback_name(picture.id, source)
        name = safe_component(name, _fallback_name(picture.id, source))

        messy = messiness > 0 and rng.random() < messiness
        if messy:
            quirk = rng.choice(_QUIRKS)
            folder, name = quirk(folder, name, rng, picture.created_at)
            stats["messy"] += 1

        dest_dir = dest_root.joinpath(*folder)
        dest_dir.mkdir(parents=True, exist_ok=True)
        taken = taken_by_dir.setdefault(dest_dir, set())
        dest_path = _unique_path(dest_dir, name, taken)
        shutil.copy2(source, dest_path)
        stats["copied"] += 1

        # A real duplicate import: the same file, copied again under a second
        # plausible name. Independent of, and rarer than, the other quirks.
        if messiness > 0 and rng.random() < messiness / 3:
            dup_dir = dest_root / "Camera Uploads"
            dup_dir.mkdir(parents=True, exist_ok=True)
            dup_taken = taken_by_dir.setdefault(dup_dir, set())
            dup_path = _unique_path(dup_dir, name, dup_taken)
            shutil.copy2(source, dup_path)
            stats["duplicated"] += 1

    return stats


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "source", type=Path, help="Existing library root (holds vault.db)."
    )
    parser.add_argument(
        "dest", type=Path, help="Folder to write the plausible library into."
    )
    parser.add_argument(
        "--messiness",
        type=float,
        default=0.0,
        help="Probability [0-1] per picture of a human-organization mistake. Default 0 (tidy).",
    )
    parser.add_argument("--seed", type=int, default=0, help="Reproducibility seed.")
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap the number of pictures."
    )
    parser.add_argument(
        "--organize-by",
        choices=sorted(_ORGANIZE_KINDS),
        default="date",
        help="Folder scheme: date (default), people, sets, or mixed.",
    )
    args = parser.parse_args(argv)

    if not 0.0 <= args.messiness <= 1.0:
        parser.error("--messiness must be between 0 and 1")

    stats = export_library(
        args.source, args.dest, args.messiness, args.seed, args.limit, args.organize_by
    )
    print(f"copied:           {stats['copied']}")
    print(f"  with a mistake: {stats['messy']}")
    print(f"  duplicated:     {stats['duplicated']}")
    print(f"  skipped (missing on disk): {stats['skipped_missing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
