"""Tests verifying that Alembic migrations run cleanly on a fresh database."""

import os
import subprocess
import sys
import tempfile


_MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "pixlstash")
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_alembic(args, db_url, cwd):
    env = {**os.environ, "PIXLSTASH_DB_URL": db_url, "PYTHONPATH": _PROJECT_ROOT}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini"] + args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    return result


def test_alembic_upgrade_head_fresh_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        result = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert os.path.isfile(db_path), "Database file was not created"


def test_alembic_downgrade_one_step():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_vault.db")
        db_url = f"sqlite:///{db_path}"

        up = _run_alembic(["upgrade", "head"], db_url, _MIGRATIONS_DIR)
        assert up.returncode == 0, (
            f"alembic upgrade head failed:\nstdout: {up.stdout}\nstderr: {up.stderr}"
        )

        down = _run_alembic(["downgrade", "-1"], db_url, _MIGRATIONS_DIR)
        assert down.returncode == 0, (
            f"alembic downgrade -1 failed:\nstdout: {down.stdout}\nstderr: {down.stderr}"
        )
