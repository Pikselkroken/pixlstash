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


def combined_picture_face_likeness(pic_a, pic_b, face_likeness_lookup):
    """
    Compute combined likeness between two pictures by summing face-likeness for each matching face index,
    then dividing by the maximum number of faces in either picture.
    - face_likeness_lookup(face_a, face_b) should return likeness score (float) or None.
    """
    faces_a = {
        f.face_index: f for f in getattr(pic_a, "faces", []) if f.face_index != -1
    }
    faces_b = {
        f.face_index: f for f in getattr(pic_b, "faces", []) if f.face_index != -1
    }
    if not faces_a and not faces_b:
        return None  # No faces in either picture
    max_faces = max(len(faces_a), len(faces_b))
    if max_faces == 0:
        return None
    total_likeness = 0.0
    count = 0
    for idx in set(faces_a.keys()) & set(faces_b.keys()):
        likeness = face_likeness_lookup(faces_a[idx], faces_b[idx])
        if likeness is not None:
            total_likeness += likeness
        count += 1
    # Penalize for missing faces by dividing by max_faces
    return total_likeness / max_faces if max_faces > 0 else None
