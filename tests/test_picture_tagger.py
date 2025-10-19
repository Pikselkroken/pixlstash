
import os
import pytest
from pixelurgy_vault.picture_iteration import PictureIteration
from pixelurgy_vault.picture_tagger import PictureTagger

def test_picture_tagger_on_directory():
    """
    Test PictureTagger on pictures/ directory.
    """
    img_path = os.path.join(os.path.dirname(__file__), "../pictures")
    assert os.path.exists(img_path), f"Training directory not found: {img_path}"
    tagger = PictureTagger()
    tags = tagger.tag_training_directory(train_data_dir=img_path)
    print("Tags returned:", tags)
