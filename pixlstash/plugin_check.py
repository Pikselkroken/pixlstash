"""Load one captioning plugin the way the server does and check its contract.

Backs ``pixlstash-cli plugins test``.  Discovery runs once, at start-up, so
without this the loop for finding a typo in a plugin is: edit, restart the
server, wait for the boot, read the error row under Settings › Auto-tagging.

**This is a development aid and not a security scanner.**  It says nothing
about whether a plugin is safe to install, because finding out whether it loads
means *running it*: the module body - and, with ``--image``, the model - execute
in this process, with the caller's permissions, exactly as they would in the
server's.  Nothing is sandboxed and nothing inspects what the code does, so the
only safe input is a plugin the caller would have installed anyway.  Anything
printed here is a statement about the plugin's *contract*, never about its
intent, and no wording in this module or its CLI verb may blur the two: a
report read as a safety verdict is worse than no report.

That is the exact opposite of :mod:`pixlstash.plugin_install`, which classifies
a source with ``ast`` and never imports it, because *it* runs before the user
has agreed to install anything - and pays for that with an inability to see any
import-time failure.  Here the user has named a plugin and asked for it to be
run, so importing it is the request rather than a side effect of classifying it.

The load itself is :meth:`TaggerPluginManager.load_plugin_from_path` - the
server's own loader, not a second implementation that resembles it, so the
module namespacing, the package ``submodule_search_locations`` and the
containment of a failing import are the same by construction.  The decisions
*around* the loader are not shared and have to be restated here, which is what
:func:`_ineligible` and :func:`_installed_names` are: discovery filters the
directory listing before it reaches that loader, and refuses a name another
plugin already holds after it, and a plugin that sails past either of those
would load perfectly here and never load in the server.

What this adds on top is the **schema shape**, which the host does not check:
a parameter the settings screen cannot render is the most likely mistake in a
first plugin, and every way of getting it wrong is silent.  An unknown ``type``
falls through to the component's ``v-else`` and becomes a text box; so does a
``select`` with no choices at all; and a ``select`` whose ``options`` list is
*empty* renders a real dropdown with nothing in it.

Passing here is not the same as working in PixlStash.  A plugin that hangs at
import hangs the server's boot, and it would hang this command too; nothing
here says the captions are any good, and nothing here says the plugin is safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixlstash import plugin_install
from pixlstash.pixl_logging import get_logger
from pixlstash.plugin_install import PluginError
from pixlstash.tagger_plugins.base import TaggerPlugin
from pixlstash.tagger_plugins.registry import TaggerPluginManager

logger = get_logger(__name__)

#: The parameter types ``TaggerParametersUI.vue`` has an explicit branch for,
#: plus ``string``, which is that component's ``v-else``.  A ``type`` outside
#: this set therefore costs a control rather than raising anywhere: it is
#: silently edited as free text.  ``bool`` is an undocumented alias the
#: component accepts alongside ``boolean``; the list mirrors what renders, not
#: what the guide recommends writing, and
#: ``test_schema_types_match_the_component_that_renders_them`` pins the two
#: together so this cannot drift.
SCHEMA_TYPES = (
    "number",
    "integer",
    "boolean",
    "bool",
    "select",
    "string",
    "textarea",
    "csv-int",
)

#: ``name`` and ``default`` are read unguarded by ``TaggerPlugin.default_params``
#: and ``TaggerPluginManager.fill_defaults``, which run on every library open,
#: so omitting either is a crash waiting for the next library switch.
REQUIRED_FIELD_KEYS = ("name", "default")

#: Documented as required, but the UI falls back to the parameter's ``name``
#: (``{{ field.label || field.name }}``), so a plugin without one works and
#: merely looks unfinished.  Reported, never failed: this command exists to be
#: trusted instead of a restart, and a false refusal costs more than a shabby
#: label.
RECOMMENDED_FIELD_KEYS = ("label", "type")


@dataclass
class PluginCheck:
    """One registered plugin class and what checking it turned up."""

    #: The name it registered under, which is the instance attribute the
    #: registry keys on rather than anything in the schema below.
    name: str
    schema: dict[str, Any]
    #: Things that stop this plugin working. These fail the command.
    problems: list[str] = field(default_factory=list)
    #: Things worth saying that do not stop it working. These do not.
    warnings: list[str] = field(default_factory=list)
    #: What ``--image`` produced, or ``None`` when it was not asked for. Typed
    #: loosely because a plugin returning the wrong shape is a finding here,
    #: not something to hide.
    output: Any | None = None


@dataclass
class CheckReport:
    """The verdict on one plugin file or folder."""

    path: Path
    checked: list[PluginCheck]
    #: Load errors, worded as the server's Auto-tagging screen words them.
    failures: list[str]

    @property
    def ok(self) -> bool:
        """True when the plugin loaded, registered something and is clean."""
        return (
            bool(self.checked)
            and not self.failures
            and not any(check.problems for check in self.checked)
        )


def check_plugin(path: str, image: str | None = None) -> CheckReport:
    """Load the plugin at *path* as the server does and check its contract.

    Args:
        path: A ``*.py`` file, or a folder holding ``__init__.py``.
        image: Optional image to caption or tag with the schema's defaults.

    Returns:
        A :class:`CheckReport`.

    Raises:
        PluginError: If *path* is not a shape the server would ever load, or
            *image* is not a file. Both are the user's typo, not the plugin's.
    """
    target = Path(path).expanduser()
    if not target.exists():
        raise PluginError(f"{target} does not exist.")
    if target.is_dir():
        if not (target / "__init__.py").is_file():
            raise PluginError(
                f"{target} is a folder with no __init__.py. The server skips "
                "such a folder without a message; a folder plugin needs one."
            )
    elif target.suffix != ".py":
        raise PluginError(
            f"{target} is not a .py file. A plugin is a .py file or a folder "
            "holding __init__.py."
        )
    if image is not None and not Path(image).expanduser().is_file():
        raise PluginError(f"{image} is not a file.")

    failures = _ineligible(target)

    # No user_dir and no first-party plugins: this loads the one thing it was
    # pointed at, and never the installed plugins beside it or a torch-heavy
    # built-in the user did not ask about. `reload()` on that empty manager
    # loads nothing and marks the registry loaded, so the `list_*` calls below
    # cannot re-enter discovery and wipe what was just loaded.
    manager = TaggerPluginManager(user_dir=None, first_party=[])
    manager.reload()
    manager.load_plugin_from_path(str(target))

    failures += [
        f"{error['name']}: {error['message']}" for error in manager.list_errors()
    ]
    # Both read statically, with `ast`, so finding out which names are taken
    # imports no first-party plugin (and no torch) and runs no other plugin's
    # code. `_taken_names` is what the server's own duplicate check would see;
    # this manager cannot see it, because it deliberately scans no directory.
    reserved = plugin_install.builtin_names(plugin_install.CAPTIONING)
    taken = _installed_names(target)

    checked = []
    for plugin in manager.get_all_plugins():
        schema = plugin.plugin_schema()
        problems, warnings = _schema_findings(schema)
        check = PluginCheck(
            name=plugin.name, schema=schema, problems=problems, warnings=warnings
        )
        if check.name in reserved:
            check.problems.append(
                f"a first-party plugin is already called {check.name!r}. The "
                "built-in wins that collision, so this one never loads."
            )
        elif check.name in taken:
            check.problems.append(
                f"an installed plugin is already called {check.name!r} "
                f"({taken[check.name]}). The first one loaded wins and the "
                "other is skipped, so one of the two never runs."
            )
        if image is not None:
            check.output, run_problems = _run_over_image(plugin, image)
            check.problems.extend(run_problems)
        checked.append(check)
    if not checked:
        failures.extend(_wrong_kind_hint(target))
    return CheckReport(path=target, checked=checked, failures=failures)


def _ineligible(target: Path) -> list[str]:
    """Return why discovery would skip this entry outright, if it would.

    ``_load_user_plugins`` filters the directory listing *before* the loader
    this module shares with it, so a name the scan skips loads perfectly here
    and never loads in the server - silently, which is the failure the whole
    command exists to remove.
    """
    entry = target.name
    if not entry.startswith((".", "_")):
        return []
    return [
        f"{entry}: the server skips any entry whose name starts with "
        f"{entry[0]!r}, without a message. Rename it, or it will never load "
        "however well it works here."
    ]


def _installed_names(target: Path) -> dict[str, str]:
    """Return ``{plugin name: where}`` for the already-installed captioners.

    Excludes *target* itself, so checking a plugin that is already installed
    does not report it as colliding with its own copy.
    """
    resolved = target.resolve()
    # `InstalledPlugin.entry` is the bare directory entry, so the comparison
    # has to be rebuilt against the directory it came from.
    directory = plugin_install.user_dir(plugin_install.CAPTIONING)
    installed = {}
    for entry in plugin_install.list_installed()[plugin_install.CAPTIONING]:
        if (directory / entry.entry).resolve() == resolved:
            continue
        installed[entry.name] = entry.entry
    return installed


def _wrong_kind_hint(target: Path) -> list[str]:
    """Say so when nothing registered because this is an image filter.

    "No TaggerPlugin subclass found" is true and useless for the person who
    pointed this at the other kind of plugin, which is an easy mistake to make
    when one CLI group installs both.
    """
    source = target / "__init__.py" if target.is_dir() else target
    try:
        found = plugin_install.inspect_file(source)
    except (OSError, PluginError):
        return []
    if not any(entry.kind == plugin_install.IMAGE for entry in found):
        return []
    return [
        "this is an image filter, not a captioning plugin. The two have "
        "different contracts - different base class, different parameter "
        "schema - and `plugins test` checks only captioning plugins."
    ]


def _schema_findings(schema: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return ``(problems, warnings)`` for one plugin's schema.

    A problem stops the plugin working - it crashes, or the control is not
    there. A warning is cosmetic, and failing the command on one would make
    this check less useful than the restart it replaces.
    """
    # A plugin may override plugin_schema() outright, and the settings screen
    # reads every one of these, so a missing key is the whole finding.
    missing = [
        key
        for key in (
            "name",
            "display_name",
            "parameters",
            "supports_tags",
            "supports_descriptions",
        )
        if key not in schema
    ]
    if missing:
        return [f"plugin_schema() returned nothing for {', '.join(missing)}"], []

    problems: list[str] = []
    warnings: list[str] = []
    if not (schema["supports_tags"] or schema["supports_descriptions"]):
        # A warning rather than a problem, and the same call `plugins install`
        # makes: it loads and registers exactly as written, it is simply never
        # reached - which may be a half-finished plugin rather than a broken one.
        warnings.append(
            "neither supports_tags nor supports_descriptions is set, so it "
            "registers and nothing ever calls it"
        )

    parameters = schema["parameters"]
    if not isinstance(parameters, list):
        problems.append(
            f"parameter_schema() returned {type(parameters).__name__}, not a list"
        )
        return problems, warnings

    for index, definition in enumerate(parameters):
        if not isinstance(definition, dict):
            problems.append(
                f"parameter {index} is a {type(definition).__name__}, not a dict"
            )
            continue
        where = f"parameter {definition.get('name', index)!r}"
        for key in REQUIRED_FIELD_KEYS:
            if key not in definition:
                problems.append(
                    f"{where} has no {key!r}, which is read unguarded every "
                    "time a library is opened"
                )
        for key in RECOMMENDED_FIELD_KEYS:
            if key not in definition:
                warnings.append(f"{where} has no {key!r}")
        kind = definition.get("type")
        if "type" in definition and kind not in SCHEMA_TYPES:
            problems.append(
                f"{where} has type {kind!r}, which is none of "
                f"{', '.join(SCHEMA_TYPES)}; it renders as a plain text box"
            )
        if kind == "select":
            problems.extend(_select_problems(where, definition))
    return problems, warnings


def _select_problems(where: str, definition: dict[str, Any]) -> list[str]:
    """Return why this select has nothing to offer, if it has nothing.

    The two failures look identical to the plugin author and land in different
    places in the component, so they are worth separate wording: the key being
    absent falls out of the ``select`` branch's guard into the ``v-else`` and
    becomes a text field, while an empty list satisfies ``Array.isArray`` and
    renders a real dropdown with nothing in it.
    """
    for key in ("options", "enum"):
        value = definition.get(key)
        if isinstance(value, list) and value:
            return []
        if isinstance(value, list):
            return [
                f"{where} is a select whose {key!r} is empty; it renders as a "
                "dropdown with nothing to choose"
            ]
    return [
        f"{where} is a select with no 'options' or 'enum' list, so it is not "
        "rendered as one: it falls through to a plain text box"
    ]


def _run_over_image(plugin: TaggerPlugin, image: str) -> tuple[Any | None, list[str]]:
    """Init the plugin and run it over one image, as the workflows do.

    This asks ``needs_download()`` first and stops if the answer is yes, so
    that a check command does not start a multi-gigabyte fetch nobody asked
    for. **That is a courtesy, not a guarantee**, and the wording everywhere
    else has to match: ``needs_download()`` is the plugin's own answer about
    its own files, and the download a plugin does in ``init()`` - which is
    where this repository's own ``from_pretrained_local_first`` does it, and so
    where a plugin author copying the shipped captioners will do it - happens
    below this line regardless.

    Returns:
        ``(what came back, problems)``. The result is ``None`` when the plugin
        never got as far as returning anything.
    """
    # Decided before anything is loaded: a plugin with neither capability flag
    # has no method for this to call, and the workflows would never reach it
    # either, so downloading its model and initialising it is work done for a
    # call that is not going to happen. `_schema_findings` has already warned
    # about the flags themselves.
    if plugin.supports_descriptions:
        call = "generate_descriptions"
    elif plugin.supports_tags:
        call = "tag_images"
    else:
        return None, []

    image_path = str(Path(image).expanduser().resolve())
    parameters = plugin.default_params()

    try:
        if plugin.needs_download(parameters):
            return None, [
                "needs_download() is True: the plugin says its model files are "
                "not on this machine. Stopping rather than fetching them - "
                "download it from Settings › Auto-tagging, then run this again."
            ]
    except Exception as exc:
        return None, [f"needs_download() raised {type(exc).__name__}: {exc}"]

    try:
        # Both workflows do this pair, in this order, before every batch.
        if hasattr(plugin, "setup"):
            plugin.setup(_device())
        plugin.init(parameters)
    except Exception as exc:
        return None, [f"init() raised {type(exc).__name__}: {exc}"]

    try:
        result = getattr(plugin, call)([image_path], parameters=parameters)
    except Exception as exc:
        return None, [f"{call}() raised {type(exc).__name__}: {exc}"]

    if not isinstance(result, dict):
        return result, [
            f"{call}() returned {type(result).__name__}, not a dict keyed by "
            "the paths it was given"
        ]
    if image_path not in result:
        return result, [
            f"{call}() returned the key(s) {sorted(map(str, result))}, not the "
            f"path it was given ({image_path}). The caller looks results up by "
            "path and would drop these."
        ]
    return result, []


def _device() -> str:
    """Return the device the server would hand a plugin's ``setup()``.

    Falls back to ``"cpu"`` if torch cannot be reached at all, which is not a
    guess about the machine so much as a way of getting out of torch's way: a
    plugin that needs a device needs torch too, and its own ``init()`` is
    moments away and will say so in terms of its own dependency. Failing here
    instead would replace that message with a traceback out of the checker,
    about a library the plugin author may not even import directly.
    """
    # Local import: torch is seconds of start-up, and every other verb in this
    # CLI - including `plugins test` without --image - runs without it.
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception as exc:
        # Deliberately not just ImportError. A torch that is installed but
        # cannot load its shared libraries raises OSError here, and a partial
        # install can raise almost anything; all of them mean the same thing to
        # this function. Logged with the exception rather than swallowed, so
        # the cause survives for whoever reads the log.
        logger.warning(
            "Could not ask torch which device to use (%s: %s); telling the "
            "plugin 'cpu'. If it needs a GPU, this is why it did not get one.",
            type(exc).__name__,
            exc,
        )
        return "cpu"
