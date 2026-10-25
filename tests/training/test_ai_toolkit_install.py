"""Discovery and check of a local ai-toolkit install, without a real trainer.

``check`` takes an injected runner, so no Python is spawned except in the one
test of :func:`run_host_process`, which runs the test interpreter for a moment.
"""

from __future__ import annotations

import os
import subprocess
import sys

from pixlstash.training import ai_toolkit_install as install


def _checkout(root, *, toolkit: bool = True, version: str | None = None) -> str:
    """A fake ai-toolkit checkout: ``run.py`` and ``toolkit/``."""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "run.py"), "w", encoding="utf-8") as fh:
        fh.write("")
    if toolkit:
        os.makedirs(os.path.join(root, "toolkit"), exist_ok=True)
    if version is not None:
        with open(os.path.join(root, "version.py"), "w", encoding="utf-8") as fh:
            fh.write(f'VERSION = "{version}"\n')
    return str(root)


def _interpreter(path) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("")
    return str(path)


def _no_path_python(name: str):
    """A PATH lookup that finds nothing."""
    return None


def _python3_on_path(name: str):
    return "/usr/bin/python3"


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def test_discover_takes_the_parent_of_a_shelf_source_folder_first(tmp_path):
    repo = _checkout(tmp_path / "shelf-owner" / "ai-toolkit")
    decoy = _checkout(tmp_path / "home" / "ai-toolkit")
    result = install.discover(
        shelf_source_folders=[os.path.join(repo, "output")],
        saved_repo_path=decoy,
        home=str(tmp_path / "home"),
        which=_no_path_python,
    )
    assert result["found"] is True
    assert result["repo_path"] == repo
    assert result["searched"][0] == repo


def test_discover_prefers_the_saved_path_over_the_home_locations(tmp_path):
    saved = _checkout(tmp_path / "saved" / "kit")
    _checkout(tmp_path / "home" / "ai-toolkit")
    result = install.discover(
        saved_repo_path=saved,
        home=str(tmp_path / "home"),
        which=_no_path_python,
    )
    assert result["repo_path"] == saved


def test_discover_searches_the_common_home_locations_in_order(tmp_path):
    home = tmp_path / "home"
    _checkout(home / "Projects" / "ai-toolkit")
    result = install.discover(home=str(home), which=_no_path_python)
    assert result["found"] is True
    assert result["repo_path"] == str(home / "Projects" / "ai-toolkit")
    assert result["searched"] == [
        str(home / "ai-toolkit"),
        str(home / "Projects" / "ai-toolkit"),
        str(home / "src" / "ai-toolkit"),
        str(home / "code" / "ai-toolkit"),
        str(home / "git" / "ai-toolkit"),
    ]


def test_a_directory_without_toolkit_is_not_a_checkout(tmp_path):
    home = tmp_path / "home"
    _checkout(home / "ai-toolkit", toolkit=False)
    result = install.discover(home=str(home), which=_no_path_python)
    assert result["found"] is False
    assert str(home / "ai-toolkit") in result["searched"]


def test_discover_reports_nothing_found_with_every_place_searched(tmp_path):
    home = tmp_path / "empty-home"
    os.makedirs(home)
    result = install.discover(home=str(home), which=_no_path_python)
    assert result == {
        "found": False,
        "repo_path": None,
        "python": None,
        "version": None,
        "searched": [str(home / rel) for rel in install.HOME_CHECKOUT_CANDIDATES],
    }


# ---------------------------------------------------------------------------
# python and version
# ---------------------------------------------------------------------------


def test_dot_venv_is_preferred_over_venv(tmp_path):
    repo = _checkout(tmp_path / "kit")
    dot = _interpreter(os.path.join(repo, ".venv", "bin", "python"))
    _interpreter(os.path.join(repo, "venv", "bin", "python"))
    assert install.find_python(repo, which=_no_path_python) == os.path.abspath(dot)


def test_venv_is_used_when_there_is_no_dot_venv(tmp_path):
    repo = _checkout(tmp_path / "kit")
    venv = _interpreter(os.path.join(repo, "venv", "bin", "python"))
    assert install.find_python(repo, which=_python3_on_path) == os.path.abspath(venv)


def test_python3_on_the_path_is_the_last_resort(tmp_path):
    repo = _checkout(tmp_path / "kit")
    assert install.find_python(repo, which=_python3_on_path) == "/usr/bin/python3"
    assert install.find_python(repo, which=_no_path_python) is None


def test_version_is_read_by_regex_and_never_executed(tmp_path):
    """version.py raises if it is imported, so a passing parse proves it was read."""
    repo = _checkout(tmp_path / "kit")
    with open(os.path.join(repo, "version.py"), "w", encoding="utf-8") as fh:
        fh.write('raise SystemExit("version.py was executed")\nVERSION = "0.12.26"\n')
    assert install.read_version(repo) == "0.12.26"


def test_a_checkout_without_version_py_has_no_version(tmp_path):
    repo = _checkout(tmp_path / "kit")
    assert install.read_version(repo) is None


# ---------------------------------------------------------------------------
# check (injected runner)
# ---------------------------------------------------------------------------


class _Runner:
    """Scripted replies, in call order, recording every argv it was given."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[list[str], str, float]] = []

    def __call__(self, argv, cwd, timeout):
        self.calls.append((argv, cwd, timeout))
        return self.replies.pop(0)


_PROBE_OK = (
    0,
    '{"torch": "2.13.0+cu128", "cuda": true, '
    '"gpus": [{"name": "RTX 4090", "total_mb": 24564}]}\n',
    "",
)
_HELP_OK = (0, "usage: run.py", "")


def test_check_passes_when_torch_sees_cuda_and_the_trainer_loads(tmp_path):
    repo = _checkout(tmp_path / "kit", version="0.12.26")
    py = _interpreter(tmp_path / "py" / "python")
    runner = _Runner(_PROBE_OK, _HELP_OK)
    result = install.check(repo, py, runner=runner)
    assert result == {
        "ok": True,
        "version": "0.12.26",
        "torch": "2.13.0+cu128",
        "cuda": True,
        "gpus": [{"name": "RTX 4090", "total_mb": 24564}],
        "trial_ok": True,
        "detail": None,
    }


def test_check_runs_two_argv_lists_in_the_checkout_with_timeouts(tmp_path):
    repo = _checkout(tmp_path / "kit")
    py = _interpreter(tmp_path / "py" / "python")
    runner = _Runner(_PROBE_OK, _HELP_OK)
    install.check(repo, py, runner=runner)
    assert len(runner.calls) == 2
    (probe_argv, probe_cwd, probe_timeout), (help_argv, help_cwd, help_timeout) = (
        runner.calls
    )
    assert probe_argv[:2] == [py, "-c"] and isinstance(probe_argv, list)
    assert help_argv == [py, "run.py", "--help"]
    assert probe_cwd == help_cwd == repo
    assert 0 < probe_timeout and 0 < help_timeout


def test_check_says_torch_sees_no_gpu(tmp_path):
    repo = _checkout(tmp_path / "kit")
    py = _interpreter(tmp_path / "py" / "python")
    no_cuda = (0, '{"torch": "2.13.0+cpu", "cuda": false, "gpus": []}\n', "")
    result = install.check(repo, py, runner=_Runner(no_cuda, _HELP_OK))
    assert result["ok"] is False
    assert result["cuda"] is False
    assert result["trial_ok"] is True
    assert result["detail"] == "torch imports but sees no CUDA GPU"


def test_check_says_torch_did_not_import_with_the_last_stderr_line(tmp_path):
    repo = _checkout(tmp_path / "kit")
    py = _interpreter(tmp_path / "py" / "python")
    broken = (1, "", "Traceback...\nModuleNotFoundError: No module named 'torch'\n")
    result = install.check(repo, py, runner=_Runner(broken, _HELP_OK))
    assert result["ok"] is False
    assert result["torch"] is None
    assert result["detail"] == (
        "torch did not import: ModuleNotFoundError: No module named 'torch'"
    )


def test_check_says_run_py_help_failed_with_its_last_stderr_line(tmp_path):
    repo = _checkout(tmp_path / "kit")
    py = _interpreter(tmp_path / "py" / "python")
    help_fails = (1, "", "boom: the trainer's config is wrong\n")
    result = install.check(repo, py, runner=_Runner(_PROBE_OK, help_fails))
    assert result["ok"] is False
    assert result["trial_ok"] is False
    assert (
        result["detail"] == "run.py --help failed: boom: the trainer's config is wrong"
    )


def test_check_treats_probe_output_that_is_not_json_as_a_failed_import(tmp_path):
    repo = _checkout(tmp_path / "kit")
    py = _interpreter(tmp_path / "py" / "python")
    garbled = (0, "warning: nonsense\n", "")
    result = install.check(repo, py, runner=_Runner(garbled, _HELP_OK))
    assert result["ok"] is False
    assert result["detail"] == "torch did not import: no output"


# ---------------------------------------------------------------------------
# the real process runner (one fast spawn)
# ---------------------------------------------------------------------------


def test_run_host_process_returns_output_and_reports_a_timeout(tmp_path):
    code, out, err = install.run_host_process(
        [sys.executable, "-c", "import sys; print('ok'); sys.stderr.write('e')"],
        str(tmp_path),
        30.0,
    )
    assert (code, out, err) == (0, "ok\n", "e")

    code, out, err = install.run_host_process(
        [sys.executable, "-c", "import time; time.sleep(5)"], str(tmp_path), 0.5
    )
    assert code == -1
    assert "timed out" in err


# ---------------------------------------------------------------------------
# read_commit: the commit a job records, asked of git (review F6)
# ---------------------------------------------------------------------------


def _git(cwd, *args):
    """Run git with an argument list and a fixed identity; return its stdout."""
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _init_repo(path):
    """A git repository with one commit at *path*; returns its HEAD commit."""
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return _git(path, "rev-parse", "HEAD")


def test_read_commit_is_the_checkouts_head(tmp_path):
    head = _init_repo(str(tmp_path / "kit"))
    assert install.read_commit(str(tmp_path / "kit")) == head


def test_read_commit_is_none_outside_a_git_checkout(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert install.read_commit(str(plain)) is None


def test_read_commit_does_not_take_a_parent_repositorys_commit(tmp_path):
    """A checkout folder inside some other git repository is not a checkout: the
    parent's HEAD must not be recorded as the trainer's commit."""
    _init_repo(str(tmp_path / "home"))
    nested = tmp_path / "home" / "ai-toolkit"
    nested.mkdir()
    assert install.read_commit(str(nested)) is None


def test_read_commit_is_none_when_git_cannot_run(tmp_path, monkeypatch):
    head = _init_repo(str(tmp_path / "kit"))
    assert head
    monkeypatch.setattr(
        install,
        "run_host_process",
        lambda argv, cwd, timeout: (-1, "", "git: not found"),
    )
    assert install.read_commit(str(tmp_path / "kit")) is None
