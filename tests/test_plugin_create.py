"""`pixlstash-cli plugins create` - scaffolding a plugin pull request.

Nothing here reaches the network or GitHub: the "checkout" is a directory laid
out like the plugins repository, and the one test that needs real branching
runs ``git init`` in ``tmp_path``.  What is actually under test is the rewrite
- the folder, the module filename, the class, the header and the README - plus
the refusals that stop someone spending an afternoon on a pull request the
repository would turn down for its name.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pixlstash import cli, plugin_create, plugin_install
from pixlstash.plugin_install import CAPTIONING, IMAGE, PluginError

EXAMPLE_STAMP = '''\
"""Hello-world image plugin for PixlStash: stamps "Hello World" on the image."""

from __future__ import annotations

from typing import Any

from pixlstash.image_plugins.base import ImagePlugin

DEFAULT_TEXT = "Hello World"


class HelloWorldStamp(ImagePlugin):
    """Draws a line of magenta text onto every image in the batch."""

    name = "hello_world_stamp"
    display_name = "Hello World Stamp"
    description = "Stamps text. Example plugin."
    author = "PixlStash plugins <https://example.com/plugins>"
    license = "MIT"
    models = []

    def parameter_schema(self) -> list[dict[str, Any]]:
        return []

    def run(self, images, parameters, progress_callback=None, error_callback=None):
        return images
'''

EXAMPLE_CAPTIONER = '''\
"""Hello-world captioner plugin for PixlStash."""

from __future__ import annotations

from typing import Any

from pixlstash.tagger_plugins.base import TaggerPlugin


class HelloWorldCaptioner(TaggerPlugin):
    """Captions every image from a template, with no model involved."""

    name = "hello_world_captioner"
    display_name = "Hello World Captioner"
    description = "Writes a templated description. Example plugin."
    author = "PixlStash plugins <https://example.com/plugins>"
    license = "MIT"
    models = []

    def parameter_schema(self) -> list[dict[str, Any]]:
        return []

    def needs_download(self, parameters=None) -> bool:
        return False

    def init(self, parameters=None) -> None:
        return None

    def unload(self) -> None:
        return None

    def is_loaded(self) -> bool:
        return True
'''

EXAMPLE_README = "# Hello World Stamp\n\nDraws magenta text on every picture.\n"


@pytest.fixture
def checkout(tmp_path) -> Path:
    """A directory laid out the way the plugins repository is."""
    root = tmp_path / "PixlStash-plugins"
    image = root / "plugins" / "image" / "hello_world_stamp"
    image.mkdir(parents=True)
    (image / "hello_world_stamp.py").write_text(EXAMPLE_STAMP, encoding="utf-8")
    (image / "README.md").write_text(EXAMPLE_README, encoding="utf-8")

    captioning = root / "plugins" / "captioning" / "hello_world_captioner"
    captioning.mkdir(parents=True)
    (captioning / "__init__.py").write_text(EXAMPLE_CAPTIONER, encoding="utf-8")
    (captioning / "README.md").write_text("# Hello World Captioner\n", encoding="utf-8")
    return root


# ----------------------------------------------------------------------
# Scaffolding
# ----------------------------------------------------------------------


def test_an_image_plugin_is_copied_renamed_and_recredited(checkout):
    """The whole rewrite, in one place: folder, file, class, header, README."""
    folder, module, readme, brief, warnings = plugin_create.scaffold(
        checkout,
        "magenta_wash",
        IMAGE,
        example="hello_world_stamp",
        author="Ada <ada@example.com>",
        plugin_license="Apache-2.0",
    )

    assert warnings == []
    assert folder == checkout / "plugins" / "image" / "magenta_wash"
    # The image loader scans for `.py` and never sees the folder, so a module
    # still called after the example is a plugin that installs under the wrong
    # name. The rename is the contract, not tidiness.
    assert module.name == "magenta_wash.py"
    assert not (folder / "hello_world_stamp.py").exists()

    source = module.read_text(encoding="utf-8")
    assert "class MagentaWash(ImagePlugin):" in source
    assert 'name = "magenta_wash"' in source
    assert 'display_name = "Magenta Wash"' in source
    assert 'author = "Ada <ada@example.com>"' in source
    assert 'license = "Apache-2.0"' in source
    # Nothing of the example's identity may survive into a pull request.
    assert "hello_world_stamp" not in source
    assert "HelloWorldStamp" not in source
    assert "PixlStash plugins" not in source

    assert source.startswith('"""Magenta Wash plugin for PixlStash.')
    assert "TODO" in readme.read_text(encoding="utf-8")
    assert "Hello World Stamp" not in readme.read_text(encoding="utf-8")


def test_a_captioning_plugin_keeps_its_init_filename(checkout):
    """A captioning plugin is a package, so `__init__.py` must not be renamed."""
    folder, module, _readme, _brief, _warnings = plugin_create.scaffold(
        checkout,
        "tiny_vlm",
        CAPTIONING,
        example="hello_world_captioner",
        description="Captions with a tiny VLM.",
    )

    assert module == folder / "__init__.py"
    source = module.read_text(encoding="utf-8")
    assert "class TinyVlm(TaggerPlugin):" in source
    assert 'description = "Captions with a tiny VLM."' in source


def test_the_description_is_a_visible_todo_when_it_is_not_given(checkout):
    """A placeholder passes the contract tests, so it has to be readable as one."""
    _folder, module, _readme, _brief, _warnings = plugin_create.scaffold(
        checkout, "tiny_vlm", CAPTIONING, example="hello_world_captioner"
    )
    assert "TODO" in module.read_text(encoding="utf-8")


def test_a_missing_header_field_warns_rather_than_passing_silently(checkout):
    """Shipping the example's author is the failure this warning exists for."""
    example = checkout / "plugins" / "image" / "hello_world_stamp"
    source = (example / "hello_world_stamp.py").read_text(encoding="utf-8")
    (example / "hello_world_stamp.py").write_text(
        source.replace(
            '    author = "PixlStash plugins <https://example.com/plugins>"\n', ""
        ),
        encoding="utf-8",
    )

    _folder, _module, _readme, _brief, warnings = plugin_create.scaffold(
        checkout, "magenta_wash", IMAGE, example="hello_world_stamp"
    )
    assert any("author" in warning for warning in warnings)


def test_a_header_value_holding_a_quote_still_parses(checkout):
    """The values reach a Python literal, so quoting them is not cosmetic."""
    description = 'Says "hello", and a backslash \\g too.'
    _folder, module, _readme, _brief, _warnings = plugin_create.scaffold(
        checkout,
        "magenta_wash",
        IMAGE,
        example="hello_world_stamp",
        description=description,
    )

    source = module.read_text(encoding="utf-8")
    literals = {
        target.id: node.value.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert literals["description"] == description


def test_a_long_description_is_wrapped_so_the_file_needs_no_reformatting(checkout):
    """A sentence-length description would otherwise make `ruff format` dirty."""
    sentence = (
        "Give every picture a soft glow around its edges, much like a bloom filter."
    )
    _folder, module, _readme, _brief, _warnings = plugin_create.scaffold(
        checkout, "edge_glow", IMAGE, example="hello_world_stamp", purpose=sentence
    )

    source = module.read_text(encoding="utf-8")
    assert all(len(line) <= 88 for line in source.splitlines())
    # Adjacent literals are one constant to `ast`, so the header readers that
    # never import a plugin still see the whole sentence.
    literals = {
        target.id: node.value.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert literals["description"] == sentence


def test_the_git_identity_is_the_default_author(checkout, monkeypatch):
    monkeypatch.setattr(plugin_create, "git_identity", lambda _root: "Ada <a@b.com>")
    _folder, module, _readme, _brief, _warnings = plugin_create.scaffold(
        checkout, "magenta_wash", IMAGE, example="hello_world_stamp"
    )
    assert 'author = "Ada <a@b.com>"' in module.read_text(encoding="utf-8")


def test_a_machine_with_no_git_identity_still_gets_a_valid_placeholder(
    checkout, monkeypatch
):
    """The repository's tests require `Name <contact>`, placeholder included."""
    monkeypatch.setattr(plugin_create, "git_identity", lambda _root: None)
    _folder, module, _readme, _brief, _warnings = plugin_create.scaffold(
        checkout, "magenta_wash", IMAGE, example="hello_world_stamp"
    )
    assert f'author = "{plugin_create._PLACEHOLDER_AUTHOR}"' in module.read_text(
        encoding="utf-8"
    )


# ----------------------------------------------------------------------
# Refusals
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["../evil", "My_Filter", "9lives", "with-dash", ""])
def test_a_name_that_is_not_a_plugin_name_is_refused(checkout, name):
    """The name reaches the filesystem, so this is containment, not tidiness."""
    with pytest.raises(PluginError, match="not a plugin name"):
        plugin_create.scaffold(checkout, name, IMAGE, example="hello_world_stamp")


def test_a_name_the_repository_already_publishes_is_refused(checkout):
    """Names are unique across the repository, not within one kind."""
    with pytest.raises(PluginError, match="already publishes"):
        plugin_create.scaffold(
            checkout, "hello_world_captioner", IMAGE, example="hello_world_stamp"
        )


def test_a_builtin_name_is_refused(checkout):
    """A built-in wins the collision, so the plugin would never load."""
    with pytest.raises(PluginError, match="already ships"):
        plugin_create.scaffold(checkout, "pixelate", IMAGE, example="hello_world_stamp")


def test_an_existing_folder_is_not_overwritten(checkout):
    (checkout / "plugins" / "image" / "magenta_wash").mkdir(parents=True)
    with pytest.raises(PluginError, match="already exists"):
        plugin_create.scaffold(
            checkout, "magenta_wash", IMAGE, example="hello_world_stamp"
        )


def test_an_example_of_the_other_kind_is_refused(checkout):
    """Copying a captioner into `plugins/image/` yields a plugin nothing loads."""
    with pytest.raises(PluginError, match="cannot be the starting point"):
        plugin_create.scaffold(
            checkout, "magenta_wash", IMAGE, example="hello_world_captioner"
        )


def test_an_unknown_example_lists_what_there_is(checkout):
    with pytest.raises(PluginError, match="hello_world_stamp"):
        plugin_create.scaffold(checkout, "magenta_wash", IMAGE, example="nope")


# ----------------------------------------------------------------------
# The checkout
# ----------------------------------------------------------------------


def test_an_existing_checkout_is_reused(checkout):
    (checkout / ".git").mkdir()
    forked, reused = plugin_create.obtain_checkout(checkout, fork=False)
    assert reused is True
    assert forked is False


def test_a_forked_checkout_is_recognised_by_its_upstream_remote(checkout, monkeypatch):
    (checkout / ".git").mkdir()
    listed = subprocess.CompletedProcess([], 0, stdout="origin\nupstream\n", stderr="")
    monkeypatch.setattr(plugin_create.subprocess, "run", lambda *a, **k: listed)
    forked, reused = plugin_create.obtain_checkout(checkout, fork=False)
    assert (forked, reused) == (True, True)


def test_a_non_empty_directory_that_is_not_a_checkout_is_refused(tmp_path):
    directory = tmp_path / "somewhere"
    directory.mkdir()
    (directory / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(PluginError, match="already exists"):
        plugin_create.obtain_checkout(directory, fork=True)


def test_without_gh_it_clones_instead_of_forking(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(plugin_create, "_have_gh", lambda: False)
    monkeypatch.setattr(
        plugin_create, "_run", lambda command, cwd=None: commands.append(command) or ""
    )

    forked, reused = plugin_create.obtain_checkout(tmp_path / "clone", fork=True)
    assert (forked, reused) == (False, False)
    assert commands == [
        ["git", "clone", plugin_create.CLONE_URL, str(tmp_path / "clone")]
    ]


def test_a_fork_that_fails_falls_back_to_a_plain_clone(tmp_path, monkeypatch):
    """Being unable to fork must not stop the scaffold; it changes the advice."""
    monkeypatch.setattr(plugin_create, "_have_gh", lambda: True)
    commands = []

    def run(command, cwd=None):
        commands.append(command)
        if command[0] == "gh":
            raise PluginError("cannot fork your own repository")
        return ""

    monkeypatch.setattr(plugin_create, "_run", run)
    forked, _reused = plugin_create.obtain_checkout(tmp_path / "clone", fork=True)
    assert forked is False
    assert [command[0] for command in commands] == ["gh", "git"]


@pytest.fixture
def git_checkout(tmp_path) -> Path:
    """A real one-commit repository on `main`, for the branching tests."""
    directory = tmp_path / "repo"
    directory.mkdir()
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
        ["git", "commit", "-q", "--allow-empty", "-m", "first"],
    ):
        subprocess.run(command, cwd=directory, check=True)
    return directory


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_branch_from_an_earlier_run_says_what_to_do_about_it(git_checkout):
    """Reusing a checkout is supported, so its second run must not end at git."""
    plugin_create.start_branch(git_checkout, "add-edge_glow")
    subprocess.run(["git", "switch", "-q", "main"], cwd=git_checkout, check=True)

    with pytest.raises(PluginError, match="--branch") as refusal:
        plugin_create.start_branch(git_checkout, "add-edge_glow")
    assert "add-edge_glow" in str(refusal.value)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_branch_is_created_off_the_freshest_main(git_checkout):
    """Branching off HEAD would silently base a reused checkout on a stale main."""
    plugin_create.start_branch(git_checkout, "add-magenta_wash")
    current = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=git_checkout,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current == "add-magenta_wash"


# ----------------------------------------------------------------------
# End to end, through the CLI
# ----------------------------------------------------------------------


@pytest.fixture
def offline(checkout, monkeypatch):
    """`create` without git or GitHub: the checkout is there, branching is a no-op."""
    monkeypatch.setattr(
        plugin_create, "obtain_checkout", lambda directory, *, fork: (True, True)
    )
    monkeypatch.setattr(plugin_create, "start_branch", lambda directory, branch: None)
    monkeypatch.setattr(plugin_create, "git_identity", lambda _root: "Ada <a@b.com>")
    monkeypatch.setattr(
        plugin_create,
        "fork_readiness",
        lambda: plugin_create.ForkReadiness(plugin_create.FORK, "will fork"),
    )
    return checkout


def test_create_names_the_branch_after_the_plugin(offline):
    result = plugin_create.create("magenta_wash", IMAGE, directory=offline)
    assert result.branch == "add-magenta_wash"
    assert result.example == "hello_world_stamp"


def test_a_checkout_you_cannot_push_to_says_so(offline, monkeypatch):
    """Without a fork the last two steps printed would simply fail."""
    monkeypatch.setattr(
        plugin_create, "obtain_checkout", lambda directory, *, fork: (False, False)
    )
    result = plugin_create.create(
        "magenta_wash",
        IMAGE,
        directory=offline,
        readiness=plugin_create.ForkReadiness(
            plugin_create.MANUAL, "no gh", "Fork it on the web."
        ),
    )
    assert any("Fork it on the web." in warning for warning in result.warnings)


def test_a_maintainer_forks_like_everyone_else(offline, monkeypatch):
    """No shortcut for write access: a plugin arrives as a pull request or not.

    A path only maintainers take is a path only maintainers test, and it would
    be the one that quietly rots while the flow everyone else uses moves on.
    """
    monkeypatch.setattr(plugin_create.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(plugin_create, "_gh", lambda *_arguments: "signed in")
    assert plugin_create.fork_readiness().mode == plugin_create.FORK
    assert not hasattr(plugin_create, "PUSH")


def test_the_cli_hands_the_rest_to_submit_rather_than_listing_git_commands(
    offline, capsys
):
    """The six manual commands are `plugins submit`; listing them invites typos."""
    exit_code = cli.main(
        ["plugins", "create", "magenta_wash", "--kind", "image", "--dir", str(offline)]
    )
    assert exit_code == 0

    output = capsys.readouterr().out
    assert "plugins/image/magenta_wash/magenta_wash.py" in output
    assert "plugins submit" in output
    for by_hand in ("git add", "git commit", "git push", "gh pr create"):
        assert by_hand not in output
    # `plugins test` checks captioning plugins only, so sending an image plugin
    # there would send someone to a command that refuses them.
    assert "plugins test" not in output


def test_the_cli_sends_a_captioning_plugin_to_plugins_test(offline, capsys):
    exit_code = cli.main(
        ["plugins", "create", "tiny_vlm", "--kind", "captioning", "--dir", str(offline)]
    )
    assert exit_code == 0
    assert "plugins test plugins/captioning/tiny_vlm" in capsys.readouterr().out


def test_a_refusal_exits_non_zero_with_a_message(offline, capsys):
    exit_code = cli.main(
        ["plugins", "create", "pixelate", "--kind", "image", "--dir", str(offline)]
    )
    assert exit_code != 0
    assert "already ships" in capsys.readouterr().err


# ----------------------------------------------------------------------
# Fork readiness
# ----------------------------------------------------------------------


def test_no_gh_means_a_manual_fork_and_says_how(monkeypatch):
    monkeypatch.setattr(plugin_create.shutil, "which", lambda _name: None)
    readiness = plugin_create.fork_readiness()
    assert readiness.mode == plugin_create.MANUAL
    assert not readiness.can_fork
    assert "cli.github.com" in readiness.remedy


def test_gh_that_is_not_signed_in_says_to_sign_in(monkeypatch):
    monkeypatch.setattr(plugin_create.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(plugin_create, "_gh", lambda *_a: None)
    readiness = plugin_create.fork_readiness()
    assert readiness.mode == plugin_create.MANUAL
    assert "gh auth login" in readiness.remedy


def _fake_gh(asked: list[tuple[str, ...]], **answers: str | None):
    """Return a `_gh` stand-in recording what was asked of it."""

    def fake_gh(*arguments: str) -> str | None:
        asked.append(arguments)
        if arguments[:2] == ("api", "user"):
            return answers.get("login", "ada")
        if arguments[:2] == ("repo", "view"):
            return answers.get("view", None)
        return "signed in"

    return fake_gh


def test_the_fork_that_will_be_created_is_named_in_full(monkeypatch):
    """It makes a public repository on someone's account, so it has to say which."""
    asked: list[tuple[str, ...]] = []
    monkeypatch.setattr(plugin_create.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(plugin_create, "_gh", _fake_gh(asked, login="ada"))

    readiness = plugin_create.fork_readiness()
    assert readiness.mode == plugin_create.FORK
    assert readiness.fork_target == "ada/PixlStash-plugins"
    assert readiness.fork_exists is False
    assert "https://github.com/ada/PixlStash-plugins" in readiness.explanation
    assert "will create" in readiness.explanation
    # Never asks what permissions you hold: everyone forks, so the answer
    # could not change what happens next.
    assert not any(
        "viewerPermission" in argument for call in asked for argument in call
    )


def test_an_existing_fork_says_it_will_be_reused_rather_than_created(monkeypatch):
    """ "Will create" would be a lie, and the difference is the whole warning."""
    monkeypatch.setattr(plugin_create.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(
        plugin_create, "_gh", _fake_gh([], login="ada", view='{"name": "x"}')
    )

    readiness = plugin_create.fork_readiness()
    assert readiness.fork_exists is True
    assert "reused" in readiness.explanation
    assert "Nothing new is created" in readiness.explanation


def test_an_account_that_will_not_name_itself_admits_it(monkeypatch):
    """Better than inventing an owner for a repository about to be made."""
    monkeypatch.setattr(plugin_create.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(plugin_create, "_gh", _fake_gh([], login=None))

    readiness = plugin_create.fork_readiness()
    assert readiness.mode == plugin_create.FORK
    assert readiness.fork_target == ""
    assert "could not tell me which account" in readiness.explanation.lower()


# ----------------------------------------------------------------------
# What the contributor said the plugin is for
# ----------------------------------------------------------------------


def test_the_purpose_reaches_the_readme_the_docstring_and_the_description(checkout):
    """Asked once, used in three places: the wizard must not ask three times."""
    purpose = "Adds a soft glow to every edge. The strength is adjustable."
    _folder, module, readme, _brief, _warnings = plugin_create.scaffold(
        checkout, "edge_glow", IMAGE, example="hello_world_stamp", purpose=purpose
    )

    assert purpose in readme.read_text(encoding="utf-8")
    source = module.read_text(encoding="utf-8")
    assert "Adds a soft glow to every edge." in ast.get_docstring(ast.parse(source))
    # The description is one line in a settings dialog, so it takes the first
    # sentence rather than the paragraph.
    assert 'description = "Adds a soft glow to every edge."' in source


def test_an_explicit_description_beats_the_purpose(checkout):
    _folder, module, _readme, _brief, _warnings = plugin_create.scaffold(
        checkout,
        "edge_glow",
        IMAGE,
        example="hello_world_stamp",
        purpose="Adds a soft glow.",
        description="Something else entirely.",
    )
    assert 'description = "Something else entirely."' in module.read_text(
        encoding="utf-8"
    )


def test_no_purpose_leaves_a_todo_in_the_readme(checkout):
    _folder, _module, readme, _brief, _warnings = plugin_create.scaffold(
        checkout, "edge_glow", IMAGE, example="hello_world_stamp"
    )
    assert plugin_create.PURPOSE_TODO in readme.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# The agent hand-off
# ----------------------------------------------------------------------


def test_the_command_carries_its_own_directory(offline):
    """It is pasted, and a paste lands wherever the reader happens to be.

    The prompt names the plugin relative to the checkout and the agent reads
    the checkout's AGENTS.md, so run anywhere else it finds neither. Printing
    the directory as prose above the command is what let that happen: the
    command is what gets copied, so the command has to carry the `cd`.
    """
    result = plugin_create.create(
        "edge_glow", IMAGE, directory=offline, purpose="Adds a glow."
    )
    command = plugin_create.agent_command("claude", result)
    parts = shlex.split(command)

    assert parts[0] == "cd"
    assert Path(parts[1]) == offline
    assert parts[2] == "&&"
    assert parts[3] == "claude"
    # Still one quoted argument, so a prompt holding quotes survives the shell.
    assert len(parts) == 5


def test_the_prompt_is_short_enough_to_paste(offline):
    """It is the only part anyone copies, so the brief goes in a file instead."""
    result = plugin_create.create(
        "edge_glow", IMAGE, directory=offline, purpose="Adds a soft glow."
    )
    prompt = plugin_create.agent_prompt(result)

    assert "\n" not in prompt
    assert len(prompt) < 80
    assert "plugins/image/edge_glow/BRIEF.md" in prompt


def test_the_brief_says_what_to_build_and_that_the_folder_is_a_scaffold(offline):
    """The facts AGENTS.md cannot hold, in the file the prompt points at.

    AGENTS.md is written for "deliver one new folder". This folder is not new:
    it holds a renamed copy of an example. Without being told, an agent has no
    way to know the code beside it is a placeholder rather than a start, and
    reads the mismatch with the README as a documentation job.
    """
    result = plugin_create.create(
        "edge_glow", IMAGE, directory=offline, purpose="Adds a soft glow."
    )
    brief = result.brief.read_text(encoding="utf-8")

    assert result.brief.name == "BRIEF.md"
    assert result.brief.parent == result.folder
    assert "Adds a soft glow." in brief
    assert "hello_world_stamp" in brief
    assert "edge_glow.py" in brief
    # The instruction that answers what actually went wrong.
    assert "Working code is the deliverable" in brief
    # And it says to clean itself up.
    assert "This file is gone" in brief


def test_the_brief_sends_the_agent_to_agents_md_for_everything_general(offline):
    """The contract stays in the repository that maintains and tests it."""
    result = plugin_create.create("edge_glow", IMAGE, directory=offline)
    brief = result.brief.read_text(encoding="utf-8")

    assert "AGENTS.md" in brief
    assert "CLAUDE.md" in brief
    assert "does not repeat it" in brief


def test_a_plugin_with_no_stated_purpose_is_told_to_ask(offline):
    """Inventing a plugin is worse than stopping, when nobody said what it does."""
    result = plugin_create.create("edge_glow", IMAGE, directory=offline)
    assert "Stop and ask" in result.brief.read_text(encoding="utf-8")


def test_the_readme_points_at_the_brief(offline):
    """A reader opening the folder should not have to guess which file is which."""
    result = plugin_create.create(
        "edge_glow", IMAGE, directory=offline, purpose="Adds a soft glow."
    )
    readme = result.readme.read_text(encoding="utf-8")
    assert "BRIEF.md" in readme
    assert "Adds a soft glow." in readme


def test_an_agent_this_does_not_know_is_refused(offline):
    result = plugin_create.create("edge_glow", IMAGE, directory=offline)
    with pytest.raises(PluginError, match="not an agent"):
        plugin_create.agent_command("emacs", result)


def test_the_cli_prints_the_agent_command_when_asked(offline, capsys):
    exit_code = cli.main(
        [
            "plugins",
            "create",
            "edge_glow",
            "--kind",
            "image",
            "--dir",
            str(offline),
            "--purpose",
            "Adds a soft glow to every edge.",
            "--agent",
            "claude",
        ]
    )
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "claude " in output
    assert "Read what it writes before you commit it" in output


def test_a_non_interactive_run_still_says_how_it_will_reach_github(offline, capsys):
    """`origin: <upstream>` with nothing explaining it reads as a failure."""
    cli.main(
        ["plugins", "create", "edge_glow", "--kind", "image", "--dir", str(offline)]
    )
    assert "will fork" in capsys.readouterr().out


def test_the_cli_prints_no_agent_command_by_default(offline, capsys):
    exit_code = cli.main(
        ["plugins", "create", "edge_glow", "--kind", "image", "--dir", str(offline)]
    )
    assert exit_code == 0
    assert "codex" not in capsys.readouterr().out


# ----------------------------------------------------------------------
# The wizard
# ----------------------------------------------------------------------


@pytest.fixture
def answers(monkeypatch):
    """Feed the wizard's prompts from a list, as a person at a terminal would."""

    def feed(*replies: str) -> list[str]:
        asked: list[str] = []
        queued = list(replies)

        def fake_input(prompt: str = "") -> str:
            asked.append(prompt)
            if not queued:
                raise EOFError
            return queued.pop(0)

        monkeypatch.setattr("builtins.input", fake_input)
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
        return asked

    return feed


def test_the_wizard_asks_for_everything_it_was_not_given(offline, answers, capsys):
    asked = answers(
        "y",  # carry on without a fork
        "image",  # kind
        "Adds a soft glow to every edge.",  # purpose, first line
        "",  # purpose, blank line ends it
        "edge_glow",  # name
        "",  # licence, Enter takes MIT
        "no",  # no coding agent
    )
    exit_code = cli.main(["plugins", "create", "--no-fork", "--dir", str(offline)])

    assert exit_code == 0
    assert any("kind of plugin" in prompt or "Choose" in prompt for prompt in asked)
    readme = offline / "plugins" / "image" / "edge_glow" / "README.md"
    assert "Adds a soft glow to every edge." in readme.read_text(encoding="utf-8")
    assert "MIT, see the" in readme.read_text(encoding="utf-8")
    assert "add-edge_glow" in capsys.readouterr().out


def test_the_wizard_re_asks_a_name_it_cannot_use(offline, answers, capsys):
    """A refused name has to cost a question, not the whole run."""
    answers(
        "y",
        "image",
        "Adds a soft glow.",
        "",
        "Edge Glow",  # not snake_case
        "pixelate",  # a built-in
        "edge_glow",
        "",
        "no",
    )
    assert cli.main(["plugins", "create", "--no-fork", "--dir", str(offline)]) == 0
    output = capsys.readouterr().out
    assert "not a plugin name" in output
    assert "already ships" in output


def test_declining_at_the_fork_question_creates_nothing(offline, answers):
    answers("n")
    exit_code = cli.main(["plugins", "create", "--no-fork", "--dir", str(offline)])
    assert exit_code != 0
    assert not (offline / "plugins" / "image" / "edge_glow").exists()


def test_aborting_at_the_kind_question_forks_and_clones_nothing(
    offline, answers, monkeypatch, capsys
):
    """The abort has to come before the fork, or it is an abort of nothing.

    Asked first for exactly this reason: the next step puts a public
    repository on the contributor's account and a clone on their disk.
    """
    obtained: list[object] = []
    monkeypatch.setattr(
        plugin_create,
        "obtain_checkout",
        lambda directory, *, fork: obtained.append(directory) or (True, True),
    )
    answers("y", "abort")

    exit_code = cli.main(["plugins", "create", "--no-fork", "--dir", str(offline)])
    assert exit_code != 0
    assert obtained == []
    assert "Nothing was forked, cloned or created" in capsys.readouterr().out


def _choose(monkeypatch, *replies: str) -> str:
    """Run one `_ask_choice` over a three-option menu, answering with *replies*."""
    queued = iter(replies)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(queued))
    return cli._ask_choice(
        "Which?", {"image": "a picture", "captioning": "words", cli.ABORT: "stop"}
    )


@pytest.mark.parametrize(
    ("answer", "chosen"),
    [
        ("1", "image"),
        ("2", "captioning"),
        ("3", cli.ABORT),
        # Enter takes the first, which is why the safe option is never last by
        # accident: option 1 is the common case, and abort is chosen on purpose.
        ("", "image"),
        # The name is still an answer: a reader who has just read it off the
        # screen should not be told off for typing it.
        ("captioning", "captioning"),
        ("ABORT", cli.ABORT),
    ],
)
def test_an_option_is_chosen_by_number_or_by_name(monkeypatch, answer, chosen):
    assert _choose(monkeypatch, answer) == chosen


@pytest.mark.parametrize("wrong", ["0", "4", "9", "img", "x"])
def test_an_answer_that_is_not_an_option_asks_again(monkeypatch, wrong, capsys):
    """Off-by-one at either end included: `0` and `4` are not options."""
    assert _choose(monkeypatch, wrong, "2") == "captioning"
    assert "Choose 1 to 3" in capsys.readouterr().out


def test_the_kind_question_is_numbered_and_offers_the_way_out(offline, answers, capsys):
    answers("y", "3")
    cli.main(["plugins", "create", "--no-fork", "--dir", str(offline)])

    output = capsys.readouterr().out
    assert "1  image" in output
    assert "2  captioning" in output
    assert "3  abort" in output


def test_choosing_the_abort_number_forks_and_clones_nothing(
    offline, answers, monkeypatch, capsys
):
    """The number has to do what the word did, or the way out moved silently."""
    obtained: list[object] = []
    monkeypatch.setattr(
        plugin_create,
        "obtain_checkout",
        lambda directory, *, fork: obtained.append(directory) or (True, True),
    )
    answers("y", "3")

    exit_code = cli.main(["plugins", "create", "--no-fork", "--dir", str(offline)])
    assert exit_code != 0
    assert obtained == []
    assert "Nothing was forked, cloned or created" in capsys.readouterr().out


def test_ctrl_c_is_not_a_traceback(offline, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    exit_code = cli.main(["plugins", "create", "--no-fork", "--dir", str(offline)])
    assert exit_code != 0
    assert "Stopped." in capsys.readouterr().err


def test_the_wizard_offers_the_agent_command(offline, answers, capsys):
    answers("y", "image", "Adds a soft glow.", "", "edge_glow", "", "codex")
    assert cli.main(["plugins", "create", "--no-fork", "--dir", str(offline)]) == 0
    assert "codex " in capsys.readouterr().out


def test_a_missing_answer_with_no_terminal_is_refused_not_guessed(offline, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    exit_code = cli.main(["plugins", "create", "--dir", str(offline)])
    assert exit_code != 0


# ----------------------------------------------------------------------
# Submitting
# ----------------------------------------------------------------------


@pytest.fixture
def submittable(offline, monkeypatch):
    """A checkout with a scaffolded plugin on its branch, and git stubbed out."""
    result = plugin_create.create(
        "edge_glow", IMAGE, directory=offline, purpose="Adds a soft glow."
    )
    (offline / ".git").mkdir(exist_ok=True)

    calls: list[list[str]] = []

    def fake_run(command, *, cwd=None):
        calls.append(command)
        if command[:3] == ["git", "branch", "--show-current"]:
            return f"{result.branch}\n"
        if command[:3] == ["git", "diff", "--cached"]:
            return "plugins/image/edge_glow/edge_glow.py\n"
        if command[:3] == ["gh", "pr", "create"]:
            return "https://github.com/Pikselkroken/PixlStash-plugins/pull/9\n"
        return ""

    monkeypatch.setattr(plugin_create, "_run", fake_run)
    monkeypatch.setattr(plugin_create.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(plugin_create, "missing_tools", lambda _submission: [])
    monkeypatch.setattr(plugin_create, "run_checks", lambda _submission: [])
    return offline, calls


def test_the_plugin_is_found_from_the_branch_name(submittable):
    checkout, _calls = submittable
    submission = plugin_create.find_submission(checkout)
    assert submission.name == "edge_glow"
    assert submission.kind == IMAGE
    assert submission.folder == checkout / "plugins" / "image" / "edge_glow"


def test_a_branch_that_names_no_plugin_asks_for_the_name(offline, monkeypatch):
    (offline / ".git").mkdir(exist_ok=True)
    monkeypatch.setattr(plugin_create, "_run", lambda *a, **k: "main\n")
    with pytest.raises(PluginError, match="plugins submit <name>"):
        plugin_create.find_submission(offline)


def test_a_directory_that_is_not_a_checkout_is_refused(tmp_path):
    with pytest.raises(PluginError, match="not a checkout"):
        plugin_create.find_submission(tmp_path / "nowhere")


def test_only_the_plugin_folder_is_staged(submittable):
    """The checkout is the contributor's and may hold work of their own."""
    checkout, calls = submittable
    plugin_create.commit(plugin_create.find_submission(checkout), "Add edge_glow")

    staging = [command for command in calls if command[:2] == ["git", "add"]]
    assert staging == [["git", "add", "--", "plugins/image/edge_glow"]]


def test_the_pull_request_body_carries_what_was_tested_and_nothing_else(submittable):
    """A pull request is public and permanent; nothing off this machine goes in."""
    checkout, _calls = submittable
    submission = plugin_create.find_submission(checkout)
    body = plugin_create.pull_request_body(submission, "  moondream2 on a 3090  ")

    assert "moondream2 on a 3090" in body
    assert "plugins/image/edge_glow/README.md" in body
    # No absolute path, so no home directory and no clue about this machine.
    assert str(checkout) not in body


def test_a_failing_check_stops_before_anything_is_pushed(submittable, monkeypatch):
    checkout, calls = submittable
    monkeypatch.setattr(plugin_create, "run_checks", lambda _s: ["ruff check ."])

    exit_code = cli.main(
        ["plugins", "submit", "--dir", str(checkout), "--tested", "by hand", "--yes"]
    )
    assert exit_code != 0
    assert not any(command[:2] == ["git", "push"] for command in calls)


def test_missing_dev_tools_are_told_apart_from_a_failing_check(
    submittable, monkeypatch, capsys
):
    """`python -m nope` exits 1 like a test failure; saying so would be a lie."""
    checkout, _calls = submittable
    monkeypatch.setattr(plugin_create, "missing_tools", lambda _s: ["ruff", "pytest"])

    exit_code = cli.main(
        ["plugins", "submit", "--dir", str(checkout), "--tested", "by hand", "--yes"]
    )
    assert exit_code != 0
    error = capsys.readouterr().err
    assert "requirements-dev.txt" in error
    assert "failed" not in error


def test_dry_run_checks_and_stops(submittable):
    checkout, calls = submittable
    exit_code = cli.main(
        ["plugins", "submit", "--dir", str(checkout), "--dry-run", "--tested", "x"]
    )
    assert exit_code == 0
    assert not any(command[:2] == ["git", "commit"] for command in calls)
    assert not any(command[:2] == ["git", "push"] for command in calls)


def test_declining_the_confirmation_pushes_nothing(submittable, answers):
    checkout, calls = submittable
    answers("n")
    exit_code = cli.main(
        ["plugins", "submit", "--dir", str(checkout), "--tested", "by hand"]
    )
    assert exit_code != 0
    assert not any(command[:2] == ["git", "push"] for command in calls)


def test_a_full_submit_commits_pushes_and_opens_the_pull_request(submittable, capsys):
    checkout, calls = submittable
    exit_code = cli.main(
        [
            "plugins",
            "submit",
            "--dir",
            str(checkout),
            "--tested",
            "Pillow 11 on Linux, no model",
            "--yes",
        ]
    )
    assert exit_code == 0

    ran = [" ".join(command[:3]) for command in calls]
    assert "git add --" in ran
    assert "git commit -m" in ran
    assert "git push --set-upstream" in ran
    assert "gh pr create" in ran
    assert "https://github.com/Pikselkroken/PixlStash-plugins/pull/9" in (
        capsys.readouterr().out
    )


def test_an_empty_tested_answer_is_refused(submittable):
    """The one thing CI cannot check for a model-backed plugin."""
    checkout, calls = submittable
    exit_code = cli.main(
        ["plugins", "submit", "--dir", str(checkout), "--tested", "   ", "--yes"]
    )
    assert exit_code != 0
    assert not any(command[:2] == ["git", "push"] for command in calls)


def test_submit_asks_what_it_was_tested_against(submittable, answers, capsys):
    checkout, calls = submittable
    answers("Pillow 11 on Linux, no model", "", "y")
    exit_code = cli.main(["plugins", "submit", "--dir", str(checkout)])

    assert exit_code == 0
    assert any(command[:2] == ["git", "push"] for command in calls)
    assert "pull/9" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_commit_against_real_git_stages_only_the_plugin(tmp_path, monkeypatch):
    """The stubs above check the sequence; this checks the commands work.

    A wrong flag on `git add` or a misread of `git diff --cached` passes every
    mocked test and loses someone's unrelated work on the first real run.
    """
    directory = tmp_path / "repo"
    (directory / "plugins" / "image" / "edge_glow").mkdir(parents=True)
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
        ["git", "commit", "-q", "--allow-empty", "-m", "first"],
        ["git", "switch", "-q", "-c", "add-edge_glow"],
    ):
        subprocess.run(command, cwd=directory, check=True)

    (directory / "plugins" / "image" / "edge_glow" / "edge_glow.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    # Work of the contributor's own, which must survive untouched.
    (directory / "notes.txt").write_text("mine", encoding="utf-8")

    submission = plugin_create.find_submission(directory)
    assert submission.name == "edge_glow"
    assert submission.branch == "add-edge_glow"

    plugin_create.commit(submission, "Add edge_glow")
    committed = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert committed == ["plugins/image/edge_glow/edge_glow.py"]

    still_untracked = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "notes.txt" in still_untracked


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_committing_nothing_says_so_rather_than_making_an_empty_commit(
    tmp_path,
):
    directory = tmp_path / "repo"
    (directory / "plugins" / "image" / "edge_glow").mkdir(parents=True)
    (directory / "plugins" / "image" / "edge_glow" / "edge_glow.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "first"],
        ["git", "switch", "-q", "-c", "add-edge_glow"],
    ):
        subprocess.run(command, cwd=directory, check=True)

    submission = plugin_create.find_submission(directory)
    with pytest.raises(PluginError, match="nothing to commit"):
        plugin_create.commit(submission, "Add edge_glow")


def test_missing_tools_reports_what_this_interpreter_really_lacks(tmp_path):
    """Run against a real interpreter, since the point is not trusting exit codes."""
    submission = plugin_create.Submission(
        checkout=tmp_path,
        kind=IMAGE,
        name="edge_glow",
        folder=tmp_path / "plugins" / "image" / "edge_glow",
        branch="add-edge_glow",
        python=Path(sys.executable),
    )
    # This interpreter runs the suite, so pytest is importable by definition.
    assert "pytest" not in plugin_create.missing_tools(submission)


def _submission(checkout: Path) -> plugin_create.Submission:
    return plugin_create.Submission(
        checkout=checkout,
        kind=IMAGE,
        name="edge_glow",
        folder=checkout / "plugins" / "image" / "edge_glow",
        branch="add-edge_glow",
        python=Path(sys.executable),
    )


def test_the_setup_hint_prints_paths_this_platform_can_type(tmp_path, monkeypatch):
    """The hint is commands for a person to type, so `bin` is wrong on Windows.

    Forced to the other platform's layout rather than read off this one: a
    Unix-only spelling is invisible to a test that runs on Unix, and this
    suite is where a Windows-only defect has to be caught.
    """
    other = Path(".venv") / ("bin" if os.name == "nt" else "Scripts")
    monkeypatch.setattr(plugin_create, "VENV_BIN", other)
    hint = plugin_create.dev_setup_hint(_submission(tmp_path), ["ruff"])

    # Both commands the hint prints: the requirements install and pixlstash.
    # Two, not one, is what says neither line kept a hardcoded spelling --
    # the interpreter's own path is in the hint too and is legitimately this
    # platform's, so the check is on what it tells someone to type.
    assert hint.count(str(other / "pip")) == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(
    "remote, expected",
    [
        # The ordinary case: the branch is on the contributor's fork, which is
        # not a branch the upstream repository has.
        (
            "git@github.com:someone/pixlstash-plugins.git",
            "main...someone:add-edge_glow",
        ),
        (
            "https://github.com/someone/pixlstash-plugins",
            "main...someone:add-edge_glow",
        ),
        # A maintainer pushing to upstream itself compares within the repository.
        (
            f"https://github.com/{plugin_create.PLUGINS_REPO}.git",
            "main...add-edge_glow",
        ),
    ],
)
def test_the_manual_compare_url_names_the_repository_the_branch_is_on(
    tmp_path, remote, expected
):
    directory = tmp_path / "repo"
    directory.mkdir()
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "remote", "add", "origin", remote],
    ):
        subprocess.run(command, cwd=directory, check=True)

    assert plugin_create.compare_url(_submission(directory)) == (
        f"https://github.com/{plugin_create.PLUGINS_REPO}/compare/{expected}"
    )


def test_a_checkout_with_no_origin_still_gets_a_url(tmp_path):
    """A way out beats no way out: this branch is already handling a failure."""
    url = plugin_create.compare_url(_submission(tmp_path / "nowhere"))
    assert url.endswith("/compare/main...add-edge_glow")


def test_the_no_gh_message_points_at_the_branch_where_it_actually_is(
    tmp_path, monkeypatch
):
    """The way out of a missing `gh` is that URL, so it has to be the real one."""
    monkeypatch.setattr(plugin_create.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        plugin_create, "compare_url", lambda _s: "https://example.invalid/compare"
    )
    with pytest.raises(PluginError, match=r"https://example\.invalid/compare"):
        plugin_create.open_pull_request(_submission(tmp_path), "Add edge_glow", "body")


# ----------------------------------------------------------------------
# Nothing unparseable, and nothing half-written
# ----------------------------------------------------------------------


#: Long enough that `_header_literal` has to wrap it, which is what makes the
#: scaffolded plugin an example the header rewrite cannot read back with a
#: single-line pattern.
WRAPPING_PURPOSE = (
    "Give every picture a soft glow around its edges, much like a bloom "
    "filter would, without touching anything in the middle of the frame."
)


def test_a_plugin_scaffolded_from_a_scaffolded_plugin_still_parses(checkout):
    """`--from` a plugin this tool made is the guaranteed break B13 named.

    The first scaffold writes a wrapped `description`; the second has to
    replace all of it rather than its opening line, or the continuation lines
    and their `)` are left stranded in a module that will not import.
    """
    plugin_create.scaffold(
        checkout,
        "edge_glow",
        IMAGE,
        example="hello_world_stamp",
        purpose=WRAPPING_PURPOSE,
    )
    _folder, module, _readme, _brief, warnings = plugin_create.scaffold(
        checkout, "soft_glow", IMAGE, example="edge_glow", description="Short one."
    )

    source = module.read_text(encoding="utf-8")
    ast.parse(source)
    assert warnings == []
    assert 'description = "Short one."' in source
    # The wrapped value it replaced, gone in full rather than beheaded.
    assert "bloom" not in source


def test_a_quote_in_the_purpose_does_not_produce_a_broken_module(checkout):
    """Both halves of B13 at once: a wrapped header and a quoted docstring."""
    _folder, module, _readme, _brief, _warnings = plugin_create.scaffold(
        checkout,
        "edge_glow",
        IMAGE,
        example="hello_world_stamp",
        purpose=f'{WRAPPING_PURPOSE} Users call it "the halo", apparently.',
    )
    ast.parse(module.read_text(encoding="utf-8"))


def test_a_purpose_that_cannot_be_written_is_refused_rather_than_written(checkout):
    """A module that will not parse is a refusal, never a reported success."""
    with pytest.raises(PluginError, match="would not parse"):
        plugin_create.scaffold(
            checkout,
            "edge_glow",
            IMAGE,
            example="hello_world_stamp",
            purpose='Ends a docstring: """ and carries on.',
        )


def test_a_scaffold_that_fails_leaves_nothing_behind(checkout):
    """A half-written folder holds the example's code under your plugin's name."""
    folder = checkout / "plugins" / "image" / "edge_glow"
    with pytest.raises(PluginError):
        plugin_create.scaffold(
            checkout,
            "edge_glow",
            IMAGE,
            example="hello_world_stamp",
            purpose='Ends a docstring: """ and carries on.',
        )
    assert not folder.exists()

    # And so the retry is not refused for a folder this tool made itself.
    _folder, module, _readme, _brief, _warnings = plugin_create.scaffold(
        checkout, "edge_glow", IMAGE, example="hello_world_stamp", purpose="A glow."
    )
    assert module.exists()


def test_a_taken_name_is_refused_before_a_branch_is_made(offline, monkeypatch):
    """A refusal that leaves a branch behind is a refusal you have to clean up."""
    branched: list[str] = []
    monkeypatch.setattr(
        plugin_create,
        "start_branch",
        lambda _directory, branch: branched.append(branch),
    )
    # Taken by the captioning plugin, so the collision is the repository-wide
    # one rather than a folder already sitting where this would go.
    with pytest.raises(PluginError, match="is taken"):
        plugin_create.create("hello_world_captioner", IMAGE, directory=offline)
    assert branched == []


@pytest.mark.skipif(
    importlib.util.find_spec("ruff") is None, reason="needs ruff to format with"
)
def test_the_checks_never_rewrite_the_contributors_checkout(tmp_path, monkeypatch):
    """`submit` is a gate in front of a push, and a gate reads.

    Formatting the checkout would rewrite files the contributor owns and that
    `commit` does not stage, before the confirmation is asked and even under
    `--dry-run`.
    """
    checkout = tmp_path / "checkout"
    (checkout / "plugins" / "image" / "edge_glow").mkdir(parents=True)
    unrelated = checkout / "something_of_their_own.py"
    unrelated.write_text("x   =    1\n", encoding="utf-8")

    # Only the formatting check, taken from the real tuple rather than written
    # out here: `pytest -q` on a directory that is not a repository is slow and
    # is not what this is about.
    monkeypatch.setattr(
        plugin_create,
        "CHECKS",
        tuple(check for check in plugin_create.CHECKS if "format" in check[1]),
    )
    submission = plugin_create.Submission(
        checkout,
        IMAGE,
        "edge_glow",
        checkout / "plugins" / "image" / "edge_glow",
        "add-edge_glow",
        Path(sys.executable),
    )
    failed = plugin_create.run_checks(submission)

    assert failed, "an unformatted checkout should fail the check"
    assert unrelated.read_text(encoding="utf-8") == "x   =    1\n"


def test_the_approved_resolution_is_the_one_installed(monkeypatch):
    """B15: `pip install -r` would resolve again, free to pick other versions.

    Lives here rather than beside `plugins install` because it is the second
    half of the same refusal: `_report_dependencies` promises the user that
    nothing they were not shown will be replaced, and only pinning what was
    resolved keeps that promise.
    """
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(plugin_install.subprocess, "run", fake_run)
    plugin_install.install_requirements(
        [
            plugin_install.DependencyChange("something", "1.0"),
            plugin_install.DependencyChange("torch", "2.4.0", installed="2.9.0"),
        ]
    )

    (command,) = commands
    assert "--no-deps" in command
    assert command[-2:] == ["something==1.0", "torch==2.4.0"]
    # The file is never handed to pip again: reading it is a second resolution.
    assert not any(argument == "-r" for argument in command)
