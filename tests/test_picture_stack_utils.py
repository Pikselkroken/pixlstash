from pixlvault.db_models import Picture
from pixlvault.db_models.quality import Quality
from pixlvault.picture_stack_utils import order_stack_pictures


def test_order_stack_pictures_basic():
    pics = [
        Picture(
            file_path="a.jpg",
            width=100,
            height=100,
            quality=Quality(sharpness=0.5, noise_level=0.2),
        ),
        Picture(
            file_path="b.jpg",
            width=200,
            height=200,
            quality=Quality(sharpness=0.3, noise_level=0.1),
        ),
        Picture(
            file_path="c.jpg",
            width=100,
            height=100,
            quality=Quality(sharpness=0.9, noise_level=0.5),
        ),
    ]

    ordered = order_stack_pictures(pics)
    assert ordered[0].file_path == "b.jpg"
    assert ordered[1].file_path == "c.jpg"
    assert ordered[2].file_path == "a.jpg"


def test_order_stack_pictures_tiebreak():
    pics = [
        Picture(
            file_path="x.jpg",
            width=100,
            height=100,
            quality=Quality(sharpness=0.5, noise_level=0.2),
        ),
        Picture(
            file_path="y.jpg",
            width=100,
            height=100,
            quality=Quality(sharpness=0.5, noise_level=0.1),
        ),
        Picture(
            file_path="z.jpg",
            width=100,
            height=100,
            quality=Quality(sharpness=0.5, noise_level=0.3),
        ),
    ]
    ordered = order_stack_pictures(pics)
    assert ordered[0].file_path == "y.jpg"
    assert ordered[1].file_path == "x.jpg"
    assert ordered[2].file_path == "z.jpg"
