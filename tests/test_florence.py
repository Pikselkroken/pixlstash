#!/usr/bin/env python3
"""
Test suite for Florence-2 captioning functionality.
Tests caption generation, performance, and error handling.
"""

import os
import pytest
import time
from pathlib import Path
from pixlvault.picture_tagger import PictureTagger

MAX_TEST_IMAGES = 3 if os.getenv("GITHUB_ACTIONS") == "true" else 50


@pytest.fixture(scope="module")
def tagger(request):
    """Create a PictureTagger instance with Florence-2 enabled."""

    tagger = PictureTagger()

    tagger._init_florence_captioning()

    return tagger


@pytest.fixture(scope="module")
def image_files():
    """Get test image files from the pictures/ directory."""
    # Use built-in test images in pictures/ directory
    test_dir = Path(__file__).parent.parent / "pictures"

    if not test_dir.is_dir():
        pytest.fail(f"Test images directory not found: {test_dir}")

    # Find image files
    image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    files = [
        str(f)
        for f in test_dir.iterdir()
        if f.is_file() and f.suffix.lower() in image_extensions
    ]

    if not files:
        pytest.fail(f"No image files found in {test_dir}")

    return files


def test_florence_caption_generation(tagger, image_files):
    """Test that Florence-2 can generate captions for multiple images."""
    success_count = 0
    fail_count = 0
    captions = []

    dataset = image_files[:MAX_TEST_IMAGES]

    for image_path in dataset:
        caption = tagger._generate_florence_caption(image_path)

        if caption:
            success_count += 1
            captions.append(caption)
        else:
            fail_count += 1

    # Assert at least 90% success rate
    success_rate = success_count / len(dataset)
    assert success_rate >= 0.9, f"Success rate {success_rate:.1%} is below 90%"

    # Assert captions are non-empty strings
    assert all(isinstance(c, str) and len(c) > 0 for c in captions)

    print(
        f"\nCaption generation: {success_count}/{len(dataset)} successful ({success_rate:.1%})"
    )


def test_florence_caption_performance(tagger, image_files):
    """Test Florence-2 captioning performance."""
    # Use all available test images
    test_images = image_files[:MAX_TEST_IMAGES]

    start_time = time.time()
    captions = []

    for image_path in test_images:
        caption = tagger._generate_florence_caption(image_path)
        captions.append(caption)

    end_time = time.time()
    total_time = end_time - start_time
    time_per_image = total_time / len(test_images)
    images_per_second = 1 / time_per_image if time_per_image else float("inf")

    # Verify captions were actually generated
    valid_captions = [c for c in captions if c and len(c) > 0]
    assert len(valid_captions) == len(test_images), (
        f"Only {len(valid_captions)}/{len(test_images)} captions generated. "
        "Florence-2 may not be working correctly."
    )

    print("\nPerformance results:")
    print(f"  Total images: {len(test_images)}")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Time per image: {time_per_image:.3f}s ({time_per_image * 1000:.0f}ms)")
    print(f"  Images per second: {images_per_second:.2f}")

    # Ensure reasonable minimum time (sanity check)
    assert time_per_image > 0.01, (
        f"Performance suspiciously fast ({time_per_image:.3f}s). "
        "Florence-2 may not have actually run."
    )

    # Relax requirements when running on CPU; Florence takes longer there.
    device = getattr(tagger, "_florence_device", None)
    if device is not None and getattr(device, "type", "cpu") == "cuda":
        assert time_per_image < 1.5, (
            f"Performance too slow on GPU: {time_per_image:.3f}s per image"
        )
    else:
        # Increased timeout for slower CI runners (GitHub Actions, etc.)
        # With 50 tokens and optimizations, should be ~3-4s per image on decent CPU
        # GitHub Actions can be 5-10x slower, so allow up to 40s for slower CI environments
        assert time_per_image < 40.0, (
            f"Performance too slow on CPU: {time_per_image:.3f}s per image"
        )


def test_florence_caption_content(tagger, image_files):
    """Test that captions contain meaningful content."""
    # Test all available images
    dataset = image_files[:MAX_TEST_IMAGES]
    for image_path in dataset:
        caption = tagger._generate_florence_caption(image_path)

        # Caption should be at least 10 characters
        assert len(caption) >= 10, f"Caption too short: {caption}"

        # Caption should not contain special tokens
        assert "<s>" not in caption, "Caption contains <s> token"
        assert "</s>" not in caption, "Caption contains </s> token"
        assert "<pad>" not in caption, "Caption contains <pad> token"

        # Caption should start with capital letter or digit
        assert caption[0].isupper() or caption[0].isdigit(), (
            f"Caption doesn't start with capital: {caption}"
        )


def test_florence_handles_missing_file(tagger):
    """Test that Florence-2 handles missing files gracefully."""
    caption = tagger._generate_florence_caption("/nonexistent/file.jpg")

    # Should return None or empty string for missing files
    assert caption is None or caption == ""


if __name__ == "__main__":
    # Allow running directly
    pytest.main([__file__, "-v"])
