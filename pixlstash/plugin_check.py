"""Load one captioning plugin the way the server does and check its contract.

Backs ``pixlstash-cli plugins test``.  Discovery runs once, at start-up, so
without this the loop for finding a typo in a plugin is: edit, restart the
server, wait for the boot, read the error row under Settings › Auto-tagging.

**This is a development aid and not a security scanner.**  It says nothing
about whether a plugin is safe to install, because finding out whether it loads
means *running it*: the module body - and, with ``--image``, the model - execute
in this process, with the caller's permissions, exactly as they would in the
server's.  Nothing is sandboxed, so the only safe input is a plugin the caller
would have installed anyway.  Anything
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

**What the plugin reached for** is reported too, by a :class:`Recorder`: a
``sys.addaudithook`` observer that is switched on only while the plugin's own
code runs and collects the few audit events worth telling someone who is about
to install it - connections, programs started, native libraries, files written
or removed.  That is *disclosure, not containment*: the hook never blocks
anything, and everything it records lives in the plugin's own interpreter,
where a plugin written to hide from it can reach it too.  So its wording says what was seen
while this command ran, never what the plugin does or is.  Once installed the
hook stays for the life of the process, which is why this module must only ever
be imported by the CLI (``test_plugin_check_is_imported_only_by_the_cli``).
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pixlstash import plugin_install
from pixlstash.pixl_logging import get_logger
from pixlstash.plugin_install import PluginError
from pixlstash.tagger_plugins.base import TaggerPlugin
from pixlstash.tagger_plugins.registry import TaggerPluginManager
from pixlstash.utils.accelerator import resolve_device

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

#: Audit events worth telling someone who is about to install a plugin.
#: Everything else (imports, every read, compile, exec) is noise at this moment.
WATCHED_EVENTS = frozenset(
    {
        "socket.getaddrinfo",  # the host name; connect only sees the address
        "socket.gethostbyname",
        "socket.connect",  # also raised by connect_ex
        "socket.sendto",  # UDP needs no connect
        "socket.sendmsg",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.fork",
        "os.startfile",  # Windows
        "os.startfile/2",  # Windows, with arguments
        "ctypes.dlopen",
        "open",  # kept only when the mode or flags write
        "os.truncate",  # empties a file without opening it
        "os.symlink",
        "os.link",
        "os.remove",  # also raised by os.unlink
        "os.rename",  # also raised by os.replace
        "os.rmdir",
        "shutil.rmtree",
    }
)

#: The file events, and which argument names the file they write or remove.
_PATH_ARGUMENT = {
    "open": 0,
    "os.truncate": 0,
    "os.symlink": 1,
    "os.link": 1,
    "os.remove": 0,
    "os.rename": 0,
    "os.rmdir": 0,
    "shutil.rmtree": 0,
}
_WRITES = frozenset({"open", "os.truncate", "os.symlink", "os.link"})

#: ``os.open`` reports flags rather than a mode string, so these mark a write.
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

#: Lines printed per group before the rest are counted rather than listed.
_MAX_LINES = 8

#: Said under every report, empty or not: the report is what was seen, and a
#: reader who takes it for what the plugin can do has been misled by it.
BLIND_SPOTS = (
    "it does not see anything the plugin did outside the calls into it (a "
    "thread it started, say), compiled extension modules or other native code, "
    "anything that only runs later in the server, or anything it does not "
    "watch for, and a plugin written to hide from this can."
)

_active: Recorder | None = None
_hook_installed = False


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
    #: What the plugin's code was seen reaching for while it ran.
    reached: Recorder

    @property
    def ok(self) -> bool:
        """True when the plugin loaded, registered something and is clean."""
        return (
            bool(self.checked)
            and not self.failures
            and not any(check.problems for check in self.checked)
        )


class Recorder:
    """Collects the watched audit events raised while plugin code runs.

    Used as ``with recorder.watch(phase):`` around each call into the plugin,
    and never around the checker's own work, so what it holds is what the
    plugin reached for rather than what checking it cost. It can be entered
    any number of times, one after the other.

    Inside the window bytecode writing is switched off, so the import system
    writes no ``.pyc`` for the plugin or a dependency it imports first. A write
    under ``__pycache__`` is then the plugin's own, which is better than
    filtering them out and handing a plugin a directory to hide writes in.
    """

    def __init__(self, plugin_dir: Path | None = None) -> None:
        #: ``(event, args)`` in the order seen, args trimmed to what is printed.
        self.events: list[tuple[str, tuple[Any, ...]]] = []
        #: What the plugin was doing, while it was; ``None`` between windows.
        self.phase: str | None = None
        #: Writes under here are grouped as the plugin's own folder.
        self.plugin_dir = plugin_dir
        self._saved: tuple[Recorder | None, bool] | None = None

    def watch(self, phase: str) -> Recorder:
        """Name the phase the next window covers, for the Ctrl-C message."""
        self.phase = phase
        return self

    def __enter__(self) -> Recorder:
        global _active
        _install_hook()
        self._saved = (_active, sys.dont_write_bytecode)
        sys.dont_write_bytecode = True
        _active = self
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        global _active
        _active, sys.dont_write_bytecode = self._saved
        # Kept when an exception is on its way through, which is the
        # KeyboardInterrupt the CLI names the phase from.
        if exc_type is None:
            self.phase = None

    def summary(self) -> list[str]:
        """Return the report, as lines, blind spots included.

        Worded as what was *seen while this command ran* in every case,
        including the empty one: "nothing seen" printed as "no network access"
        would be a safety verdict this cannot give.
        """
        network: Counter[str] = Counter()
        processes: Counter[str] = Counter()
        libraries: dict[str, set[str]] = {}
        written: dict[str, set[str]] = {}
        removed: dict[str, set[str]] = {}
        for event, args in self.events:
            if event in ("socket.getaddrinfo", "socket.gethostbyname"):
                network[f"looked up {_text(args[0])}"] += 1
            elif event == "socket.connect":
                network[f"connected to {_address(args[0])}"] += 1
            elif event in ("socket.sendto", "socket.sendmsg"):
                network[f"sent to {_address(args[0])}"] += 1
            elif event == "os.fork":
                processes["forked this process"] += 1
            elif event == "os.system":
                processes[f"ran the shell command {_clip(_text(args[0]))}"] += 1
            elif event in ("os.startfile", "os.startfile/2"):
                processes[f"opened {_text(args[0])} with its default program"] += 1
            elif event in (
                "subprocess.Popen",
                "os.exec",
                "os.posix_spawn",
                "os.spawn",
            ):
                processes[f"started {_command(*args)}"] += 1
            elif event == "ctypes.dlopen":
                name = "this program's own symbols" if args[0] is None else args[0]
                name = _text(name)
                libraries.setdefault(os.path.dirname(name) or name, set()).add(name)
            else:  # a file event, trimmed to the one path by the hook
                self._bucket(written if event in _WRITES else removed, args[0])

        lines = _counted(network) + _counted(processes)
        lines += _grouped(libraries, "loaded {n} native {what} from {where}")
        lines += _grouped(written, "wrote {n} {what} under {where}")
        lines += _grouped(removed, "removed or moved {n} {what} under {where}")
        if not lines:
            return [
                "While this command ran, none of what it watches for was seen "
                "(lookups and connections, programs started, libraries loaded "
                "through ctypes, files written or removed).",
                f"That is not a clean bill: {BLIND_SPOTS}",
            ]
        return [
            "While this command ran, the plugin's code:",
            *(f"  {line}" for line in lines),
            f"This is what was seen, not what the plugin can do: {BLIND_SPOTS}",
        ]

    def _bucket(self, buckets: dict[str, set[str]], path: Any) -> None:
        """File *path* under the root a reader would recognise it by.

        Grouped rather than listed: a model download writes hundreds of files
        into one cache, which is one fact and not hundreds.
        """
        if isinstance(path, int):
            buckets.setdefault("an open file descriptor", set()).add(str(path))
            return
        try:
            target = Path(os.path.abspath(_text(path)))  # made absolute by the hook
        except (OSError, ValueError):
            # The working directory is gone, which is when the hook kept this
            # path relative too. Reported as it was given rather than raised.
            target = Path(_text(path))
        cache = Path.home() / ".cache"
        for root in (self.plugin_dir, Path(tempfile.gettempdir())):
            if root is not None and target.is_relative_to(root):
                break
        else:
            if target.is_relative_to(cache) and target != cache:
                root = cache / target.relative_to(cache).parts[0]
            else:
                root = target.parent
        buckets.setdefault(_shown(root), set()).add(str(target))


def _install_hook() -> None:
    """Install the audit hook, once, the first time a window opens.

    Never at import: importing this module changes nothing about a process,
    running a check does. There is no way to remove the hook afterwards, which
    is fine for a short-lived CLI and is why the server must never import this.
    """
    global _hook_installed
    if not _hook_installed:
        sys.addaudithook(_audit)
        _hook_installed = True


def _audit(event: str, args: tuple[Any, ...]) -> None:
    """Record *event* on the active recorder, if it is one worth reporting.

    Runs on every audit event in the process for the rest of its life, so it
    returns at once when there is nothing to do. It opens nothing and resolves
    no symlinks, which would raise events of its own; making a path absolute
    only reads the working directory, which raises none.
    """
    recorder = _active
    if recorder is None or event not in WATCHED_EVENTS:
        return
    if event == "open":
        _path, mode, flags = args
        # `mode` is a string from open(), None from os.open(); reads are every
        # .pyc of every import and never worth a line.
        if (
            not (mode and any(c in mode for c in "wax+"))
            and not (flags or 0) & _WRITE_FLAGS
        ):
            return
    if event in _PATH_ARGUMENT:
        args = (_absolute(args[_PATH_ARGUMENT[event]]),)
    elif event in ("socket.connect", "socket.sendto", "socket.sendmsg"):
        args = (args[1],)  # the address; the socket object is not kept
    recorder.events.append((event, args))


def _absolute(path: Any) -> Any:
    """Return *path* made absolute against the working directory right now.

    Now rather than when the summary is printed: a plugin that changes
    directory, writes and changes back would otherwise be reported as writing
    wherever the checker happens to be. A path relative to a ``dir_fd`` is
    still placed against the working directory, which is wrong, and rare.
    """
    if not isinstance(path, (str, bytes, os.PathLike)):
        return path  # a file descriptor, bucketed as one
    try:
        return os.path.abspath(path)
    except (OSError, ValueError):
        # A deleted working directory. Kept relative rather than raised: an
        # exception here would fail the plugin's own call, and this hook must
        # never change what the plugin does. It is still reported, relative.
        return path


def _text(value: Any) -> str:
    """Return *value* as text, decoding a bytes path the way the OS would."""
    return os.fsdecode(value) if isinstance(value, bytes) else str(value)


def _clip(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _address(address: Any) -> str:
    """Return a connect() address as ``host:port``, or a socket's path."""
    if isinstance(address, tuple) and len(address) >= 2:
        host = _text(address[0])
        return f"[{host}]:{address[1]}" if ":" in host else f"{host}:{address[1]}"
    return _text(address)


def _command(*args: Any) -> str:
    """Return the program and a clipped argument list from a spawn event.

    ``os.spawn`` puts a mode first; every other spawn event starts with the
    executable, which ``subprocess`` leaves ``None`` on Windows, where the
    argument list (or the whole command line, as one string) says it instead.
    """
    if args and isinstance(args[0], int):
        args = args[1:]
    executable, argv = args[0], args[1]
    if isinstance(argv, (str, bytes)):
        return _clip(_text(executable or argv))
    argv = [_text(a) for a in argv]
    program = _text(executable) if executable is not None else argv[0]
    return _clip(" ".join([program, *argv[1:]]))


def _shown(path: Path) -> str:
    """Return *path* with the home directory written as ``~``."""
    home = Path.home()
    return (
        f"~{os.sep}{path.relative_to(home)}" if path.is_relative_to(home) else str(path)
    )


def _counted(lines: Counter[str]) -> list[str]:
    """Return each distinct line once, with how often it was seen."""
    shown = [
        line if count == 1 else f"{line}  ({count} times)"
        for line, count in lines.items()
    ]
    return _capped(shown, "more")


def _grouped(buckets: dict[str, set[str]], template: str) -> list[str]:
    """Return one line per bucket, largest first."""
    shown = []
    for where, items in sorted(buckets.items(), key=lambda item: -len(item[1])):
        n = len(items)
        if "native" in template:
            what = "library" if n == 1 else "libraries"
        else:
            what = "file" if n == 1 else "files"
        shown.append(template.format(n=n, what=what, where=where))
    return _capped(shown, "more directories")


def _capped(lines: list[str], more: str) -> list[str]:
    if len(lines) <= _MAX_LINES:
        return lines
    return [*lines[:_MAX_LINES], f"+{len(lines) - _MAX_LINES} {more}"]


def check_plugin(
    path: str, image: str | None = None, recorder: Recorder | None = None
) -> CheckReport:
    """Load the plugin at *path* as the server does and check its contract.

    Args:
        path: A ``*.py`` file, or a folder holding ``__init__.py``.
        image: Optional image to caption or tag with the schema's defaults.
        recorder: Where to record what the plugin reaches for. The CLI passes
            its own so that it can still print it after a Ctrl-C; otherwise a
            fresh one is made.

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
    if recorder is None:
        recorder = Recorder()
    # abspath, not resolve: the events carry the paths as the plugin spelled
    # them, and a symlinked temp directory would otherwise never match.
    recorder.plugin_dir = Path(
        os.path.abspath(target if target.is_dir() else target.parent)
    )

    # No user_dir and no first-party plugins: this loads the one thing it was
    # pointed at, and never the installed plugins beside it or a torch-heavy
    # built-in the user did not ask about. `reload()` on that empty manager
    # loads nothing and marks the registry loaded, so the `list_*` calls below
    # cannot re-enter discovery and wipe what was just loaded.
    manager = TaggerPluginManager(user_dir=None, first_party=[])
    manager.reload()
    with recorder.watch("load"):
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
        # A plugin may override this, so it is the plugin's code as well.
        with recorder.watch("plugin_schema()"):
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
            check.output, run_problems = _run_over_image(plugin, image, recorder)
            check.problems.extend(run_problems)
        checked.append(check)
    if not checked:
        failures.extend(_wrong_kind_hint(target))
    return CheckReport(
        path=target, checked=checked, failures=failures, reached=recorder
    )


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


def _run_over_image(
    plugin: TaggerPlugin, image: str, recorder: Recorder
) -> tuple[Any | None, list[str]]:
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
    with recorder.watch("--image run"):
        parameters = plugin.default_params()
        try:
            if plugin.needs_download(parameters):
                return None, [
                    "needs_download() is True: the plugin says its model files "
                    "are not on this machine. Stopping rather than fetching "
                    "them - download it from Settings › Auto-tagging, then run "
                    "this again."
                ]
        except Exception as exc:
            return None, [f"needs_download() raised {type(exc).__name__}: {exc}"]

    # Outside the window: picking a device imports torch, which loads its own
    # native libraries, and that is the checker's doing rather than the
    # plugin's.
    device = _device() if hasattr(plugin, "setup") else None

    with recorder.watch("--image run"):
        try:
            # Both workflows do this pair, in this order, before every batch.
            if hasattr(plugin, "setup"):
                plugin.setup(device)
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

    Whatever accelerator this host has - the same answer
    :meth:`InferenceEngine.create` reaches, so the checker cannot tell a plugin
    author "cpu" while the server would have handed their plugin a GPU.

    Falls back to ``"cpu"`` if torch cannot be reached at all, which is not a
    guess about the machine so much as a way of getting out of torch's way: a
    plugin that needs a device needs torch too, and its own ``init()`` is
    moments away and will say so in terms of its own dependency. Failing here
    instead would replace that message with a traceback out of the checker,
    about a library the plugin author may not even import directly.
    """
    # resolve_device imports torch lazily, for the same reason this function
    # used to: torch is seconds of start-up and every other verb in this CLI -
    # including `plugins test` without --image - runs without it. It also owns
    # the "torch cannot be reached" case, logging the cause rather than
    # swallowing it, so the fallback described above still holds.
    return resolve_device()
