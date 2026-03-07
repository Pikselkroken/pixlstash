"""
Pytest configuration and fixtures for test suite.
"""

from pixlvault.picture_tagger import PictureTagger
from pixlvault.server import Server


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--force-cpu",
        action="store_true",
        default=False,
        help="Force CPU inference for all models (disable GPU usage)",
    )
    parser.addoption(
        "--fast-captions",
        action="store_true",
        default=False,
        help="Use minimal tokens for faster caption generation (for CI)",
    )
    parser.addoption(
        "--max-vram-gb",
        type=float,
        default=None,
        help="VRAM budget in GB applied to all Server instances (e.g. 4.0). "
        "Overrides the persisted user config value.",
    )


def pytest_configure(config):
    """Set static attributes on PictureTagger from command line options."""
    force_cpu = config.getoption("--force-cpu")
    PictureTagger.FORCE_CPU = force_cpu
    # Persist force-cpu as a Server-level override so startup checks cannot
    # clobber the flag after conftest sets it (startup checks set FORCE_CPU
    # based on the server config's default_device value).
    Server.DEFAULT_FORCE_CPU = True if force_cpu else None
    PictureTagger.FAST_CAPTIONS = config.getoption("--fast-captions")
    Server.DEFAULT_MAX_VRAM_GB = config.getoption("--max-vram-gb")
