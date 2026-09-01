"""Install, list and remove PixlStash plugins from the command line.

Backs the ``pixlstash-cli plugins`` verbs.  A plugin is a Python file (image
plugins, always) or a folder holding ``__init__.py`` (captioning plugins may be
either), and it has to land in one of two ``platformdirs`` directories that
differ by kind.  Copying it by hand is the failure this module exists to
remove: a folder in the wrong place is skipped without a message.

**Nothing here imports the plugin.**  The kind, the name and the shape are all
read out of the source with :mod:`ast`, because importing to classify means
running third-party code before the user has agreed to install it.  The price
is that some checks can only warn: a class whose base class comes from a module
we cannot see is analysed best-effort, and the abstract-method check is skipped
rather than guessed at.

**Authentication is filesystem access**, as it is for the library verbs:
whoever can write to the plugin directory can already install a plugin by hand.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

import requests
from platformdirs import user_data_dir

CAPTIONING = "captioning"
IMAGE = "image"

#: Where each kind is loaded from.  These two paths are duplicated from
#: ``tagger_plugins.registry.user_plugin_dir()`` and
#: ``image_plugins.registry.get_image_plugin_manager()`` rather than imported:
#: importing the image registry pulls in cv2 and Pillow, which is most of a
#: second on every CLI run.  ``test_plugin_install.py`` asserts the two agree.
_SUBDIRS = {
    CAPTIONING: ("tagger-plugins", "user"),
    IMAGE: ("image-plugins", "user"),
}

KIND_LABELS = {
    CAPTIONING: "Captioning plugins",
    IMAGE: "Image filters",
}

_BASE_MODULES = {
    "pixlstash.tagger_plugins.base": CAPTIONING,
    "pixlstash.image_plugins.base": IMAGE,
}
_BASE_CLASSES = {"TaggerPlugin": CAPTIONING, "ImagePlugin": IMAGE}

# A class missing one of these cannot be instantiated, so the registry skips it.
_REQUIRED_METHODS = {
    CAPTIONING: ("parameter_schema", "needs_download", "init", "unload", "is_loaded"),
    IMAGE: ("parameter_schema", "run"),
}

# ImagePluginManager skips this filename outright, so installing one produces a
# file that is ignored forever with no message anywhere.
_EXCLUDED_FILENAMES = {"plugin_template.py"}

_NAME_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
# A git ref, and nothing that could steer the download somewhere else.
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")

PLUGINS_REPO = "Pikselkroken/PixlStash-plugins"
DEFAULT_REF = "main"


class PluginError(Exception):
    """A refusal carrying a message written for the person at the terminal."""


@dataclass
class PluginClass:
    """One plugin class found in a source file, without importing it."""

    name: str
    display_name: str
    class_name: str
    kind: str
    file: Path
    warnings: list[str] = field(default_factory=list)
    # Declared on the base classes (issue #961). Read here as literals rather
    # than off an instance, because everything in this module has to work on a
    # plugin it has deliberately not imported.
    author: str = ""
    license: str = ""


@dataclass
class InstallPlan:
    """What ``plugins install`` is about to copy, and where."""

    kind: str
    name: str
    display_name: str
    source: Path
    destination: Path
    warnings: list[str] = field(default_factory=list)
    requirements: Path | None = None


# ----------------------------------------------------------------------
# Directories
# ----------------------------------------------------------------------


def user_dir(kind: str) -> Path:
    """Return the user plugin directory for *kind*.

    Not created here; ``install`` makes it when it first writes something.
    """
    return Path(user_data_dir("pixlstash"), *_SUBDIRS[kind])


def builtin_names(kind: str) -> set[str]:
    """Return the names the shipped plugins of *kind* already occupy.

    Read out of the package sources rather than listed here, so the two lists
    cannot drift apart from the registries that actually load them.
    """
    package = Path(__file__).parent
    if kind == IMAGE:
        files = [
            path
            for path in sorted((package / "image_plugins" / "built-in").glob("*.py"))
            if path.name not in _EXCLUDED_FILENAMES
        ]
    else:
        from pixlstash.tagger_plugins.registry import _FIRST_PARTY_PLUGINS

        files = [
            package / "tagger_plugins" / f"{module.rsplit('.', 1)[1]}.py"
            for module, _class_name in _FIRST_PARTY_PLUGINS
        ]

    names: set[str] = set()
    for path in files:
        try:
            found = _analyse(read_source(path), path, strict=False)
        except (OSError, PluginError):
            # A shipped plugin we cannot read statically is not a reason to
            # refuse the install the user asked for; it only costs this one
            # name in the collision check.
            continue
        names.update(entry.name for entry in found if entry.kind == kind)
    return names


# ----------------------------------------------------------------------
# Static analysis
# ----------------------------------------------------------------------


def _dotted(node: ast.expr) -> str | None:
    """Return ``a.b.c`` for an attribute/name expression, else ``None``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _import_bindings(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(local name → kind, local name → base module)`` for the file."""
    class_alias: dict[str, str] = {}
    module_alias: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if node.module in _BASE_MODULES and alias.name in _BASE_CLASSES:
                    class_alias[alias.asname or alias.name] = _BASE_MODULES[node.module]
                elif full in _BASE_MODULES:
                    module_alias[alias.asname or alias.name] = full
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _BASE_MODULES:
                    module_alias[alias.asname or alias.name] = alias.name
    return class_alias, module_alias


def _base_kind(
    node: ast.expr, class_alias: dict[str, str], module_alias: dict[str, str]
) -> str | None:
    """Return the plugin kind *node* names as a base class, if any."""
    if isinstance(node, ast.Name):
        return class_alias.get(node.id)
    if isinstance(node, ast.Attribute) and node.attr in _BASE_CLASSES:
        owner = _dotted(node.value)
        if owner is None:
            return None
        module = module_alias.get(owner, owner)
        if _BASE_MODULES.get(module) == _BASE_CLASSES[node.attr]:
            return _BASE_MODULES[module]
    return None


def _class_kinds(
    classes: dict[str, ast.ClassDef],
    class_alias: dict[str, str],
    module_alias: dict[str, str],
) -> tuple[dict[str, set[str]], set[str]]:
    """Resolve each class to the plugin kind(s) it derives from.

    Also returns the classes with a base we could not resolve at all - an
    imported mixin, say - for which the method checks are unreliable.
    """
    kinds: dict[str, set[str]] = {name: set() for name in classes}
    opaque: set[str] = set()
    for name, node in classes.items():
        for base in node.bases:
            if (
                _base_kind(base, class_alias, module_alias) is None
                and _dotted(base) not in classes
            ):
                opaque.add(name)

    changed = True
    while changed:
        changed = False
        for name, node in classes.items():
            found = set(kinds[name])
            for base in node.bases:
                direct = _base_kind(base, class_alias, module_alias)
                if direct:
                    found.add(direct)
                else:
                    parent = _dotted(base)
                    if parent in classes:
                        found |= kinds[parent]
                        if parent in opaque:
                            opaque.add(name)
            if found != kinds[name]:
                kinds[name] = found
                changed = True
    return kinds, opaque


def _string_attribute(node: ast.ClassDef, attribute: str) -> tuple[bool, str | None]:
    """Return ``(assigned, literal)`` for a class-level string attribute."""
    assigned = False
    for statement in node.body:
        target = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
        if not isinstance(target, ast.Name) or target.id != attribute:
            continue
        assigned = True
        value = statement.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return True, value.value
    return assigned, None


def _truthy_flag(node: ast.ClassDef, attribute: str) -> bool:
    """Return ``True`` if the class body assigns *attribute* a truthy literal."""
    for statement in node.body:
        target = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
        if isinstance(target, ast.Name) and target.id == attribute:
            value = statement.value
            if isinstance(value, ast.Constant) and value.value:
                return True
    return False


def _methods(
    name: str, classes: dict[str, ast.ClassDef], seen: set[str] | None = None
) -> set[str]:
    """Return the methods *name* defines, including local ancestors."""
    seen = seen or set()
    if name in seen or name not in classes:
        return set()
    seen.add(name)
    node = classes[name]
    found = {
        statement.name
        for statement in node.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for base in node.bases:
        parent = _dotted(base)
        if parent in classes:
            found |= _methods(parent, classes, seen)
    return found


def _analyse(source: str, path: Path, *, strict: bool) -> list[PluginClass]:
    """Return the plugin classes *source* defines, refusing what cannot load."""
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise PluginError(f"{path.name} is not valid Python: {exc}") from exc

    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    class_alias, module_alias = _import_bindings(tree)
    kinds, opaque = _class_kinds(classes, class_alias, module_alias)

    found: list[PluginClass] = []
    for class_name, node in classes.items():
        kind_set = kinds[class_name]
        if not kind_set:
            continue
        if len(kind_set) > 1:
            raise PluginError(
                f"{class_name} in {path.name} derives from both TaggerPlugin and "
                "ImagePlugin. A plugin is one kind or the other."
            )
        kind = kind_set.pop()
        assigned, name = _string_attribute(node, "name")
        if not assigned:
            # An abstract intermediate, most likely; it contributes nothing to
            # the registry on its own.
            continue
        if name is None:
            raise PluginError(
                f"{class_name} in {path.name} computes its `name` instead of "
                "declaring a string literal. The name is the plugin's identity "
                "and the installed filename, so it has to be readable here."
            )
        if not _NAME_RE.match(name):
            raise PluginError(
                f"{class_name} in {path.name} declares name={name!r}, which is "
                "not snake_case (lower-case letters, digits and underscores, "
                "starting with a letter)."
            )
        _display_assigned, display_name = _string_attribute(node, "display_name")
        warnings: list[str] = []
        if class_name not in opaque:
            missing = [
                method
                for method in _REQUIRED_METHODS[kind]
                if method not in _methods(class_name, classes)
            ]
            if missing:
                warnings.append(
                    f"{class_name} does not define {', '.join(missing)}; it "
                    "cannot be instantiated and the registry will skip it."
                )
        if kind == CAPTIONING and not (
            _truthy_flag(node, "supports_tags")
            or _truthy_flag(node, "supports_descriptions")
        ):
            warnings.append(
                f"{class_name} sets neither supports_tags nor "
                "supports_descriptions, so it appears in no table."
            )
        _, author = _string_attribute(node, "author")
        _, license_id = _string_attribute(node, "license")
        found.append(
            PluginClass(
                name=name,
                display_name=display_name or name,
                class_name=class_name,
                kind=kind,
                file=path,
                warnings=warnings,
                author=author or "",
                license=license_id or "",
            )
        )

    image_classes = [entry for entry in found if entry.kind == IMAGE]
    if len(image_classes) > 1:
        # _find_plugin_class returns the first *concrete* ImagePlugin subclass
        # the module itself defines (#968), so an abstract base or an imported
        # class no longer wins - but between two concrete ones it is still a
        # dict-ordering accident. Nothing downstream reports this.
        image_classes[0].warnings.append(
            f"{path.name} defines {len(image_classes)} ImagePlugin subclasses "
            f"({', '.join(entry.class_name for entry in image_classes)}); only "
            "the first one the registry happens to see is loaded."
        )

    if strict:
        for entry in found:
            if entry.warnings:
                raise PluginError(entry.warnings[0] + "  (refused by --strict)")
    return found


def read_source(path: Path) -> str:
    """Read a source file, turning every way that can fail into a refusal.

    ``UnicodeDecodeError`` is a ``ValueError``, not an ``OSError``, so a single
    non-UTF-8 file in a plugin directory would otherwise escape every caller's
    handler and take `plugins list` down with it - including the entries that
    are perfectly fine, in the one command whose job is to say what is wrong.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise PluginError(f"could not read {path}: {exc}") from exc


def inspect_file(path: Path, *, strict: bool = False) -> list[PluginClass]:
    """Analyse one ``.py`` file, refusing names the registries throw away."""
    if path.name in _EXCLUDED_FILENAMES:
        raise PluginError(
            f"{path.name} is the starter template, and the image plugin "
            "registry skips that filename. Rename it before installing."
        )
    return _analyse(read_source(path), path, strict=strict)


# ----------------------------------------------------------------------
# Sources: a folder, a zip, a single module, or a name from the repository
# ----------------------------------------------------------------------


def _python_files(root: Path) -> list[Path]:
    """Return the plugin-relevant ``.py`` files under *root*, in a stable order."""
    files = [
        path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    ]
    # __init__.py first: for a package it is where the plugin is declared, and
    # it decides the name when a package bundles more than one engine.
    return sorted(files, key=lambda path: (path.name != "__init__.py", path))


def _extract_zip(archive: Path, target: Path) -> Path:
    """Extract *archive* into *target*, refusing entries that escape it."""
    root = target.resolve()
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                destination = (root / member.filename).resolve()
                if destination != root and not destination.is_relative_to(root):
                    raise PluginError(
                        f"{archive.name} contains {member.filename!r}, which "
                        "would be written outside the plugin directory. "
                        "Refusing the whole archive."
                    )
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise PluginError(
                        f"{archive.name} contains a symlink ({member.filename!r}). "
                        "Refusing the whole archive."
                    )
            bundle.extractall(root)
    except zipfile.BadZipFile as exc:
        raise PluginError(f"{archive.name} is not a readable zip: {exc}") from exc

    # A zip made from a folder (or downloaded from GitHub) has one directory at
    # the top; unwrap it so the plugin folder itself is what gets installed.
    entries = [entry for entry in root.iterdir() if entry.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return root


def _download_repository(ref: str, workdir: Path) -> Path:
    """Download the plugins repository at *ref* and return the extracted root."""
    # The ref goes into a URL path, and requests collapses dot segments before
    # sending: an unchecked `--ref ../../../someone-else/evil/zip/main` walks
    # straight out of PLUGINS_REPO and installs code that this CLI then runs
    # unsandboxed in the server process. The repository source is deliberately
    # narrow - a named plugin from one repository - and this is what keeps it
    # narrow.
    if not _REF_RE.match(ref) or ".." in ref.split("/"):
        raise PluginError(
            f"{ref!r} is not a branch, tag or commit. A ref may contain "
            "letters, digits, dot, dash, underscore and slash, and cannot "
            "contain '..'."
        )

    url = f"https://codeload.github.com/{PLUGINS_REPO}/zip/{ref}"
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise PluginError(
            f"could not download {PLUGINS_REPO} at ref {ref!r}: {exc}"
        ) from exc

    archive = workdir / "repository.zip"
    archive.write_bytes(response.content)
    return _extract_zip(archive, workdir / "repository")


def _published_dirs(root: Path) -> list[Path]:
    """Return every published plugin folder in an extracted repository.

    The repository lays them out as ``plugins/<kind>/<slug>``, so the parent
    directory names the kind and the directory itself names the slug. One
    definition of that layout, shared by installing a named plugin and by
    listing the catalogue, so the two cannot come to disagree about what the
    repository contains.
    """
    return sorted(path for path in (root / "plugins").glob("*/*") if path.is_dir())


def _declared_name(folder: Path) -> str | None:
    """Return the name a published plugin folder declares, or None if unreadable.

    The same ast-only read the catalogue and the installer do - nothing here
    imports code that has just come off the network. A folder that will not
    parse, or declares no plugin, is not an error at this point: it is one entry
    the reader cannot be offered, and the others still have to work.
    """
    try:
        for source in _python_files(folder):
            found = _analyse(read_source(source), source, strict=False)
            if found:
                return found[0].name
    except (PluginError, OSError):
        return None
    return None


def _fetch_from_repository(name: str, ref: str, workdir: Path) -> Path:
    """Download the plugin called *name* and return its folder.

    Matched on the plugin's **declared** name first - that is what ``plugins
    available`` prints, what it lands under once installed, what ``plugins
    list`` shows and what ``plugins remove`` takes - and on the repository's
    directory name second, because that is a real name too and was the only one
    accepted before. Accepting only the directory name meant the catalogue
    advertised `moondream2` and installing it failed.

    Both passes look at the **whole** set before choosing, so a declared name
    always beats a directory name rather than merely beating the ones that
    happen to sort after it, and two folders answering to the same name are
    refused rather than resolved alphabetically - the shape `resolve_removal`
    already uses for an ambiguous name. Silently picking one would mean the
    plugin a reader installs is decided by a string inside downloaded code that
    nothing in the listing shows them.
    """
    candidates = _published_dirs(_download_repository(ref, workdir))
    declared = {folder: _declared_name(folder) for folder in candidates}

    matched = [folder for folder in candidates if declared[folder] == name] or [
        folder for folder in candidates if folder.name == name
    ]
    if len(matched) > 1:
        where = ", ".join(f"{folder.parent.name}/{folder.name}" for folder in matched)
        raise PluginError(
            f"{PLUGINS_REPO} at ref {ref!r} publishes {len(matched)} plugins "
            f"called {name!r} ({where}), so the name does not say which one you "
            "mean. That is a fault in the repository; until it is fixed, "
            "install from a local copy of the folder you want."
        )
    if matched:
        return matched[0]

    available = (
        ", ".join(sorted(declared[folder] or folder.name for folder in candidates))
        or "(none)"
    )
    raise PluginError(
        f"{PLUGINS_REPO} at ref {ref!r} has no plugin called {name!r}. "
        f"Available: {available}"
    )


@contextmanager
def materialise(source: str, ref: str = DEFAULT_REF) -> Iterator[Path]:
    """Yield a local file or folder for *source*, unpacking whatever it is."""
    candidate = Path(source).expanduser()
    with TemporaryDirectory(prefix="pixlstash-plugin-") as scratch:
        workdir = Path(scratch)
        if candidate.is_file() and candidate.suffix.lower() == ".zip":
            yield _extract_zip(candidate, workdir / "unpacked")
        elif candidate.exists():
            yield candidate
        elif os.sep not in source and "/" not in source and _NAME_RE.match(source):
            yield _fetch_from_repository(source, ref, workdir)
        else:
            raise PluginError(
                f"{source} is not a file or folder on this machine, and is not "
                "a plugin name from the plugins repository."
            )


# ----------------------------------------------------------------------
# Planning and installing
# ----------------------------------------------------------------------


def plan_install(root: Path, *, strict: bool = False) -> InstallPlan:
    """Work out what *root* is, and where a copy of it belongs."""
    if root.is_file():
        if root.suffix.lower() != ".py":
            raise PluginError(
                f"{root.name} is not a Python file. A plugin source is a .py "
                "file, a folder, or a zip of a folder."
            )
        found = inspect_file(root, strict=strict)
        requirements = None
    elif root.is_dir():
        found = []
        for path in _python_files(root):
            if path.name in _EXCLUDED_FILENAMES:
                continue
            found.extend(_analyse(read_source(path), path, strict=strict))
        requirements = root / "requirements.txt"
        requirements = requirements if requirements.is_file() else None
    else:
        raise PluginError(f"{root} does not exist.")

    if not found:
        raise PluginError(
            f"{root.name} defines no TaggerPlugin or ImagePlugin subclass with "
            "a name, so it is not a plugin."
        )
    kinds = {entry.kind for entry in found}
    if len(kinds) > 1:
        raise PluginError(
            f"{root.name} holds both a captioning plugin and an image plugin. "
            "They install to different directories, so install them separately."
        )

    kind = kinds.pop()
    primary = found[0]
    warnings = [warning for entry in found for warning in entry.warnings]
    if kind == CAPTIONING and len(found) > 1:
        warnings.append(
            f"{root.name} declares {len(found)} captioning plugins "
            f"({', '.join(entry.name for entry in found)}); it is installed "
            f"under {primary.name}, and all of them are registered."
        )

    destination_dir = user_dir(kind)
    if kind == CAPTIONING and root.is_dir() and (root / "__init__.py").is_file():
        source = root
        destination = destination_dir / primary.name
    else:
        # Everything else installs as the single module that declares it -
        # always, for image plugins, which the registry only ever reads as one
        # file.
        source = primary.file
        destination = destination_dir / f"{primary.name}.py"
        if root.is_dir():
            siblings = [
                path
                for path in _python_files(root)
                if path != source and path.name != "__init__.py"
            ]
            if siblings:
                # Only the one module travels, so a helper it imports is left
                # behind and the plugin fails at import - where, for image
                # plugins, nothing reports it. Say so here or nowhere.
                warnings.append(
                    f"only {source.name} is installed; "
                    f"{', '.join(path.name for path in siblings)} "
                    f"{'is' if len(siblings) == 1 else 'are'} left behind, and "
                    "the plugin will fail to import if it needs them."
                )

    if primary.name in builtin_names(kind):
        if kind == IMAGE:
            raise PluginError(
                f"an image filter named {primary.name!r} ships with PixlStash, "
                "and a user plugin replaces a built-in, with only a server log "
                "line to say so. Rename the plugin's `name` before installing it."
            )
        raise PluginError(
            f"a captioning plugin named {primary.name!r} ships with PixlStash, "
            "and built-ins win the collision, so yours would never load. "
            "Rename the plugin's `name` before installing it."
        )

    # Applied here rather than only in _analyse: the warnings raised above are
    # about the *plan* (which module travels, which name it lands under) and
    # exist after every per-file analysis has finished.
    if strict and warnings:
        raise PluginError(warnings[0] + "  (refused by --strict)")

    return InstallPlan(
        kind=kind,
        name=primary.name,
        display_name=primary.display_name,
        source=source,
        destination=destination,
        warnings=warnings,
        requirements=requirements,
    )


def install(plan: InstallPlan, *, force: bool = False) -> None:
    """Copy the planned source into place, refusing to clobber by accident."""
    # Both shapes are checked, not just the one being written: a plugin that
    # used to be a folder and is now a single module (or the reverse) would
    # otherwise leave the old shape behind, and the registry would load both.
    candidates = {
        plan.destination,
        plan.destination.with_suffix(".py"),
        plan.destination.with_suffix(""),
    }
    existing = sorted(path for path in candidates if path.exists())
    if existing and not force:
        raise PluginError(f"{existing[0]} already exists. Pass --force to replace it.")

    # Reinstalling a plugin over itself is a plausible gesture - you edited the
    # installed file, or you tab-completed the path out of the plugin directory
    # - and without this it deletes the only copy and then fails to find its
    # source. --force makes it worse, not better.
    source = plan.source.resolve()
    if any(
        source == path.resolve() or source.is_relative_to(path.resolve())
        for path in existing
    ):
        raise PluginError(
            f"{plan.source} is already the installed plugin. There is nothing "
            "to copy, and copying it over itself would destroy it."
        )

    plan.destination.parent.mkdir(parents=True, exist_ok=True)
    # Staged next to the destination and moved into place, so a copy that fails
    # part-way leaves the previous plugin intact instead of deleting it and
    # then not replacing it. The leading underscore keeps the staging entry out
    # of both registries' scans if the server starts mid-install.
    staged = plan.destination.with_name(f"_{plan.destination.name}.installing")
    if staged.exists():
        shutil.rmtree(staged) if staged.is_dir() else staged.unlink()
    try:
        if plan.source.is_dir():
            shutil.copytree(
                plan.source,
                staged,
                ignore=shutil.ignore_patterns("__pycache__", ".git", "*.pyc"),
            )
        else:
            shutil.copy2(plan.source, staged)

        for path in existing:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        staged.replace(plan.destination)
    except OSError as exc:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True) if staged.is_dir() else (
                staged.unlink(missing_ok=True)
            )
        raise PluginError(f"could not install to {plan.destination}: {exc}") from exc


@dataclass
class DependencyChange:
    """One package pip would install, and what is there now."""

    name: str
    version: str
    #: The version already installed, or None when the package is new here.
    installed: str | None = None

    @property
    def moves(self) -> bool:
        """Whether installing this would change a package already in use.

        The one distinction that matters. Adding a package cannot break what is
        running; replacing one can, and PixlStash pins every dependency it has,
        so the thing most likely to be replaced is PixlStash's own torch.
        """
        return self.installed is not None and self.installed != self.version


def resolve_requirements(requirements: Path) -> list[DependencyChange]:
    """Return every package installing *requirements* would add or replace.

    Resolved by pip rather than by us: ``--dry-run --report`` runs the real
    resolver, writes what it would do as JSON, and touches nothing.  The report
    lists only what is not already satisfied, so an entry is by definition a
    change, and the entries include transitive dependencies -- which is the
    point, since a plugin asking for one package can pull in forty.

    Nothing here decides whether the answer is acceptable; it says what would
    happen, and the caller shows it to the person who has to agree to it.
    """
    with TemporaryDirectory(prefix="pixlstash-deps-") as scratch:
        report = Path(scratch) / "report.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--quiet",
                "--report",
                str(report),
                "-r",
                str(requirements),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise PluginError(
                f"pip could not work out what {requirements.name} needs "
                f"(exit {result.returncode})"
                + (f": {detail[-1]}" if detail else "")
                + ". Nothing was installed."
            )
        try:
            entries = json.loads(report.read_text(encoding="utf-8"))["install"]
        except (OSError, ValueError, KeyError) as exc:
            raise PluginError(
                f"pip did not report what it would install: {exc}. "
                "Nothing was installed."
            ) from exc

    changes = []
    for entry in entries:
        name = entry["metadata"]["name"]
        try:
            installed = metadata_version(name)
        except PackageNotFoundError:
            installed = None
        changes.append(DependencyChange(name, entry["metadata"]["version"], installed))
    return sorted(changes, key=lambda change: change.name.lower())


def read_requirements(requirements: Path) -> list[str]:
    """Return the requirement lines, so the CLI can show them before running pip."""
    return [
        line.strip()
        for line in read_source(requirements).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def install_requirements(changes: list[DependencyChange]) -> None:
    """Install exactly the resolution *changes* describes, and nothing else.

    *changes* is what :func:`resolve_requirements` worked out and what the
    caller showed the user before asking. Installing it, rather than running
    ``pip install -r`` again, is what makes that agreement binding: a second
    run is a second resolution, free to pick different versions, so the torch
    replacement someone was told would not happen could happen anyway.

    ``--no-deps`` for the same reason. The resolved list already holds every
    transitive dependency pip found, so letting it resolve again could only add
    something nobody was shown.
    """
    if not changes:
        return
    pins = [f"{change.name}=={change.version}" for change in changes]
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", *pins],
        check=False,
    )
    if result.returncode != 0:
        raise PluginError(
            f"pip failed installing {', '.join(pins)} (exit "
            f"{result.returncode}). The plugin was installed; its "
            "dependencies were not."
        )


# ----------------------------------------------------------------------
# Listing and removing
# ----------------------------------------------------------------------


@dataclass
class InstalledPlugin:
    """One entry in a user plugin directory, as ``plugins list`` shows it."""

    kind: str
    entry: str
    name: str
    display_name: str
    problem: str | None = None
    shadows_builtin: bool = False


def list_installed() -> dict[str, list[InstalledPlugin]]:
    """Describe both user plugin directories without importing anything."""
    listing: dict[str, list[InstalledPlugin]] = {}
    for kind in (CAPTIONING, IMAGE):
        directory = user_dir(kind)
        entries: list[InstalledPlugin] = []
        builtins = builtin_names(kind)
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.name.startswith((".", "_")) or (
                    path.is_file() and path.suffix.lower() != ".py"
                ):
                    continue
                entries.append(_describe(kind, path, builtins))
        listing[kind] = entries
    return listing


def _describe(kind: str, path: Path, builtins: set[str]) -> InstalledPlugin:
    """Read one installed entry, recording why it will not load rather than raising."""
    try:
        found: list[PluginClass] = []
        if path.is_dir():
            if not (path / "__init__.py").is_file():
                return InstalledPlugin(
                    kind=kind,
                    entry=path.name,
                    name=path.name,
                    display_name="-",
                    problem="folder has no __init__.py, so it is skipped",
                )
            for source in _python_files(path):
                found.extend(_analyse(read_source(source), source, strict=False))
        else:
            found = inspect_file(path)
    except (PluginError, OSError) as exc:
        return InstalledPlugin(
            kind=kind,
            entry=path.name,
            name=path.stem,
            display_name="-",
            problem=str(exc),
        )

    if not found:
        return InstalledPlugin(
            kind=kind,
            entry=path.name,
            name=path.stem,
            display_name="-",
            problem="no plugin class found",
        )
    primary = found[0]
    problem = primary.warnings[0] if primary.warnings else None
    if primary.kind != kind:
        problem = (
            f"this is a {primary.kind} plugin in the {kind} directory; "
            "it will never load"
        )
    return InstalledPlugin(
        kind=kind,
        entry=path.name,
        name=primary.name,
        display_name=primary.display_name,
        problem=problem,
        shadows_builtin=primary.name in builtins,
    )


# ----------------------------------------------------------------------
# The published catalogue
# ----------------------------------------------------------------------


@dataclass
class CataloguePlugin:
    """One plugin published in the repository, as ``plugins available`` shows it."""

    kind: str
    name: str
    display_name: str
    summary: str = ""
    author: str = ""
    license: str = ""
    installed: bool = False
    problem: str | None = None


def _summary(folder: Path) -> str:
    """Return the one-line summary from a published plugin's README.

    The repository writes each README as a title, a blank line, then a paragraph
    saying what the plugin does. That paragraph is *hard-wrapped*, so the first
    line of it is a fragment ("...so it runs") rather than a sentence: the lines
    are joined back up before the first sentence is taken. A plugin without a
    README, or with a shape this does not fit, simply has no summary - guessing
    harder would put the wrong sentence in a listing people read to choose what
    to install.
    """
    try:
        text = (folder / "README.md").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""

    paragraph: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "<!--", "![", "[!")):
            continue
        if not stripped:
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    if not paragraph:
        return ""

    joined = " ".join(paragraph)
    sentence = re.split(r"(?<=[.!?])\s", joined, maxsplit=1)[0]
    # ponytail: a plain length clamp, not word-aware wrapping. A README whose
    # first sentence runs past this is already unusual; wrap properly if the
    # catalogue ever grows entries that genuinely need it.
    return sentence if len(sentence) <= 200 else sentence[:197] + "..."


def _catalogue_entry(folder: Path, installed: set[str]) -> CataloguePlugin:
    """Read one published plugin folder, recording problems rather than raising.

    One unparseable plugin in the repository must not empty the whole listing:
    the reader is choosing between the others, and a refusal that names none of
    them is useless. Nothing here imports the plugin - this is the same ast-only
    read the installer does, on code that has just come off the network.
    """
    kind = CAPTIONING if folder.parent.name == "captioning" else IMAGE
    try:
        found: list[PluginClass] = []
        for source in _python_files(folder):
            found.extend(_analyse(read_source(source), source, strict=False))
    except (PluginError, OSError) as exc:
        return CataloguePlugin(
            kind=kind,
            name=folder.name,
            display_name="-",
            installed=folder.name in installed,
            problem=str(exc),
        )
    if not found:
        return CataloguePlugin(
            kind=kind,
            name=folder.name,
            display_name="-",
            installed=folder.name in installed,
            problem="no plugin class found",
        )
    primary = found[0]
    return CataloguePlugin(
        kind=primary.kind,
        name=primary.name,
        display_name=primary.display_name,
        summary=_summary(folder),
        author=primary.author,
        license=primary.license,
        installed=primary.name in installed,
    )


def catalogue(ref: str = DEFAULT_REF) -> list[CataloguePlugin]:
    """Return every plugin published in the repository at *ref*.

    One download of the same archive ``plugins install <name>`` uses, so
    listing needs no API token, no second host and no rate-limited endpoint -
    and shows exactly the set that installing can reach.
    """
    installed = {
        entry.name for entries in list_installed().values() for entry in entries
    }
    with TemporaryDirectory(prefix="pixlstash-catalogue-") as scratch:
        root = _download_repository(ref, Path(scratch))
        return [_catalogue_entry(folder, installed) for folder in _published_dirs(root)]


def matches(entry: CataloguePlugin, query: str) -> bool:
    """Return whether *entry* matches a case-insensitive *query*.

    Searched across everything the listing shows - name, display name, summary,
    author and licence - because a reader who can see a word in the output will
    expect to be able to search for it. An empty query matches everything.
    """
    needle = query.strip().lower()
    if not needle:
        return True
    haystack = " ".join(
        (entry.name, entry.display_name, entry.summary, entry.author, entry.license)
    )
    return needle in haystack.lower()


def resolve_removal(name: str, kind: str | None = None) -> tuple[str, Path]:
    """Return the ``(kind, path)`` ``plugins remove`` would delete for *name*.

    The name reaches the filesystem, so containment is load-bearing rather than
    tidiness. Two things enforce it, and both are needed: the name may not carry
    a path separator (so it cannot name anything but a direct child), and the
    entry may not be a symlink (so following it cannot reach outside either).
    The symlink is refused rather than followed on purpose - resolving it would
    stay inside the letter of "delete only plugin files" while deleting a file
    the user did not name.
    """
    if not name or "/" in name or os.sep in name or name in (".", ".."):
        raise PluginError(f"{name!r} is not a plugin name.")

    matches: list[tuple[str, Path]] = []
    for candidate_kind in (CAPTIONING, IMAGE) if kind is None else (kind,):
        try:
            directory = Path(os.path.realpath(user_dir(candidate_kind)))
        except (OSError, ValueError) as exc:
            raise PluginError(f"{name!r} is not a plugin name: {exc}") from exc
        for candidate in (directory / name, directory / f"{name}.py"):
            if candidate.parent != directory:
                # Unreachable while the name has no separator; kept because the
                # cost of being wrong here is a deleted file somewhere else.
                raise PluginError(f"{name!r} would leave {directory}. Refused.")
            if candidate.is_symlink():
                raise PluginError(
                    f"{candidate} is a symlink. Refusing to delete what it "
                    "points at; remove the link yourself if that is what you want."
                )
            if candidate.exists():
                matches.append((candidate_kind, candidate))

    if not matches:
        where = "either plugin directory" if kind is None else f"the {kind} directory"
        raise PluginError(f"no plugin called {name!r} is installed in {where}.")
    if len({found_kind for found_kind, _ in matches}) > 1:
        raise PluginError(
            f"{name!r} names both a captioning plugin and an image filter. "
            f"Say which with --kind {CAPTIONING} or --kind {IMAGE}."
        )
    return matches[0]


def remove(path: Path) -> None:
    """Delete an installed plugin file or folder."""
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
