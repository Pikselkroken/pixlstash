"""
Pytest configuration and fixtures for test suite.
"""

import pytest


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--force-cpu",
        action="store_true",
        default=False,
        help="Force CPU inference for Florence-2 (for benchmarking)",
    )
