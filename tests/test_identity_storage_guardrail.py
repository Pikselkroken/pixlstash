"""Identity is read and written in the hub, never in a vault.

Since the hub/vault split, ``User`` and ``UserToken`` live in the hub. A query
for either routed through ``vault.db`` runs against whichever library happens to
be active, which is the exact bug the split removes: on a fresh vault it returns
nothing (a silent wrong answer, not an error), and after a library switch it
would answer from a different library.

This is a **guardrail, not a findings list.** The repo's security process asks
for completeness to be arithmetic rather than a matter of judgement, so this
walks every module and fails on any occurrence, with an explicit allowlist. A
new route that reaches for identity through the vault fails here rather than
being caught by whoever happens to review it.
"""

import ast
import pathlib

import pytest

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "pixlstash"

# The task-runner methods that execute a callable against a database.
_RUNNER_METHODS = {"run_task", "run_immediate_read_task", "submit_task"}

# The models that moved to the hub.
_IDENTITY_MODELS = {"User", "UserToken"}

# Modules exempt from the rule, each for a stated reason. Adding an entry is a
# deliberate act, which is the point: the list is short and every line says why.
_ALLOWLIST: dict[str, str] = {
    # The first-run migration reads the vault's pre-split identity rows on
    # purpose, to copy them into the hub. It uses raw sqlite3 rather than the
    # vault task runner, so it should not match anyway; listed for the record.
    "hub/bootstrap.py": "copies pre-split identity out of the vault, by design",
}


def _iter_python_files():
    """Yield every module in the package except migrations and caches."""
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        if relative.startswith("migrations/") or "__pycache__" in relative:
            continue
        yield path, relative


def _runs_against_a_vault(call: ast.Call, source: str) -> bool:
    """True when this call executes something against ``vault.db``."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _RUNNER_METHODS:
        return False
    receiver = ast.get_source_segment(source, func.value) or ""
    return receiver.endswith("vault.db") or receiver.endswith("vault_db")


def _mentions_identity(node: ast.AST) -> set[str]:
    """Return the identity model names referenced anywhere under *node*."""
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in _IDENTITY_MODELS:
            found.add(child.id)
        elif isinstance(child, ast.Attribute) and child.attr in _IDENTITY_MODELS:
            found.add(child.attr)
    return found


def _violations_in(path: pathlib.Path, relative: str) -> list[str]:
    """Return one message per identity access routed through a vault."""
    source = path.read_text(encoding="utf-8")
    if "vault.db" not in source and "vault_db" not in source:
        return []

    tree = ast.parse(source, filename=str(path))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _runs_against_a_vault(node, source):
            continue
        if not node.args:
            continue

        target = node.args[0]
        # ``run_task(update_user, ...)`` names a function defined nearby;
        # ``run_task(lambda session: ...)`` carries its body inline.
        if isinstance(target, ast.Name) and target.id in functions:
            body = functions[target.id]
        elif isinstance(target, ast.Lambda):
            body = target
        else:
            continue

        models = _mentions_identity(body)
        if models:
            problems.append(
                f"{relative}:{node.lineno} runs a query touching "
                f"{', '.join(sorted(models))} against vault.db; identity lives "
                "in the hub (use the hub engine)"
            )
    return problems


def test_identity_is_never_queried_through_a_vault():
    """Every ``User``/``UserToken`` access goes to the hub.

    The failure message lists every offending site, so the fix list is the test
    output rather than something a reviewer has to assemble.
    """
    problems = []
    for path, relative in _iter_python_files():
        if relative in _ALLOWLIST:
            continue
        problems.extend(_violations_in(path, relative))

    assert not problems, "Identity accessed through a vault:\n" + "\n".join(problems)


def test_the_allowlist_stays_small_and_justified():
    """Each exemption carries a reason, and the list does not grow quietly."""
    assert len(_ALLOWLIST) <= 3, "the allowlist is growing; justify or fix instead"
    for module, reason in _ALLOWLIST.items():
        assert reason.strip(), f"{module} is allowlisted without a reason"
        assert (PACKAGE_ROOT / module).exists(), f"{module} no longer exists"


@pytest.mark.parametrize(
    "snippet",
    [
        "server.vault.db.run_task(lambda session: session.get(User, 1))",
        "self.vault.db.run_immediate_read_task(lambda s: s.exec(select(UserToken)))",
    ],
)
def test_the_guardrail_actually_catches_the_pattern(snippet, tmp_path):
    """The detector is tested against known-bad code, so a silent pass is not a pass."""
    module = tmp_path / "offender.py"
    module.write_text(snippet, encoding="utf-8")
    assert _violations_in(module, "offender.py")
