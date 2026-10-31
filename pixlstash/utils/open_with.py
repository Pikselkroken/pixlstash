"""The applications the operating system offers for a file, and launching one.

``Open with ▸`` on a picture lists what the OS itself registers for the file's
type, never a list PixlStash keeps, and launches the chosen app on an edit copy
(``pixlstash/services/edit_copies.py``). Per OS:

- **Linux:** ``gio mime <type>``, the union of its *Recommended* and
  *Registered* blocks with the default marked, names and categories read from
  each app's ``.desktop`` file; launched with ``gio launch``. No "Other app…":
  the only OS chooser is the XDG portal, which needs an fd passed over D-Bus.
- **macOS:** a JXA script over ``NSWorkspace``; launched with ``open -a``.
  "Other app…" is the OS's own ``choose application``.
- **Windows:** a PowerShell ``Add-Type`` helper over ``SHAssocEnumHandlers``,
  launched through the handler's own ``IAssocHandler.Invoke`` (Store apps
  included). "Other app…" is the shell's ``OpenAs_RunDLL`` dialog.

**The client never names a program.** An app id is opaque: a desktop id, a
bundle path or a handler name, and :func:`find_app` only ever returns one the OS
listed for this file type. Nothing a request sends is executed, and paths and
ids reach the helpers as argv or environment variables, never inside a command
string.

**Editors first.** Each app carries ``editor``: false only when the app says it
is a viewer (``Categories=Viewer``, ``CFBundleTypeRole=Viewer``) or a web
browser. The rule is negative because no OS says reliably "this app can save a
PNG", and a mistake should leave a viewer among the editors rather than hide an
editor among the viewers. Windows handlers carry no role, so there only
browsers are told apart.

Every caller is on the ``LOOPBACK_OWNER_ONLY`` red line
(``docs/backend_architecture.md`` §16.3.1): this drives the server process's
own desktop session.
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# The PowerShell compile and the JXA bridge cost real time, and what is
# installed rarely changes while the app runs.
# ponytail: per-extension TTL cache, no invalidation on app install; an app
# installed mid-session shows up within the TTL.
_CACHE_TTL_S = 10 * 60
_LIST_TIMEOUT_S = 20

_cache: dict[tuple[str, str], tuple[float, list["OpenWithApp"]]] = {}
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class OpenWithApp:
    """One application the OS offers for a file type.

    Attributes:
        id: Opaque to the client: a desktop id, a bundle path or a handler name.
        name: What the OS calls it.
        default: The OS's default for the type.
        editor: False when the app declares itself a viewer or a browser.
        launch: Platform detail for the launch (the ``.desktop`` file on Linux);
            never sent to the client.
    """

    id: str
    name: str
    default: bool = False
    editor: bool = True
    launch: str = ""

    def public(self) -> dict:
        """The fields the client sees."""
        data = asdict(self)
        data.pop("launch")
        return data


def has_other_chooser() -> bool:
    """Whether this OS has a chooser for an app the list does not name."""
    return sys.platform == "darwin" or sys.platform.startswith("win")


def list_apps(file_path: str) -> list[OpenWithApp]:
    """The apps the OS offers for *file_path*'s type, editors first.

    Cached per extension. An empty list is the answer on a headless host or
    when the OS tool fails; the failure is logged where it happens.
    """
    ext = os.path.splitext(file_path)[1].lower()
    key = (sys.platform, ext)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL_S:
            return hit[1]
    if sys.platform == "darwin":
        apps = _list_macos(file_path)
    elif sys.platform.startswith("win"):
        apps = _list_windows(ext)
    else:
        apps = _list_linux(file_path)
    apps = editors_first(apps)
    # Not an empty answer: a tool that failed once (and logged it) is asked
    # again on the next hover rather than hiding every app for the TTL.
    if apps:
        with _cache_lock:
            _cache[key] = (now, apps)
    return apps


def find_app(file_path: str, app_id: str) -> OpenWithApp | None:
    """The listed app whose id is *app_id*, or None: the only way to an app."""
    for app in list_apps(file_path):
        if app.id == app_id:
            return app
    return None


def editors_first(apps: list[OpenWithApp]) -> list[OpenWithApp]:
    """Editors, then the rest, each in the OS's order; one row per name."""
    seen: set[str] = set()
    unique = []
    # The default wins a name it shares with another registration.
    for app in sorted(apps, key=lambda a: not a.default):
        if app.name.casefold() in seen:
            continue
        seen.add(app.name.casefold())
        unique.append(app)
    order = {app.id: idx for idx, app in enumerate(apps)}
    unique.sort(key=lambda a: (not a.editor, order[a.id]))
    return unique


def launch(app: OpenWithApp, file_path: str) -> None:
    """Start *app* on *file_path* and return at once.

    Fire and forget: a single-instance editor hands the file to its running
    copy and the spawned process exits, so its exit says nothing about the
    edit. The child gets its own session, so closing PixlStash never takes the
    editor with it.

    Raises:
        OSError: The launcher could not be started.
    """
    if sys.platform == "darwin":
        _spawn(["open", "-a", app.id, file_path])
    elif sys.platform.startswith("win"):
        _spawn(
            _powershell_argv(),
            env={
                "PIXL_HELPER_SOURCE": _WINDOWS_HELPER_SOURCE,
                "PIXL_MODE": "open",
                "PIXL_FILE": file_path,
                "PIXL_HANDLER": app.id,
            },
        )
    else:
        _spawn(["gio", "launch", app.launch, file_path])


def launch_other(file_path: str) -> None:
    """Open the OS's own app chooser on *file_path* (macOS, Windows).

    Raises:
        OSError: The chooser could not be started.
        RuntimeError: This OS has no chooser to call.
    """
    if sys.platform == "darwin":
        _spawn(["osascript", "-e", _MACOS_CHOOSE_SCRIPT, file_path])
    elif sys.platform.startswith("win"):
        _spawn(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", file_path])
    else:
        raise RuntimeError("This OS has no app chooser PixlStash can call")


def _spawn(argv: list[str], env: dict[str, str] | None = None) -> None:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if env:
        kwargs["env"] = {**os.environ, **env}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x8) | (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        )
    else:
        kwargs["start_new_session"] = True
    logger.info("Open with: starting %s", argv[:3])
    subprocess.Popen(argv, **kwargs)  # noqa: S603 - argv from a listed app


def _run_tool(argv: list[str], env: dict[str, str] | None = None) -> str | None:
    """Run a listing tool; its stdout, or None after logging why not."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv
            argv,
            capture_output=True,
            text=True,
            timeout=_LIST_TIMEOUT_S,
            env={**os.environ, **(env or {})},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Open with: could not run %s: %s", argv[0], exc)
        return None
    if completed.returncode != 0:
        logger.warning(
            "Open with: %s exited %s: %s",
            argv[:2],
            completed.returncode,
            (completed.stderr or "").strip()[:500],
        )
        return None
    return completed.stdout


# ── Linux ──────────────────────────────────────────────────────────────────


def _list_linux(file_path: str) -> list[OpenWithApp]:
    mime = mimetypes.guess_type(file_path)[0]
    if not mime:
        logger.info("Open with: no MIME type for %s", file_path)
        return []
    # C locale: the block headers are parsed.
    out = _run_tool(["gio", "mime", mime], env={"LC_ALL": "C"})
    if out is None:
        return []
    default, ids = parse_gio_mime(out)
    apps = []
    for desktop_id in ids:
        path = find_desktop_file(desktop_id)
        if path is None:
            logger.debug("Open with: no .desktop file for %s", desktop_id)
            continue
        entry = read_desktop_entry(path)
        if entry is None:
            continue
        name, categories = entry
        apps.append(
            OpenWithApp(
                id=desktop_id,
                name=name,
                default=desktop_id == default,
                editor=not ({"Viewer", "WebBrowser"} & categories),
                launch=path,
            )
        )
    return apps


def parse_gio_mime(output: str) -> tuple[str | None, list[str]]:
    """``(default id, [desktop ids])`` from ``LC_ALL=C gio mime <type>``.

    The ids are the union of the *Recommended* and *Registered* blocks,
    Recommended first: a per-format desktop file (Krita's ``krita_png``) can be
    listed only under Registered while Recommended is not empty. The default
    is included even when neither block names it.
    """
    default = None
    blocks: dict[str, list[str]] = {}
    current: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")):
            item = line.strip()
            if item.endswith(".desktop"):
                current.append(item)
        elif line.startswith("Default application") and ":" in line:
            candidate = line.rsplit(":", 1)[1].strip()
            if candidate.endswith(".desktop"):
                default = candidate
        else:
            current = blocks.setdefault(line.split()[0].lower(), [])
    ids = list(
        dict.fromkeys(
            blocks.get("recommended", [])
            + blocks.get("registered", [])
            + [default] * bool(default)
        )
    )
    return default, ids


def _xdg_data_dirs() -> list[str]:
    home = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [home, *(d for d in dirs.split(":") if d)]


def find_desktop_file(desktop_id: str) -> str | None:
    """The first ``<dir>/applications/<id>`` over the XDG data dirs."""
    if "/" in desktop_id or desktop_id.startswith("."):
        return None
    for data_dir in _xdg_data_dirs():
        path = os.path.join(data_dir, "applications", desktop_id)
        if os.path.isfile(path):
            return path
    return None


def read_desktop_entry(path: str) -> tuple[str, set[str]] | None:
    """``(Name, Categories)`` from a ``.desktop`` file; None when hidden.

    ``NoDisplay=true`` is kept: it means "not in the app menu", and Krita's
    per-format entries carry it while being exactly the handler wanted.
    ``Hidden=true`` means the entry was deleted, so it is dropped.
    """
    name = None
    categories: set[str] = set()
    in_entry = False
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if line.startswith("["):
                    in_entry = line == "[Desktop Entry]"
                    continue
                if not in_entry or "=" not in line:
                    continue
                key, value = (part.strip() for part in line.split("=", 1))
                if key == "Name" and name is None:
                    name = value
                elif key == "Categories":
                    categories = {c for c in value.split(";") if c}
                elif key == "Hidden" and value.lower() == "true":
                    return None
    except OSError as exc:
        logger.warning("Open with: could not read %s: %s", path, exc)
        return None
    return (name, categories) if name else None


# ── macOS ──────────────────────────────────────────────────────────────────

# The apps for the file, the default, display names, each app's role for the
# file's type from its Info.plist, and which apps open https (the browsers).
_MACOS_LIST_SCRIPT = r"""
ObjC.import('AppKit');
ObjC.import('UniformTypeIdentifiers');
function run(argv) {
  const ws = $.NSWorkspace.sharedWorkspace;
  const fm = $.NSFileManager.defaultManager;
  const path = argv[0];
  const url = $.NSURL.fileURLWithPath(path);
  const ext = path.split('.').pop().toLowerCase();
  const fileType = $.UTType.typeWithFilenameExtension(ext);
  const def = ws.URLForApplicationToOpenURL(url);
  const defPath = def.isNil() ? '' : def.path.js;
  const browsers = {};
  const b = ws.URLsForApplicationsToOpenURL($.NSURL.URLWithString('https://example.com'));
  for (let i = 0; i < b.count; i++) browsers[b.objectAtIndex(i).path.js] = true;
  const apps = ws.URLsForApplicationsToOpenURL(url);
  const out = [];
  for (let i = 0; i < apps.count; i++) {
    const p = apps.objectAtIndex(i).path.js;
    let role = '';
    try {
      const bundle = $.NSBundle.bundleWithPath(p);
      const types = ObjC.deepUnwrap(bundle.infoDictionary).CFBundleDocumentTypes || [];
      for (const t of types) {
        const exts = (t.CFBundleTypeExtensions || []).map(e => String(e).toLowerCase());
        const uts = t.LSItemContentTypes || [];
        const conforms = !fileType.isNil() && uts.some(u => {
          const ut = $.UTType.typeWithIdentifier(u);
          return !ut.isNil() && fileType.conformsToType(ut);
        });
        if (exts.includes(ext) || conforms) { role = t.CFBundleTypeRole || ''; break; }
      }
    } catch (e) { role = ''; }
    out.push({id: p, name: fm.displayNameAtPath(p).js.replace(/\.app$/, ''),
              default: p === defPath, role: role, browser: !!browsers[p]});
  }
  return JSON.stringify(out);
}
"""

# The OS's own app chooser, then open the file with what was chosen. Paths
# come in argv and are quoted by AppleScript's ``quoted form``.
_MACOS_CHOOSE_SCRIPT = r"""
on run argv
  set chosen to choose application as alias
  do shell script "open -a " & quoted form of POSIX path of chosen & " " & quoted form of (item 1 of argv)
end run
"""


def _list_macos(file_path: str) -> list[OpenWithApp]:
    out = _run_tool(
        ["osascript", "-l", "JavaScript", "-e", _MACOS_LIST_SCRIPT, file_path]
    )
    return [] if out is None else parse_macos_apps(out)


def parse_macos_apps(output: str) -> list[OpenWithApp]:
    """Apps from the JXA script's JSON; ``[]`` (logged) when unreadable."""
    try:
        rows = json.loads(output)
        return [
            OpenWithApp(
                id=str(row["id"]),
                name=str(row.get("name") or os.path.basename(row["id"])),
                default=bool(row.get("default")),
                editor=row.get("role") != "Viewer" and not row.get("browser"),
            )
            for row in rows
        ]
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("Open with: unreadable macOS app list %r: %s", output[:200], exc)
        return []


# ── Windows ────────────────────────────────────────────────────────────────

# ``list`` prints one tab-separated line per recommended handler:
# name, UI name, default (0/1), browser (0/1). ``open`` invokes the handler
# whose name is PIXL_HANDLER on PIXL_FILE through the handler's own
# IAssocHandler.Invoke, which also covers Store apps. Inputs arrive in
# environment variables; the script itself is this constant.
_WINDOWS_HELPER_SOURCE = r"""
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32;
public static class PixlOpenWith {
  [ComImport, Guid("F04061AC-1659-4a3f-A954-775AA57FC083"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAssocHandler {
    [PreserveSig] int GetName([MarshalAs(UnmanagedType.LPWStr)] out string name);
    [PreserveSig] int GetUIName([MarshalAs(UnmanagedType.LPWStr)] out string name);
    [PreserveSig] int GetIconLocation([MarshalAs(UnmanagedType.LPWStr)] out string path, out int index);
    [PreserveSig] int IsRecommended();
    [PreserveSig] int MakeDefault([MarshalAs(UnmanagedType.LPWStr)] string description);
    [PreserveSig] int Invoke([MarshalAs(UnmanagedType.Interface)] object dataObject);
  }
  [ComImport, Guid("973810ae-9599-4b88-9e4d-6ee98c9552da"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IEnumAssocHandlers {
    [PreserveSig] int Next(uint count, [Out, MarshalAs(UnmanagedType.LPArray, SizeParamIndex = 0)] IAssocHandler[] items, out uint fetched);
  }
  [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IShellItem {
    [PreserveSig] int BindToHandler(IntPtr bindCtx, ref Guid bhid, ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out object ppv);
  }
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHAssocEnumHandlers(string extra, int filter, out IEnumAssocHandlers handlers);
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHCreateItemFromParsingName(string path, IntPtr bindCtx, ref Guid riid, out IShellItem item);
  [DllImport("shlwapi.dll", CharSet = CharSet.Unicode)]
  static extern int AssocQueryString(int flags, int str, string assoc, string extra, StringBuilder outBuf, ref uint outLen);
  const int ASSOC_FILTER_RECOMMENDED = 1;
  const int ASSOCSTR_FRIENDLYAPPNAME = 4;

  static List<IAssocHandler> Handlers(string ext) {
    var list = new List<IAssocHandler>();
    IEnumAssocHandlers e;
    SHAssocEnumHandlers(ext, ASSOC_FILTER_RECOMMENDED, out e);
    var one = new IAssocHandler[1];
    uint fetched;
    while (e.Next(1, one, out fetched) == 0 && fetched == 1) list.Add(one[0]);
    return list;
  }

  static HashSet<string> BrowserExes() {
    var exes = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    foreach (var hive in new[] { Registry.LocalMachine, Registry.CurrentUser }) {
      using (var root = hive.OpenSubKey(@"SOFTWARE\Clients\StartMenuInternet")) {
        if (root == null) continue;
        foreach (var sub in root.GetSubKeyNames()) {
          using (var cmd = root.OpenSubKey(sub + @"\shell\open\command")) {
            var v = cmd == null ? null : cmd.GetValue("") as string;
            if (string.IsNullOrEmpty(v)) continue;
            exes.Add(v.Trim().Trim('"').Split('"')[0].Trim());
          }
        }
      }
    }
    return exes;
  }

  public static string List(string ext) {
    var sb = new StringBuilder(512);
    uint len = 512;
    string def = AssocQueryString(0, ASSOCSTR_FRIENDLYAPPNAME, ext, null, sb, ref len) == 0 ? sb.ToString() : "";
    var browsers = BrowserExes();
    var lines = new StringBuilder();
    foreach (var h in Handlers(ext)) {
      string name, ui;
      if (h.GetName(out name) != 0 || h.GetUIName(out ui) != 0) continue;
      lines.Append(name.Replace("\t", " ")).Append('\t').Append(ui.Replace("\t", " ")).Append('\t')
           .Append(ui == def ? "1" : "0").Append('\t').Append(browsers.Contains(name) ? "1" : "0").Append('\n');
    }
    return lines.ToString();
  }

  public static int Open(string path, string handlerName) {
    var ext = System.IO.Path.GetExtension(path);
    foreach (var h in Handlers(ext)) {
      string name;
      if (h.GetName(out name) != 0 || name != handlerName) continue;
      var iidItem = new Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe");
      IShellItem item;
      SHCreateItemFromParsingName(path, IntPtr.Zero, ref iidItem, out item);
      var bhid = new Guid("B8C0BD9F-ED24-455c-83E6-D5390C4FE8C4");
      var iidData = new Guid("0000010e-0000-0000-C000-000000000046");
      object data;
      int hr = item.BindToHandler(IntPtr.Zero, ref bhid, ref iidData, out data);
      if (hr != 0) return hr;
      return h.Invoke(data);
    }
    return 2;
  }
}
"""

_WINDOWS_SCRIPT = (
    "$ErrorActionPreference = 'Stop'\n"
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
    "Add-Type -TypeDefinition $env:PIXL_HELPER_SOURCE\n"
    "if ($env:PIXL_MODE -eq 'open') {\n"
    "  exit [PixlOpenWith]::Open($env:PIXL_FILE, $env:PIXL_HANDLER)\n"
    "}\n"
    "[Console]::Out.Write([PixlOpenWith]::List($env:PIXL_EXT))\n"
)


def _powershell_argv() -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        _WINDOWS_SCRIPT,
    ]


def _list_windows(ext: str) -> list[OpenWithApp]:
    if not ext:
        return []
    out = _run_tool(
        _powershell_argv(),
        env={
            "PIXL_HELPER_SOURCE": _WINDOWS_HELPER_SOURCE,
            "PIXL_MODE": "list",
            "PIXL_EXT": ext,
        },
    )
    return [] if out is None else parse_windows_apps(out)


def parse_windows_apps(output: str) -> list[OpenWithApp]:
    """Apps from the helper's tab-separated lines; malformed lines are logged."""
    apps = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            logger.warning("Open with: unreadable Windows handler line %r", line)
            continue
        name, ui_name, default, browser = parts
        apps.append(
            OpenWithApp(
                id=name,
                name=ui_name or name,
                default=default == "1",
                editor=browser != "1",
            )
        )
    return apps
