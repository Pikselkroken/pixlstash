from .picture import PictureModel
from typing import Union, Tuple

"""
Tools for converting between PictureModel objects and database dicts.
"""


@staticmethod
def from_db_dicts(
    picture_dicts: Union[dict, list[dict]],
    tag_dicts: Union[list[dict], list[list[dict]]],
):
    """
    Convert dicts from DB rows to a PictureModel object
    """
    pic = PictureModel.from_dict(picture_dicts)
    pic.tags = [tag_dict["tag"] for tag_dict in tag_dicts]

    return pic


@staticmethod
def from_batch_of_db_dicts(
    picture_dicts: Union[dict, list[dict]],
    tag_dicts: Union[list[dict], list[list[dict]]] = None,
):
    """
    Convert list of dicts from DB rows to a list of PictureModel objects
    """

    if not tag_dicts:
        tag_dicts = [[] for _ in picture_dicts]
    return [from_db_dicts(p, t) for p, t in zip(picture_dicts, tag_dicts)]


@staticmethod
def to_db_dicts(pic: PictureModel) -> Tuple[dict, list[dict]]:
    """
    Convert PictureModel to dicts suitable for DB insertion.
    Supports single model.
    Returns tuple of (picture_dict, list[tag_dict]).
    """
    pic = pic
    tags = pic.tags if hasattr(pic, "tags") and pic.tags is not None else []
    tag_dicts = [{"picture_id": pic.id, "tag": tag} for tag in tags]
    picture_dict = {}
    for key, value in pic.to_dict().items():
        if key in ("tags", "character_ids"):
            continue
        picture_dict[key] = value
    return picture_dict, tag_dicts


@staticmethod
def to_batch_of_db_dicts(
    pics: list[PictureModel],
) -> Tuple[list[dict], list[list[dict]]]:
    """
    Convert PictureModels to dicts suitable for DB insertion.
    Supports a list of models.
    Returns tuple of (list[picture_dict], list[list[tag_dict]]).
    """

    if isinstance(pics, list):
        picture_dicts = []
        list_of_tag_dicts = []
        for pic in pics:
            pic_dict, tag_dicts = to_db_dicts(pic)
            picture_dicts.append(pic_dict)
            list_of_tag_dicts.append(tag_dicts)
        return picture_dicts, list_of_tag_dicts
