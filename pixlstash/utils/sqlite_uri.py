"""Build SQLite connection strings from filesystem paths, percent-encoded.

A path pasted raw into a connection string is cut short by any character the
string's grammar reserves. In a SQLite ``file:`` URI ``?`` starts the query and
``#`` the fragment; SQLAlchemy's URL parser also splits on ``?``. A library
folder named ``odd #1?`` then opened a truncated path, created a stray file
beside the folder, and left the real vault unmigrated (#2210).

Every SQLite open that goes through a URI or a SQLAlchemy URL builds it here.
``tests/test_architecture_guardrails.py`` fails the build on a hand-built
``f"file:{...}"`` or ``f"sqlite:///{...}"`` anywhere else under ``pixlstash/``.
"""

import os
from urllib.parse import quote


def sqlite_file_uri(path, *, read_only: bool = False) -> str:
    """Return a SQLite ``file:`` URI naming *path*, for ``connect(uri=True)``.

    Everything but ASCII letters, digits, ``_.-~`` and the separators ``/``,
    ``\\`` and ``:`` is percent-encoded, and SQLite decodes it again. Keeping
    the separators means a Windows drive path keeps the shape SQLite already accepted
    (``file:C:\\...``) and a UNC path does not grow a URI authority, which
    SQLite refuses. A POSIX absolute path gets an explicit empty authority so
    a path starting with ``//`` cannot be read as a host.

    Args:
        path: Filesystem path to the database, absolute or relative to the
            current directory.
        read_only: Append ``mode=ro``.

    Returns:
        The URI string.
    """
    quoted = quote(os.path.abspath(os.fspath(path)), safe="/\\:")
    if quoted.startswith("/"):
        quoted = "//" + quoted
    uri = f"file:{quoted}"
    return f"{uri}?mode=ro" if read_only else uri


def sqlite_url(path) -> str:
    """Return a SQLAlchemy URL for the SQLite database at *path*.

    Carries :func:`sqlite_file_uri` with ``uri=true``, so the path reaches
    SQLite intact. The engine is otherwise what ``sqlite:///<path>`` gave: a
    file database, so ``QueuePool`` and ``check_same_thread=False``.

    Alembic reads its URL through ``ConfigParser`` interpolation, so a value
    passed to ``Config.set_main_option`` must have its ``%`` doubled.

    Args:
        path: Filesystem path to the database.

    Returns:
        The URL string.
    """
    return f"sqlite:///{sqlite_file_uri(path)}?uri=true"
