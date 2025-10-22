import os
from pixelurgy_vault.picture_tagger import PictureTagger


def test_picture_tagger_on_directory():
    """
    Test PictureTagger on pictures/ directory.
    """
    img_path = os.path.join(os.path.dirname(__file__), "../pictures/TaggerTest.png")
    assert os.path.exists(img_path), f"Training directory not found: {img_path}"
    tagger = PictureTagger()
    tags = tagger.tag_images(image_paths=[img_path])
    print("Tags returned:", tags)
