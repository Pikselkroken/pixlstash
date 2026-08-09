"""Tests for ``scripts/update_security_supported_versions.py``.

The table is release-facing prose, so these assert on the exact rendered text
including column padding rather than on a loose regex: ``1.10.x`` is one
character wider than ``1.9.x`` and must still line the columns up.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from update_security_supported_versions import (  # noqa: E402
    main,
    update_supported_versions,
)

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "update_security_supported_versions.py"
)

TABLE_1_9 = """| Version | Supported          |
| ------- | ------------------ |
| 1.9.x   | :white_check_mark: |
| 1.8.x   | :x:                |
| < 1.8.x | :x:                |
"""

TABLE_1_10 = """| Version | Supported          |
| ------- | ------------------ |
| 1.10.x  | :white_check_mark: |
| 1.9.x   | :x:                |
| < 1.9.x | :x:                |
"""

TABLE_2_0 = """| Version | Supported          |
| ------- | ------------------ |
| 2.0.x   | :white_check_mark: |
| 1.9.x   | :x:                |
| < 1.9.x | :x:                |
"""

# Every cell in the left column outgrows the "Version" header here, so the
# whole column widens rather than letting `< 1.10.x` overhang the separator.
TABLE_2_0_FROM_1_10 = """| Version  | Supported          |
| -------- | ------------------ |
| 2.0.x    | :white_check_mark: |
| 1.10.x   | :x:                |
| < 1.10.x | :x:                |
"""

PREAMBLE = """# Security Policy

## Supported Versions

PixlStash does not currently support older releases with security updates.

"""

EPILOGUE = """
## Reporting a Vulnerability

Please submit vulnerability reports to lindkvis@gmail.com.
"""


def document(table: str) -> str:
    return PREAMBLE + table + EPILOGUE


def test_minor_bump_rewrites_the_table_and_keeps_the_prose():
    text, message = update_supported_versions(document(TABLE_1_9), "v1.10.0")
    assert text == document(TABLE_1_10)
    assert "1.10.x" in message and "demoted 1.9.x" in message


def test_major_bump_demotes_the_table_minor_not_major_minus_one():
    # 2.-1.x is what naive arithmetic would produce here.
    text, _ = update_supported_versions(document(TABLE_1_9), "v2.0.0")
    assert text == document(TABLE_2_0)


def test_a_wider_version_widens_the_whole_column():
    text, _ = update_supported_versions(document(TABLE_1_10), "v2.0.0")
    assert text == document(TABLE_2_0_FROM_1_10)


def test_the_widened_table_is_still_parseable_on_the_next_release():
    """Two consecutive releases must chain without a manual repair."""
    text, _ = update_supported_versions(document(TABLE_2_0_FROM_1_10), "v2.1.0")
    assert "| 2.1.x   | :white_check_mark: |" in text
    assert "| < 2.0.x | :x:                |" in text


@pytest.mark.parametrize(
    "tag, reason",
    [
        ("v1.9.4", "patch release of the supported minor"),
        ("v1.10.0-dev.1", "pre-release"),
        ("v1.10.0rc1", "pre-release"),
        ("v1.8.7", "older minor"),
        ("v1.9.0", "same minor"),
    ],
)
def test_no_op_releases_leave_the_table_untouched(tag, reason):
    original = document(TABLE_1_9)
    text, message = update_supported_versions(original, tag)
    assert text == original, reason
    assert "skipping" in message


def test_unparseable_table_raises_instead_of_rewriting():
    mangled = PREAMBLE + "| Release | Status |\n| --- | --- |\n" + EPILOGUE
    with pytest.raises(ValueError, match="could not find the supported-versions table"):
        update_supported_versions(mangled, "v1.10.0")


def test_a_fourth_row_raises_rather_than_leaving_an_orphan():
    """A longer table must fail, not match its first three rows and strand the rest."""
    four_rows = TABLE_1_9 + "| < 1.7.x | :x:                |\n"
    with pytest.raises(ValueError, match="could not find the supported-versions table"):
        update_supported_versions(document(four_rows), "v1.10.0")


def test_table_without_a_supported_row_raises():
    no_check = TABLE_1_9.replace(":white_check_mark:", ":x:               ")
    with pytest.raises(ValueError, match="no supported row"):
        update_supported_versions(document(no_check), "v1.10.0")


def test_main_writes_the_file_and_reports(tmp_path, capsys):
    path = tmp_path / "SECURITY.md"
    path.write_text(document(TABLE_1_9), encoding="utf-8")

    assert main([str(path), "v1.10.0"]) == 0
    assert path.read_text(encoding="utf-8") == document(TABLE_1_10)
    assert "1.10.x" in capsys.readouterr().out


def test_main_exits_nonzero_and_leaves_the_file_alone_on_a_bad_table(tmp_path):
    path = tmp_path / "SECURITY.md"
    mangled = PREAMBLE + "| Release | Status |\n" + EPILOGUE
    path.write_text(mangled, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "v1.10.0"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "SECURITY.md" in result.stderr
    assert path.read_text(encoding="utf-8") == mangled


def test_the_repository_security_file_is_a_recognised_table():
    """The workflow points at the real file; keep its *shape* parseable.

    Deliberately asserts nothing about which versions the real table names --
    those move with every release and with branch merges. This fails only if
    someone reformats the table out from under the script.
    """
    security = Path(__file__).resolve().parents[1] / "SECURITY.md"
    text = security.read_text(encoding="utf-8")
    # A release far beyond anything shipped is guaranteed to be a newer minor,
    # so a rewrite proves the parser recognised the table.
    updated, message = update_supported_versions(text, "v99.0.0")
    assert updated != text, message
