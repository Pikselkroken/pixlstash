from typing import List
from pixlvault.picture import PictureModel
from pixlvault.picture_utils import PictureUtils


def picture_order_key(pic: PictureModel):
    """
    Key for ordering pictures in a likeness stack:
    - Higher resolution (width*height) first
    - Higher sharpness first
    - Lower noise_level first
    """
    if not pic.height or not pic.width:
        pic.width, pic.height, _ = PictureUtils.load_metadata(pic.file_path)
    resolution = (pic.width * pic.height) if pic.width and pic.height else 0
    sharp = getattr(pic, "sharpness", -1)
    if isinstance(sharp, bytes):
        try:
            sharp = float(sharp.decode())
        except Exception:
            sharp = -1.0
    noise = getattr(pic, "noise_level", 1e9)
    # Sort by highest resolution, highest sharpness, lowest noise
    return (-resolution, -sharp, noise)


def order_stack_pictures(pictures: List[PictureModel]) -> List[PictureModel]:
    """
    Return pictures sorted by best-to-worst (resolution, sharpness, noise).
    """
    return sorted(pictures, key=picture_order_key)
