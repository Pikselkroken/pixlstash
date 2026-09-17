"""The fast process exit keeps pytest's verdict and pytest's output.

``tests/process_teardown.py`` ends the process with ``os._exit`` rather than
letting CPython finalize, because native exit-time destructors crash
intermittently on Windows. Two things have to survive that, and they are what
this file asserts:

* **the exit status**, because a harness that exits 0 on a red run is worse
  than the flake it replaces, and
* **the terminal output**, because ``os._exit`` flushes nothing on its own and
  pytest's global capture is still in place when the hook runs.

Both are proved by running a real pytest session in a subprocess and reading
what came back, not by inspecting the decision in isolation - the risk is in
the exit itself, not in choosing to take it.

The platform decision is unit-tested separately, with the platform and the
environment passed in, so the Windows branch is covered on the Linux gate that
never takes it.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from tests.process_teardown import (
    FAST_EXIT_ENV,
    XDIST_WORKER_ENV,
    fast_exit_reason,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _conftest(after_remember="pass"):
    """The wiring tests/conftest.py uses, so the subprocess exercises the glue.

    ``after_remember`` is the session-finish work that follows the recording,
    which in tests/conftest.py is the leaked-thread and time-budget
    guardrails. What one of those does to the run when it raises is the thing
    under test, so it is a parameter rather than a fixed body.
    """
    return textwrap.dedent(
        f"""
        import pytest
        from tests.process_teardown import exit_if_enabled, remember_session


        @pytest.hookimpl(trylast=True)
        def pytest_sessionfinish(session, exitstatus):
            remember_session(session)
            {after_remember}


        @pytest.hookimpl(trylast=True)
        def pytest_unconfigure(config):
            exit_if_enabled(config)
        """
    )


def _subprocess_env(*, fast_exit):
    """The parent environment, with the repo importable and the knob pinned.

    Inherited rather than rebuilt: pytest needs a usable environment on every
    platform, and the parent's own value of the knob must not decide what the
    child does.
    """
    existing = os.environ.get("PYTHONPATH")
    path = str(REPO_ROOT) if not existing else f"{REPO_ROOT}{os.pathsep}{existing}"
    return {
        **os.environ,
        "PYTHONPATH": path,
        FAST_EXIT_ENV: "1" if fast_exit else "0",
    }


def _run_pytest(tmp_path, body, *, fast_exit, after_remember="pass"):
    """Run a one-test pytest session in its own process and return it."""
    (tmp_path / "conftest.py").write_text(_conftest(after_remember))
    (tmp_path / "test_subject.py").write_text(textwrap.dedent(body))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "test_subject.py"],
        cwd=tmp_path,
        env=_subprocess_env(fast_exit=fast_exit),
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.parametrize("fast_exit", [True, False])
def test_a_passing_session_still_exits_zero_and_says_so(tmp_path, fast_exit):
    """The fast exit reports success exactly as full finalization does."""
    result = _run_pytest(
        tmp_path,
        """
        def test_ok():
            assert True
        """,
        fast_exit=fast_exit,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("fast_exit", [True, False])
def test_a_failing_session_still_exits_nonzero_and_says_so(tmp_path, fast_exit):
    """A red run stays red. Exiting early must not swallow the verdict.

    This is the assertion the whole change stands on: ``os._exit`` takes the
    status handed to it, so reading the wrong status would turn every Windows
    failure into a green job.
    """
    result = _run_pytest(
        tmp_path,
        """
        def test_not_ok():
            assert False, "deliberate failure"
        """,
        fast_exit=fast_exit,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed" in result.stdout, result.stdout + result.stderr
    assert "deliberate failure" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("fast_exit", [True, False])
def test_a_session_finish_hook_that_raises_is_never_a_green_job(tmp_path, fast_exit):
    """A guardrail that raises stays red, and still says why.

    pytest catches only ``exit.Exception`` around ``pytest_sessionfinish``.
    Anything else escapes ``wrap_session``'s ``finally`` - skipping the
    ``_ensure_unconfigure`` there - and reaches ``main``'s own, which runs
    ``pytest_unconfigure`` with the exception still propagating and the
    session's exit status still 0, because nothing updated it. Exiting on that
    status unchanged would turn every raising guardrail on Windows into a
    green job, and ``os._exit`` would take the traceback with it, so both arms
    have to end non-zero and both have to print the reason.
    """
    result = _run_pytest(
        tmp_path,
        """
        def test_ok():
            assert True
        """,
        fast_exit=fast_exit,
        after_remember='raise AssertionError("deliberate guardrail failure")',
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "deliberate guardrail failure" in output, output


def test_the_fast_exit_announces_itself(tmp_path):
    """The exit is never silent: it names itself and where to read about it."""
    result = _run_pytest(
        tmp_path,
        """
        def test_ok():
            assert True
        """,
        fast_exit=True,
    )
    assert "without interpreter finalization" in result.stderr, result.stderr
    assert "tests/process_teardown.py" in result.stderr, result.stderr


def test_a_normal_run_finalizes_and_says_nothing(tmp_path):
    """The opt-out really opts out, so the old path is still reachable."""
    result = _run_pytest(
        tmp_path,
        """
        def test_ok():
            assert True
        """,
        fast_exit=False,
    )
    assert "without interpreter finalization" not in result.stderr, result.stderr


def test_windows_skips_native_teardown_and_other_platforms_do_not():
    """The platform decision, covered on the platform that never takes it."""
    assert fast_exit_reason("win32", {}) is not None
    assert fast_exit_reason("linux", {}) is None
    assert fast_exit_reason("darwin", {}) is None


@pytest.mark.parametrize(
    ("platform", "override", "expected"),
    [
        ("win32", "0", False),
        ("linux", "1", True),
        ("win32", "1", True),
        ("linux", "0", False),
        ("win32", "", True),
        ("linux", "yes", False),
    ],
)
def test_the_override_decides_in_both_directions(platform, override, expected):
    """``0`` puts Windows back on the normal path; ``1`` forces it anywhere.

    Anything else is not an override - a typo must not quietly change what a
    platform does, in either direction.
    """
    reason = fast_exit_reason(platform, {FAST_EXIT_ENV: override})
    assert (reason is not None) is expected


@pytest.mark.parametrize("override", [None, "1"])
def test_an_xdist_worker_never_takes_the_fast_exit(override):
    """A worker is killed by nothing, not even the override.

    ``-n auto`` is the recommended local runner, and a worker that vanishes
    mid-protocol is reported to its controller as a crash - so this outranks
    both the platform and the knob.
    """
    environ = {XDIST_WORKER_ENV: "gw0"}
    if override is not None:
        environ[FAST_EXIT_ENV] = override
    assert fast_exit_reason("win32", environ) is None
    assert fast_exit_reason("linux", environ) is None
