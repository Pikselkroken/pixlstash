"""Start a new plugin as a pull request against the plugins repository.

Backs ``pixlstash-cli plugins create``.  The repository README already
documents the manual procedure - clone, ``cp -r`` an example, rename the
folder, rename the module, rewrite the header and the README - and every step
of it is a place to get it wrong quietly: a folder under the wrong kind, an
image plugin whose ``.py`` no longer matches its folder, a header still
crediting the example's author.  This module performs that procedure.

**The skeleton is the example plugin in the checkout, not a copy kept here.**
A template maintained in this repository would be a second definition of a
contract owned by another repository, and the two would drift the first time
its contract tests changed.  The example CI keeps green is the one that gets
copied, so a scaffold cannot be stale.

Nothing here imports the plugin: the example is read with :mod:`ast` through
:mod:`pixlstash.plugin_install`, the same way installing reads it.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

# The private names are the shared definitions of the repository's layout and
# of what a plugin name may be. Importing them is the point: a second copy of
# `plugins/<kind>/<slug>` here would be a second thing to get wrong when the
# repository moves a folder.
from pixlstash.plugin_install import (
    CAPTIONING,
    IMAGE,
    PLUGINS_REPO,
    PluginClass,
    PluginError,
    _analyse,
    _declared_name,
    _NAME_RE,
    _published_dirs,
    builtin_names,
    read_source,
)

logger = logging.getLogger(__name__)

#: What each kind is scaffolded from when ``--from`` is not given.  Both load
#: no model, so a contributor can run the copy before changing a line of it.
DEFAULT_EXAMPLES = {
    CAPTIONING: "hello_world_captioner",
    IMAGE: "hello_world_stamp",
}

#: Where a clone lands when ``--dir`` is not given, relative to the working
#: directory: the name ``git clone`` would have chosen itself.
DEFAULT_CHECKOUT = "PixlStash-plugins"

CLONE_URL = f"https://github.com/{PLUGINS_REPO}.git"

# "Your Name <you@example.com>" is the shape the repository's contract tests
# require, so a placeholder has to satisfy it too; it is only ever used when
# the machine has no git identity to read.
_PLACEHOLDER_AUTHOR = "Your Name <you@example.com>"
_PLACEHOLDER_DESCRIPTION = "TODO: one line saying what this plugin does."


@dataclass
class CreateResult:
    """What ``plugins create`` produced, for the CLI to describe."""

    checkout: Path
    folder: Path
    module: Path
    readme: Path
    #: The scaffolding brief the agent command points at.
    brief: Path
    branch: str
    kind: str
    name: str
    example: str
    #: True when ``origin`` is the contributor's own fork, so pushing works.
    forked: bool
    #: True when the checkout was already there and was reused.
    reused: bool
    #: What the contributor said the plugin should do, verbatim. Carried here
    #: because it is the instruction: the agent prompt needs it, and reading it
    #: back out of the README means reading it out of a file that also holds
    #: TODOs the agent then has to tell apart from the brief.
    purpose: str = ""
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# git and gh
# ----------------------------------------------------------------------


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    """Run *command*, returning its stdout and turning a failure into a refusal."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise PluginError(f"could not run {command[0]}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise PluginError(
            f"{' '.join(command[:2])} failed (exit {result.returncode})"
            + (f": {detail[-1]}" if detail else "")
        )
    return result.stdout


def _have_gh() -> bool:
    """Return whether a signed-in ``gh`` is available to fork with."""
    return shutil.which("gh") is not None and _gh("auth", "status") is not None


#: How the contributor will get their branch onto GitHub.  Everyone forks,
#: maintainers included: a plugin arrives as a pull request from a fork, and a
#: tool that quietly gives some people a shorter route is a tool whose main
#: path stops being the one anybody tests.
FORK = "fork"
MANUAL = "manual"


@dataclass
class ForkReadiness:
    """Whether a fork can be made, and what to say when it cannot."""

    mode: str
    #: One line naming the situation, for the wizard to print before it asks.
    explanation: str
    #: What the contributor should do about it, when there is anything to do.
    remedy: str = ""
    #: The repository that will be created, as ``owner/name``, when one will
    #: be. Named rather than implied: this command makes a public repository on
    #: someone's account, and "it will fork it for you" does not say where.
    fork_target: str = ""
    #: True when *fork_target* is already there and will be reused rather than
    #: created, which is the difference between a new repository appearing on
    #: your account and nothing appearing at all.
    fork_exists: bool = False

    @property
    def can_fork(self) -> bool:
        """Whether a fork can be made, and so whether pushing will work."""
        return self.mode == FORK


def _gh(*arguments: str) -> str | None:
    """Run a ``gh`` command, returning its stdout or None if it did not work."""
    try:
        result = subprocess.run(
            ["gh", *arguments], capture_output=True, text=True, check=False
        )
    except OSError:
        # No `gh` on this machine. The caller has a fallback for every use of
        # this, so a missing binary is an answer rather than a failure.
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def fork_readiness() -> ForkReadiness:
    """Work out how this machine can contribute, before anything is cloned.

    Asked up front rather than discovered by a failed ``gh repo fork`` halfway
    through, because being told at the end that you cannot push is being told
    too late to do anything about it.  Both probes are reads, and neither asks
    what permissions you hold: everyone contributes from a fork, so the answer
    would not change what happens next.
    """
    if shutil.which("gh") is None:
        return ForkReadiness(
            MANUAL,
            "`gh` is not installed, so this cannot fork the repository for you.",
            f"Clone now and fork https://github.com/{PLUGINS_REPO} on the web "
            "when you are ready to push, then point `origin` at your fork. Or "
            "install the GitHub CLI (https://cli.github.com) and run this again.",
        )
    if _gh("auth", "status") is None:
        return ForkReadiness(
            MANUAL,
            "`gh` is installed but not signed in, so it cannot fork for you.",
            "Run `gh auth login`, then run this again. Or clone now and fork "
            f"https://github.com/{PLUGINS_REPO} on the web later.",
        )

    login = _gh("api", "user", "--jq", ".login")
    if not login:
        # Signed in, but the account will not name itself. The fork will still
        # work; only the sentence describing it has to admit what it does not
        # know, rather than inventing an owner.
        return ForkReadiness(
            FORK,
            f"`gh` will fork {PLUGINS_REPO} to your GitHub account. It could "
            "not tell me which account that is.",
        )

    target = f"{login}/{PLUGINS_REPO.split('/')[1]}"
    exists = _gh("repo", "view", target, "--json", "name") is not None
    if exists:
        explanation = (
            f"You already have a fork at https://github.com/{target}, and it "
            "will be reused. Nothing new is created on your account."
        )
    else:
        explanation = (
            f"This will create https://github.com/{target}, a public fork of "
            f"{PLUGINS_REPO} on your account, and clone it."
        )
    return ForkReadiness(FORK, explanation, fork_target=target, fork_exists=exists)


def obtain_checkout(directory: Path, *, fork: bool) -> tuple[bool, bool]:
    """Make sure *directory* is a checkout of the plugins repository.

    Returns ``(forked, reused)``.  A fork is how a plugin reaches the
    repository, so it is the default and the plain clone is the fallback rather
    than the other way round.  A fork that cannot be made is still worth a
    checkout: the contributor can write the plugin now and sort the remote out
    before pushing, which is what the warning tells them to do.
    """
    if (directory / ".git").exists():
        # Reuse rather than refuse: creating a second plugin is a plausible
        # gesture, and the checkout already has the remotes set up.
        return _has_fork_remote(directory), True
    if directory.exists() and any(directory.iterdir()):
        raise PluginError(
            f"{directory} already exists and is not a checkout of "
            f"{PLUGINS_REPO}. Pass --dir to clone somewhere else."
        )

    directory.parent.mkdir(parents=True, exist_ok=True)
    if fork and _have_gh():
        try:
            # Everything after `--` reaches `git clone`, so this is one command
            # for fork, clone, `origin` = the fork and `upstream` = here.
            _run(
                ["gh", "repo", "fork", PLUGINS_REPO, "--clone", "--", str(directory)],
                cwd=directory.parent,
            )
            return True, False
        except PluginError as exc:
            # A fork that cannot be made is not a reason to stop: the clone
            # below still gives a working checkout, and the caller says so.
            # Logged rather than dropped, because the caller's warning can only
            # say "you cannot push" - the reason gh gave is the part that says
            # why, and it is the only place it is ever offered.
            logger.warning(
                "Could not fork %s (%s), so %s was cloned from upstream "
                "instead and cannot be pushed to.",
                PLUGINS_REPO,
                exc,
                directory,
            )
            shutil.rmtree(directory, ignore_errors=True)

    _run(["git", "clone", CLONE_URL, str(directory)])
    return False, False


def _has_fork_remote(directory: Path) -> bool:
    """Return whether ``origin`` in *directory* is something other than upstream.

    ``gh repo fork`` leaves ``origin`` pointing at the fork and ``upstream`` at
    the source, so a second remote is what says a push will land somewhere the
    contributor can push.

    A probe, not a command: a checkout git will not answer for is one we cannot
    say is a fork, and "no" is the safe answer - it costs a warning telling the
    contributor to check their remotes, where raising would stop the scaffold.
    """
    listed = subprocess.run(
        ["git", "remote"], cwd=directory, capture_output=True, text=True, check=False
    )
    remotes = listed.stdout.split() if listed.returncode == 0 else []
    return "origin" in remotes and "upstream" in remotes


def start_branch(directory: Path, branch: str) -> None:
    """Create *branch* off the freshest main the checkout can see.

    Branching off ``HEAD`` would be right for a clone made a second ago and
    wrong for the reused checkout that made last month's plugin, where it
    quietly bases the pull request on a stale main. ``upstream`` first, because
    a fork's own ``main`` is the copy that goes stale.
    """
    # Checked before branching rather than left to git, because reusing a
    # checkout is a supported gesture and this is what it hits on the second
    # run: git's own message names the branch but not the way out.
    if (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=directory,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    ):
        raise PluginError(
            f"{directory} already has a branch called {branch!r}, from an "
            "earlier run. Carry on there (`git switch "
            f"{branch}`), delete it (`git branch -D {branch}`), or pass "
            "--branch to use another name."
        )
    subprocess.run(["git", "fetch", "--all", "--quiet"], cwd=directory, check=False)
    for base in ("upstream/main", "origin/main"):
        if (
            subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
                cwd=directory,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        ):
            _run(["git", "switch", "-c", branch, base], cwd=directory)
            return
    _run(["git", "switch", "-c", branch], cwd=directory)


def git_identity(directory: Path) -> str | None:
    """Return ``Name <email>`` from git config, or None if it is not set.

    The header the repository requires is exactly the shape git already holds,
    so the one credit a contributor should never have to type is the one they
    configured years ago.
    """

    def configured(key: str) -> str:
        try:
            return subprocess.run(
                ["git", "config", key],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
        except OSError:
            # No git on this machine. Not fatal here: the caller falls back to
            # the placeholder and the contributor edits one line.
            return ""

    name, email = configured("user.name"), configured("user.email")
    return f"{name} <{email}>" if name and email else None


# ----------------------------------------------------------------------
# Scaffolding
# ----------------------------------------------------------------------


def class_name_for(name: str) -> str:
    """Return the CamelCase class name for a snake_case plugin *name*."""
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)


def display_name_for(name: str) -> str:
    """Return a readable default label for a snake_case plugin *name*."""
    return " ".join(part[:1].upper() + part[1:] for part in name.split("_") if part)


def first_sentence(text: str | None) -> str:
    """Return the first sentence of *text*, short enough to be a description.

    The wizard asks what the plugin should do once and puts the answer in two
    places: the README paragraph whole, and the class's one-line
    ``description`` trimmed to its first sentence.  Asking twice for the same
    thing in two lengths is how a wizard starts feeling like a form.
    """
    joined = " ".join((text or "").split())
    if not joined:
        return ""
    sentence = re.split(r"(?<=[.!?])\s", joined, maxsplit=1)[0]
    # ponytail: a plain clamp, matching `plugin_install._summary`. A first
    # sentence longer than this is one the contributor should shorten anyway.
    return sentence if len(sentence) <= 200 else sentence[:197] + "..."


def check_name(name: str, kind: str) -> None:
    """Raise unless *name* is one a plugin of *kind* can actually be called.

    The two checks that do not need a checkout, so the wizard can run them
    while it is still in a position to ask again rather than after a clone.
    The repository-wide collision needs the checkout and stays in `scaffold`.
    """
    if not _NAME_RE.match(name):
        raise PluginError(
            f"{name!r} is not a plugin name. Names are lower-case, start with "
            "a letter, and hold only letters, digits and underscores."
        )
    if name in builtin_names(kind):
        raise PluginError(
            f"PixlStash already ships a {kind} plugin called {name!r}, and a "
            "built-in wins the collision, so yours would never load. Pick "
            "another name."
        )


def taken_names(checkout: Path) -> set[str]:
    """Return every name the repository already publishes, folder or declared.

    Both, because either one is a collision: the folder name is what a
    contributor sees in the tree, and the declared name is what the plugin
    installs and loads under.
    """
    published = _published_dirs(checkout)
    return {_declared_name(entry) for entry in published} | {
        entry.name for entry in published
    }


def check_name_free(checkout: Path, name: str, kind: str) -> None:
    """Raise unless *name* is usable and nothing in *checkout* already has it.

    Separate from `check_name` because this one needs the checkout: it is the
    check the wizard runs against the clone it has just made, so a taken name
    costs one more question rather than an error after every question.
    """
    check_name(name, kind)
    # Before the collision check, which would otherwise see this very folder as
    # a plugin the repository publishes and give the less useful of the two
    # answers: "the name is taken" when what happened is you already made it.
    folder = checkout / "plugins" / kind / name
    if folder.exists():
        raise PluginError(
            f"{folder} already exists, from an earlier run of this command or "
            "from work of your own. Carry on there, delete it, or pick "
            "another name."
        )
    if name in taken_names(checkout):
        raise PluginError(
            f"the name {name!r} is taken: {PLUGINS_REPO} already publishes a "
            "plugin called that. Names are unique across the repository, so a "
            "pull request adding a second one is turned down. Pick another."
        )


def find_example(checkout: Path, kind: str, example: str) -> tuple[Path, PluginClass]:
    """Return the example folder and the plugin class declared in it."""
    candidates = [
        folder
        for folder in _published_dirs(checkout)
        if folder.name == example or _declared_name(folder) == example
    ]
    if not candidates:
        published = ", ".join(sorted(f.name for f in _published_dirs(checkout)))
        raise PluginError(
            f"{checkout} publishes no plugin called {example!r}. "
            f"Available: {published or '(none)'}"
        )
    folder = candidates[0]

    found = [
        entry
        for source in sorted(folder.glob("*.py"))
        for entry in _analyse(read_source(source), source, strict=False)
    ]
    if not found:
        raise PluginError(f"{folder} declares no plugin class to copy.")
    primary = found[0]
    if primary.kind != kind:
        raise PluginError(
            f"{example} is a {primary.kind} plugin, so it cannot be the "
            f"starting point for a {kind} one. Drop --from to use "
            f"{DEFAULT_EXAMPLES[kind]}."
        )
    return folder, primary


#: Ruff's default line length, which the plugins repository does not change.
_LINE_LENGTH = 88


def _header_literal(field_name: str, value: str, width: int = _LINE_LENGTH) -> str:
    """Return ``    field = "value"``, wrapped across lines if it will not fit.

    A description written as a sentence - which is what the wizard asks for -
    routinely overruns the line length, and ``ruff format`` would then want to
    reformat a file nobody has touched yet.  Adjacent string literals are one
    constant to :mod:`ast`, so the header readers see no difference.
    """
    single = f"    {field_name} = {json.dumps(value)}"
    if len(single) <= width:
        return single

    chunks: list[str] = []
    current = ""
    for word in value.split():
        candidate = f"{current} {word}" if current else word
        # 8 for the continuation indent; the trailing space joins this chunk to
        # the next one, and has to fit too.
        if current and len(json.dumps(f"{candidate} ")) + 8 > width:
            chunks.append(f"{current} ")
            current = word
        else:
            current = candidate
    chunks.append(current)

    body = "\n".join(f"        {json.dumps(chunk)}" for chunk in chunks)
    return f"    {field_name} = (\n{body}\n    )"


#: One header assignment in a class body, whether it is written on one line or
#: wrapped into a bracketed run of adjacent literals.  The bracketed branch is
#: lazy and demands the closing `    )` at the class body's own indent, so it
#: stops at this field's own bracket rather than swallowing the ones below it.
_WRAPPABLE_FIELD = r"^    {field} = (?:\((?:\n[^\n]*)*?\n    \)|[^\n]*)$"


def _rewrite_header(source: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """Replace the example's header values with *values*, reporting misses.

    Anchored on the four-space indent of a class body so it cannot touch a
    module-level constant of the same name.  A field that is not found is a
    warning rather than a refusal: the scaffold is still usable, and the one
    thing that must not happen is shipping a pull request that silently credits
    the example's author.

    The value being replaced may itself be wrapped across lines, because
    `_header_literal` writes it that way whenever it will not fit and the
    example being copied may well be a plugin this tool scaffolded.  Matching
    only the first physical line would leave the old continuation lines and
    their closing bracket behind, in a module that no longer parses.
    """
    warnings: list[str] = []
    for field_name, value in values.items():
        # `json.dumps` rather than quotes round the value, and a callable
        # rather than a template: a name holding a quote, a backslash or a
        # newline would otherwise produce a file that will not parse, and a
        # `\g` in it would be read as a group reference by `re`.
        literal = _header_literal(field_name, value)
        source, count = re.subn(
            _WRAPPABLE_FIELD.format(field=field_name),
            lambda _match, literal=literal: literal,
            source,
            count=1,
            flags=re.MULTILINE,
        )
        if not count:
            warnings.append(
                f"could not find `{field_name}` in the example, so it still "
                "holds whatever the example declared; set it by hand."
            )
    return source, warnings


def _refuse_unparseable(source: str) -> None:
    """Raise unless the rewritten module is still Python.

    The last check before anything is written.  Everything this module
    substitutes is text the contributor typed - a purpose holding ``\"\"\"`` or
    ending in a backslash closes the docstring early, and a header rewrite that
    misses part of what it was replacing leaves the rest of it stranded - and
    all of it lands in a file the tool then reports as a success.  Refusing
    here is the difference between one error naming the field and a pull
    request that will not import.
    """
    try:
        ast.parse(source)
    except SyntaxError as exc:
        raise PluginError(
            f"the scaffolded module would not parse: {exc.msg} "
            f"(line {exc.lineno}), from {_blame(source, exc.lineno)}. "
            "Nothing was written. Give that field a plainer value and run "
            "this again."
        ) from exc


def _blame(source: str, lineno: int | None) -> str:
    """Name what was being written where *lineno* is, for a refusal to quote.

    The nearest header assignment at or above the failure, because that is what
    the rewrite put there; nothing above it means the failure is in the module
    docstring, which is the only other thing this tool writes.
    """
    for line in reversed(source.splitlines()[: lineno or 0]):
        match = re.match(r"^    (\w+) = ", line)
        if match:
            return f"`{match[1]}`"
    return "the module docstring, which holds --purpose"


def _replace_docstring(source: str, text: str) -> str:
    """Replace the module docstring with *text*, or add one if there is none."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    lines = source.splitlines(keepends=True)
    docstring = f'"""{text}"""\n'
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        node = tree.body[0]
        return (
            "".join(lines[: node.lineno - 1])
            + docstring
            + "".join(lines[node.end_lineno :])
        )
    return docstring + "\n" + source


#: What goes under "What it should do" when the wizard did not ask, or the
#: answer was empty. A TODO is honest: the repository requires a README that
#: describes the plugin, and nothing here knows what this one will do.
PURPOSE_TODO = (
    "TODO: say what this plugin should do, in a sentence or three. Nothing "
    "else in this folder knows, so an agent sent here will have to ask."
)

#: The file the agent is pointed at, written into the plugin folder.
#:
#: On disk rather than in the command because a prompt has to be pasted, and
#: nobody pastes four paragraphs: the command stays one short line and the
#: brief goes where it can be read, re-read and edited before anyone runs
#: anything. Being a file also makes it worth writing properly, where a shell
#: argument is only ever worth keeping short.
BRIEF_NAME = "BRIEF.md"

BRIEF = """# Brief: {name}

A PixlStash {kind} plugin. Build it in this folder.

## Read this first

`AGENTS.md` at the root of this repository, and `CLAUDE.md`, which is the same
file under the other name, are the general brief for writing a plugin here:
the contract to follow, which guide in `docs/` covers this kind, the six header
fields, what a plugin README needs, the dependency rules and the house style.
That file is maintained and tested by this repository, so it wins wherever
anything disagrees with it. This brief does not repeat it. It covers only what
that file cannot know: which plugin this is, and what it is for.

## What it has to do

{purpose}

## Where you are starting from

This folder is a copy of the `{example}` example with the names changed, so
`{module}` currently does what that example does. Replace it: none of the
example's behaviour, parameters or constants should survive.

## What done looks like

- `{module}` does what this brief says.
- `README.md` describes the plugin you built, with no TODO left in it: what it
  does, its dependencies, one row per parameter, the models it loads and its
  license.
- `ruff format .`, `ruff check .` and `pytest` pass from the root of this
  repository.
- This file is gone. It is scaffolding, not part of the plugin.

Working code is the deliverable. A README explaining that the plugin is not
implemented is not.
"""

#: Written into the brief when nobody said what the plugin is for. Stopping is
#: the right answer: a plugin invented to fill a silence is worse than none.
BRIEF_NO_PURPOSE = (
    "**Nobody said.** Whoever scaffolded this folder did not record what the "
    "plugin should do, and nothing here knows. Stop and ask rather than "
    "inventing one."
)

README = """# {display_name}

> Not written yet. `{brief}` in this folder is the brief, and the code beside
> it is still the example it was copied from.

## What it should do

{purpose}

## Install

```bash
pixlstash-cli plugins install {name}
```

## Dependencies

TODO: what has to be installed into the environment PixlStash runs in, and
nothing if the answer is nothing. List the same packages in `requirements.txt`.

## Parameters

| Parameter | Type | Default | Meaning |
|-----------|------|---------|---------|
| TODO | | | |

## Models

TODO: every model or remote service this plugin loads, and its license. Say
"none" if it loads none. This is the part a user cannot find out for
themselves, and your own license says nothing about it.

## License

{license}, see the [LICENSE](../../../LICENSE) at the repository root.
"""


def scaffold(
    checkout: Path,
    name: str,
    kind: str,
    *,
    example: str,
    display_name: str | None = None,
    description: str | None = None,
    purpose: str | None = None,
    author: str | None = None,
    plugin_license: str = "MIT",
) -> tuple[Path, Path, Path, Path, list[str]]:
    """Copy the example into ``plugins/<kind>/<name>/`` and make it *name*'s.

    Returns ``(folder, module, readme, brief, warnings)``.
    """
    check_name(name, kind)
    check_name_free(checkout, name, kind)
    folder = checkout / "plugins" / kind / name
    source_folder, primary = find_example(checkout, kind, example)
    shutil.copytree(
        source_folder, folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )

    try:
        # An image plugin's folder holds one `.py` named after it - the loader
        # scans for files and never sees the folder - so the rename is part of
        # the contract rather than tidiness.
        module = folder / primary.file.name
        if module.name != "__init__.py":
            renamed = folder / f"{name}.py"
            module.rename(renamed)
            module = renamed

        label = display_name or display_name_for(name)
        source = read_source(module)
        source = source.replace(primary.name, name)
        source = source.replace(primary.class_name, class_name_for(name))
        # What the contributor said the plugin is for goes here as well as in
        # the README: this is the file they - or the coding agent they hand it
        # to - will have open, and a docstring still saying TODO next to a
        # README that does not is the pair that gets left inconsistent.
        said = " ".join((purpose or "").split())
        body = (
            "\n".join(textwrap.wrap(said, width=79))
            if said
            else "TODO: what it does, and how."
        )
        source = _replace_docstring(
            source, f"{label} plugin for PixlStash.\n\n{body}\n"
        )
        source, warnings = _rewrite_header(
            source,
            {
                "display_name": label,
                "description": description
                or first_sentence(purpose)
                or _PLACEHOLDER_DESCRIPTION,
                "author": author or git_identity(checkout) or _PLACEHOLDER_AUTHOR,
                "license": plugin_license,
            },
        )
        _refuse_unparseable(source)
        module.write_text(source, encoding="utf-8")

        # Written fresh rather than rewritten: the example's README describes
        # the example, and every sentence of it left in place is a sentence a
        # reviewer has to notice is a lie.
        readme = folder / "README.md"
        readme.write_text(
            README.format(
                display_name=label,
                name=name,
                brief=BRIEF_NAME,
                license=plugin_license,
                purpose=(purpose or "").strip() or PURPOSE_TODO,
            ),
            encoding="utf-8",
        )

        brief = folder / BRIEF_NAME
        brief.write_text(
            BRIEF.format(
                name=name,
                kind=kind,
                example=example,
                module=module.name,
                purpose=(purpose or "").strip() or BRIEF_NO_PURPOSE,
            ),
            encoding="utf-8",
        )
    except Exception:
        # Everything above the copy is a check; everything below it can still
        # fail, and what it leaves behind is the example's code under this
        # plugin's name with the example author's header on it - the exact
        # pull request this module exists to stop anyone opening. Removed so
        # the retry finds nothing rather than a folder it has to refuse.
        logger.warning("Scaffolding %s failed; removing %s.", name, folder)
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder, module, readme, brief, warnings


#: The coding agents `plugins create` can hand the job to, by the command that
#: starts one.  Both take the prompt as a single argument and run in the
#: working directory, which is why one prompt serves both.
AGENTS = {
    "claude": "claude",
    "codex": "codex",
}

#: One short line, because it gets pasted. Everything it would otherwise have
#: had to say is in the brief that `scaffold` writes into the plugin folder:
#: what to build, that the folder holds a renamed copy of an example rather
#: than a start, and that code rather than an apology is the deliverable. A
#: file can be read before it is run and edited before it is obeyed; a shell
#: argument can only be squinted at.
AGENT_PROMPT = "Read {brief} and do what it says."


def agent_prompt(result: CreateResult) -> str:
    """Return the prompt that points a coding agent at this plugin's brief."""
    return AGENT_PROMPT.format(
        brief=result.brief.relative_to(result.checkout).as_posix()
    )


def agent_command(agent: str, result: CreateResult) -> str:
    """Return a pasteable shell command running *agent* on this plugin.

    It carries its own ``cd``.  The prompt names the plugin by a path relative
    to the checkout, and the agent reads the checkout's ``AGENTS.md``, so both
    are wrong anywhere else; printing the directory as prose above the command
    put one line between a reader and a paste, and the paste is what gets used.
    """
    if agent not in AGENTS:
        raise PluginError(
            f"{agent!r} is not an agent this knows how to call. "
            f"Choose one of: {', '.join(sorted(AGENTS))}."
        )
    return (
        f"cd {shlex.quote(str(result.checkout))} && "
        f"{AGENTS[agent]} {shlex.quote(agent_prompt(result))}"
    )


def create(
    name: str,
    kind: str,
    *,
    directory: Path,
    example: str | None = None,
    branch: str | None = None,
    display_name: str | None = None,
    description: str | None = None,
    purpose: str | None = None,
    author: str | None = None,
    plugin_license: str = "MIT",
    fork: bool = True,
    readiness: ForkReadiness | None = None,
    checkout_state: tuple[bool, bool] | None = None,
) -> CreateResult:
    """Clone or reuse a checkout, branch, and scaffold *name* into it.

    *readiness* is passed in by the wizard, which has already probed it and
    shown the contributor what it found; anything else lets this probe for
    itself.  It decides both whether to try forking and whether the "you cannot
    push" warning is true, which is why one value does both rather than a bool
    saying to fork and a separate guess about what that meant.
    """
    check_name(name, kind)
    directory = directory.expanduser()
    example = example or DEFAULT_EXAMPLES[kind]
    branch = branch or f"add-{name}"
    if readiness is None:
        readiness = (
            fork_readiness()
            if fork
            else ForkReadiness(MANUAL, "--no-fork was given, so nothing was forked.")
        )

    forked, reused = (
        checkout_state
        if checkout_state is not None
        else obtain_checkout(directory, fork=readiness.can_fork)
    )
    # Before the branch rather than inside `scaffold`, which is where the same
    # check runs on the way past: a name the repository already publishes would
    # otherwise be discovered one step too late, leaving a branch behind in the
    # contributor's checkout that the refusal never mentions.
    check_name_free(directory, name, kind)
    start_branch(directory, branch)
    folder, module, readme, brief, warnings = scaffold(
        directory,
        name,
        kind,
        example=example,
        display_name=display_name,
        description=description,
        purpose=purpose,
        author=author,
        plugin_license=plugin_license,
    )
    if not forked:
        remedy = readiness.remedy or (
            f"Fork https://github.com/{PLUGINS_REPO} on the web, then point "
            "`origin` at your fork before pushing."
        )
        warnings.append(
            "`origin` is the upstream repository rather than your fork, so "
            f"you cannot push to it. {remedy}"
        )
    return CreateResult(
        checkout=directory,
        folder=folder,
        module=module,
        readme=readme,
        brief=brief,
        branch=branch,
        kind=kind,
        name=name,
        example=example,
        forked=forked,
        reused=reused,
        purpose=(purpose or "").strip(),
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# Submitting
# ----------------------------------------------------------------------


#: The branch names `create` makes, so `submit` can work out which plugin it is
#: being asked about without being told twice.
_BRANCH_PREFIX = "add-"

#: The repository's own checks, in the order its instructions run them.
#:
#: ``format --check`` rather than ``format``: this is a gate in front of a
#: push, and the checkout is the contributor's own. Reformatting it would
#: rewrite every Python file in it - including work of their own that has
#: nothing to do with the plugin - before the confirmation is asked and even
#: under ``--dry-run``, and `commit` stages only the plugin folder, so the
#: churn would be left behind uncommitted for them to find. All three checks
#: read; none of them writes. Formatting is the contributor's own `ruff
#: format .`, run when they mean it.
CHECKS = (
    ("ruff", ["format", "--check", "."]),
    ("ruff", ["check", "."]),
    ("pytest", ["-q"]),
)


@dataclass
class Submission:
    """One plugin, ready to be checked, committed and turned into a PR."""

    checkout: Path
    kind: str
    name: str
    folder: Path
    branch: str
    #: The interpreter that has the repository's dev tools.
    python: Path


def find_submission(directory: Path, name: str | None = None) -> Submission:
    """Work out which plugin in *directory* is the one being submitted.

    Derived from the branch when it can be, because `create` named the branch
    after the plugin, and asking again for something already on disk is how a
    two-command flow becomes one you have to remember arguments for.  An
    explicit *name* wins, for a checkout branched by hand.
    """
    directory = directory.expanduser()
    if not (directory / ".git").exists():
        raise PluginError(
            f"{directory} is not a checkout of {PLUGINS_REPO}. Pass --dir, or "
            "run `plugins create` first."
        )

    branch = _run(["git", "branch", "--show-current"], cwd=directory).strip()
    if not branch:
        raise PluginError(
            f"{directory} is not on a branch (detached HEAD), so there is "
            "nothing to push. Check out the branch your plugin is on."
        )
    if name is None:
        if not branch.startswith(_BRANCH_PREFIX):
            raise PluginError(
                f"{directory} is on {branch!r}, which does not name a plugin. "
                "Say which with `plugins submit <name>`."
            )
        name = branch[len(_BRANCH_PREFIX) :]

    matches = [
        (kind, directory / "plugins" / kind / name)
        for kind in (CAPTIONING, IMAGE)
        if (directory / "plugins" / kind / name).is_dir()
    ]
    if not matches:
        raise PluginError(
            f"{directory} has no plugin folder called {name!r} under "
            "plugins/captioning/ or plugins/image/."
        )
    if len(matches) > 1:
        raise PluginError(
            f"{directory} holds both a captioning and an image plugin called "
            f"{name!r}. The repository takes one plugin per pull request; "
            "submit them from separate branches."
        )
    kind, folder = matches[0]
    return Submission(directory, kind, name, folder, branch, _dev_python(directory))


#: Where a virtualenv keeps its executables, which is the one thing about one
#: that differs by platform.  Named once: the setup hint prints these paths for
#: someone to type, so a Unix-only spelling here is a command that fails on
#: Windows rather than a wrong path nobody sees.
VENV_BIN = Path(".venv") / ("Scripts" if os.name == "nt" else "bin")


def _dev_python(directory: Path) -> Path:
    """Return the interpreter holding the repository's dev tools.

    Its own virtualenv first: the repository pins its ruff, and a different
    version formats differently, so borrowing ours could turn its CI red over
    whitespace.  Falling back to ours is what makes the checks runnable at all
    on a machine that has not made one yet.
    """
    candidate = directory / VENV_BIN / ("python.exe" if os.name == "nt" else "python")
    return candidate if candidate.exists() else Path(sys.executable)


def missing_tools(submission: Submission) -> list[str]:
    """Return the check modules *submission*'s interpreter cannot import.

    Probed rather than inferred from an exit code: ``python -m nope`` exits 1
    exactly like a test failure does, and "your plugin is broken" is the wrong
    thing to tell someone whose only problem is an empty virtualenv.
    """
    return [
        module
        for module in dict.fromkeys(module for module, _arguments in CHECKS)
        if subprocess.run(
            [str(submission.python), "-c", f"import {module}"],
            capture_output=True,
            check=False,
        ).returncode
        != 0
    ]


def dev_setup_hint(submission: Submission, missing: list[str]) -> str:
    """Return the commands that give the checkout the tools it is missing."""
    pip = VENV_BIN / "pip"
    return (
        f"{submission.python} cannot import {', '.join(missing)}, so the "
        "checks cannot run. Give the checkout its own tools:\n"
        f"  cd {submission.checkout}\n"
        f"  python -m venv .venv && {pip} install -r requirements-dev.txt\n"
        f"  {pip} install --no-deps pixlstash"
    )


def run_checks(submission: Submission) -> list[str]:
    """Run the repository's own checks, returning the ones that failed."""
    failed: list[str] = []
    for module, arguments in CHECKS:
        printable = f"{module} {' '.join(arguments)}"
        # Flushed, and so is everything buffered behind it: the child writes
        # straight to the terminal, so without this its output arrives before
        # the line saying which command produced it.
        print(f"\n$ {printable}", flush=True)
        result = subprocess.run(
            [str(submission.python), "-m", module, *arguments],
            cwd=submission.checkout,
            check=False,
        )
        if result.returncode != 0:
            failed.append(printable)
    return failed


def commit(submission: Submission, message: str) -> None:
    """Stage the plugin folder and nothing else, then commit it.

    Staged by path rather than with ``git add -A``: the checkout is the
    contributor's, may hold work of their own, and the repository takes one
    plugin per pull request.
    """
    relative = submission.folder.relative_to(submission.checkout).as_posix()
    _run(["git", "add", "--", relative], cwd=submission.checkout)
    staged = _run(
        ["git", "diff", "--cached", "--name-only"], cwd=submission.checkout
    ).split()
    if not staged:
        raise PluginError(
            f"nothing to commit in {relative}: it is already committed, or "
            "unchanged since the last commit."
        )
    _run(["git", "commit", "-m", message], cwd=submission.checkout)


def push(submission: Submission) -> None:
    """Push the branch to ``origin``, setting it upstream."""
    _run(
        ["git", "push", "--set-upstream", "origin", submission.branch],
        cwd=submission.checkout,
    )


#: The pull request body.  Deliberately short, and deliberately not a summary
#: of the plugin: the plugin's own README is in the diff and says all of that.
#: What a reviewer cannot get from the diff is what the contributor actually
#: ran, which CI can never tell them for a plugin that loads a model.
PULL_REQUEST_BODY = """\
Adds `{name}`, {article} {kind} plugin.

See `{readme}` for what it does, its parameters and its licence.

## Tested against

{tested}
"""


def pull_request_body(submission: Submission, tested: str) -> str:
    """Return the pull request body, given what the contributor tested against.

    *tested* is theirs, verbatim.  Nothing here adds a path, a directory
    listing or anything else read off this machine: a pull request is public
    and permanent, and the only thing that belongs in one is what the person
    opening it chose to write.
    """
    readme = (submission.folder / "README.md").relative_to(submission.checkout)
    return PULL_REQUEST_BODY.format(
        name=submission.name,
        article="a" if submission.kind == CAPTIONING else "an",
        kind=submission.kind,
        readme=readme.as_posix(),
        tested=tested.strip(),
    )


#: An `origin` URL, in any of the spellings git accepts, down to its owner.
_ORIGIN_OWNER_RE = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$")


def compare_url(submission: Submission) -> str:
    """Return the web URL that opens *submission*'s pull request by hand.

    The branch is on whatever ``origin`` is, and for everyone but a maintainer
    that is a fork - where ``compare/<branch>`` names a branch the upstream
    repository does not have.  GitHub spells a cross-repository comparison
    ``main...<owner>:<branch>``, so the owner has to be read off the remote.
    An unreadable remote falls back to the same-repository form: a URL that
    might be wrong beats no way out of a failure this branch is already in.
    """
    head = submission.branch
    try:
        listed = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=submission.checkout,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        # No git on PATH, or a checkout that is no longer there. Neither is
        # worth raising over -- this URL is itself the handling of a failure --
        # but it does mean the URL may name the wrong repository, so say so
        # rather than let a puzzling empty compare page be the first anyone
        # hears of it.
        logger.warning(
            "Could not read `origin` in %s (%s), so the manual pull request "
            "URL assumes the branch is on %s itself.",
            submission.checkout,
            exc,
            PLUGINS_REPO,
        )
        return f"https://github.com/{PLUGINS_REPO}/compare/main...{head}"
    match = (
        _ORIGIN_OWNER_RE.search(listed.stdout.strip())
        if not listed.returncode
        else None
    )
    if match and f"{match[1]}/{match[2]}" != PLUGINS_REPO:
        head = f"{match[1]}:{submission.branch}"
    return f"https://github.com/{PLUGINS_REPO}/compare/main...{head}"


def open_pull_request(submission: Submission, title: str, body: str) -> str:
    """Open the pull request against the upstream repository, returning its URL."""
    if shutil.which("gh") is None:
        raise PluginError(
            "`gh` is not installed, so the pull request cannot be opened from "
            "here. The branch is pushed, so nothing is lost: open it at "
            f"{compare_url(submission)}"
        )
    return _run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            PLUGINS_REPO,
            "--base",
            "main",
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=submission.checkout,
    ).strip()
