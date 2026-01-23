from typing import List
from pixlvault.db_models import Picture
from pixlvault.picture_utils import PictureUtils


def picture_order_key(pic: Picture, image_root: str = None):
    """
    Key for ordering pictures in a likeness stack:
    - Higher resolution (width*height) first
    - Higher sharpness first
    - Lower noise_level first
    """
    if not pic.height or not pic.width:
        file_path = PictureUtils.resolve_picture_path(image_root, pic.file_path)
        pic.width, pic.height, _ = PictureUtils.load_metadata(file_path)
    resolution = (pic.width * pic.height) if pic.width and pic.height else 0

    quality = pic.quality
    sharp = quality.sharpness if quality and quality.sharpness is not None else 0.0
    noise = quality.noise_level if quality and quality.noise_level is not None else 1.0

    return (-resolution, -sharp, noise)


def order_stack_pictures(
    pictures: List[Picture], image_root: str = None
) -> List[Picture]:
    """
    Return pictures sorted by best-to-worst (resolution, sharpness, noise).
    """
    return sorted(pictures, key=lambda pic: picture_order_key(pic, image_root))
