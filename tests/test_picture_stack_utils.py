from pixlvault.picture import PictureModel
from pixlvault.picture_stack_utils import order_stack_pictures


class DummyPic(PictureModel):
    def __init__(self, file_path, width, height, sharpness, noise_level):
        super().__init__(file_path=file_path)
        self.width = width
        self.height = height
        self.sharpness = sharpness
        self.noise_level = noise_level

    def to_dict(self):
        return {
            "file_path": self.file_path,
            "width": self.width,
            "height": self.height,
            "sharpness": self.sharpness,
            "noise_level": self.noise_level,
        }


def test_order_stack_pictures_basic():
    pics = [
        DummyPic("a.jpg", 100, 100, 0.5, 0.2),
        DummyPic("b.jpg", 200, 200, 0.3, 0.1),
        DummyPic("c.jpg", 100, 100, 0.9, 0.5),
    ]
    ordered = order_stack_pictures(pics)
    # Highest resolution first, then sharpness, then lowest noise
    assert ordered[0].file_path == "b.jpg"
    assert ordered[1].file_path == "c.jpg"
    assert ordered[2].file_path == "a.jpg"


def test_order_stack_pictures_tiebreak():
    pics = [
        DummyPic("x.jpg", 100, 100, 0.5, 0.2),
        DummyPic("y.jpg", 100, 100, 0.5, 0.1),
        DummyPic("z.jpg", 100, 100, 0.5, 0.3),
    ]
    ordered = order_stack_pictures(pics)
    # All resolution/sharpness equal, lowest noise first
    assert ordered[0].file_path == "y.jpg"
    assert ordered[1].file_path == "x.jpg"
    assert ordered[2].file_path == "z.jpg"
