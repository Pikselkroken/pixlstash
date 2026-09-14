"""The directories a PixlStash installation reads or writes as library content.

One list, several callers, because it used to be three partial ones and the
gaps were exactly the routes that move files:

* ``POST /pictures/export/folder`` knew every root (#1177 item 2, #1206 item 1).
* ``POST /import-folders`` knew only the active vault's ``image_root``, so a
  watch folder could be pointed at another library's folder or at one of this
  library's own reference folders - and one carrying ``delete_after_import``
  then copies every file into the active library and unlinks the original,
  leaving the owning library's rows pointing at files that are gone.
* The reference-folder create/update/relocate routes knew ``image_root`` and
  the other reference-folder rows, so a relocation would physically move
  pictures into another library's folder, or into a ``delete_after_import``
  watch folder that then eats them.

Issue #1206 item 1 asked for one shared list. This is it.

Two rules read it, because writing files and naming a folder to read are
different acts (#1223):

* The folder export may not write inside any of these roots.
* A watch or reference folder may overlap another watch or reference folder,
  but may not equal, sit inside or contain a library's folder; and
  a library may not be added inside, or around, a watch or reference folder
  of any registered library (``LibraryRegistry``, so the CLI too).
"""

import functools

from fastapi import HTTPException

from pixlstash.hub.registry import FOLDER_OVERLAP_LOCK
from pixlstash.pixl_logging import get_logger
from pixlstash.services.config_service import get_import_folder_paths
from pixlstash.utils.path_utils import LibraryRootsUnavailable, path_is_within

logger = get_logger(__name__)

#: Said to a folder export aimed inside anything the library reads.
PATH_INSIDE_LIBRARY_DETAIL = (
    "That folder is part of your library - PixlStash reads it - so anything "
    "put there would be imported straight back in, or moved out from under "
    "the library that owns it. Choose a folder outside this library, its "
    "reference folders and its import folders, and outside any other "
    "library's folder."
)

#: Said to a watch or reference folder that overlaps a library's own folder.
FOLDER_OVERLAPS_LIBRARY_DETAIL = (
    "That folder overlaps a library's folder. Watch folders and reference "
    "folders must sit outside every library and must not contain one. Choose "
    "a folder outside your libraries."
)

#: The lists below are a *blocklist*, so one that cannot be read must refuse.
#: Treating a failed read as an empty list turns the check off silently and
#: permits exactly what it exists to refuse (#1177 item 59).
ROOTS_UNAVAILABLE_DETAIL = (
    "PixlStash could not check your reference folders, watched folders and "
    "libraries just now, so it cannot confirm this folder is outside them. "
    "Try again in a moment."
)


def library_folders(server, vault) -> list[str]:
    """The active library's own folder and every registered library's folder.

    Args:
        server: The running server, for the hub's library registry.
        vault: The vault holding the active lease.

    Returns:
        The folders, not resolved. Compare with
        :func:`~pixlstash.utils.path_utils.path_is_within`.

    Raises:
        LibraryRootsUnavailable: The registry could not be read.
    """
    try:
        # Every OTHER registered library is the same hazard as this one: a
        # library's folder IS its image_root (it is where `vault.db` lives), so
        # files written into one come back as new pictures the moment that
        # library is opened, and only the active lease's vault is open here.
        #
        # `include_detached=True`: a detached library is not being read *today*,
        # but re-attaching it is one click and everything written into it then
        # imports as new pictures. One keyword, no extra read.
        libraries = server.library_registry.list_libraries(include_detached=True)
    except Exception as exc:
        logger.warning("Could not list the registered libraries: %s", exc)
        raise LibraryRootsUnavailable(
            f"Could not list the registered libraries: {exc}"
        ) from exc
    roots = [getattr(vault, "image_root", None)]
    roots.extend(library.path for library in libraries)
    return [root for root in roots if root]


def watch_and_reference_folders(vault) -> list[str]:
    """The active library's watch (import) folders and reference folders.

    Raises:
        LibraryRootsUnavailable: A folder list could not be read.
    """
    return [*vault.reference_folder_roots(), *get_import_folder_paths(vault)]


def library_content_roots(server, vault) -> list[str]:
    """Every directory this installation reads or writes as library content.

    The library folders plus this library's watch and reference folders. Only
    the active lease's vault is read: another library's watch and reference
    folders live in ITS vault, and opening someone else's vault to answer this
    question is a worse trade than the narrower check.

    Args:
        server: The running server, for the hub's library registry.
        vault: The vault whose reference and import folders to include.

    Returns:
        The root directories, in no particular order and not resolved.

    Raises:
        LibraryRootsUnavailable: A list could not be read, so the answer is
            "we do not know" rather than "there are none".
    """
    roots = library_folders(server, vault)
    try:
        roots.extend(watch_and_reference_folders(vault))
    except Exception as exc:
        # Including LibraryRootsUnavailable, deliberately: one failure shape for
        # the caller to map, and the original is chained for the log.
        logger.warning(
            "Could not list the roots this installation owns, so no path can "
            "be shown to be outside them: %s",
            exc,
        )
        raise LibraryRootsUnavailable(
            f"Could not list the roots this installation owns: {exc}"
        ) from exc
    return [root for root in roots if root]


def _roots_or_503(read, path: str) -> list[str]:
    """*read()*'s roots, or a 503: a blocklist that cannot be read must refuse."""
    try:
        return read()
    except LibraryRootsUnavailable as exc:
        logger.warning(
            "Refusing %s: the roots this installation owns could not be "
            "listed, so it cannot be shown to be outside them: %s",
            path,
            exc,
        )
        raise HTTPException(status_code=503, detail=ROOTS_UNAVAILABLE_DETAIL) from exc


def refuse_path_inside_a_library(
    path: str,
    server,
    vault,
    *,
    status_code: int = 409,
) -> None:
    """Refuse a destination for written files inside anything the library reads.

    For the folder export: files written into a library folder, a watch folder
    or a reference folder come straight back as pictures, or are eaten by a
    ``delete_after_import`` watcher.

    Args:
        path: An absolute path a caller wants files written to.
        server: The running server.
        vault: The vault holding the active lease.
        status_code: The refusal's status. The folder export has answered 400
            since #1177 and its callers read that.

    Raises:
        HTTPException: 503 when the roots could not be listed, *status_code*
            when *path* is inside one of them.
    """
    for root in _roots_or_503(lambda: library_content_roots(server, vault), path):
        if path_is_within(path, root):
            raise HTTPException(
                status_code=status_code, detail=PATH_INSIDE_LIBRARY_DETAIL
            )


def refuse_folder_overlapping_a_library(path: str, server, vault) -> None:
    """Refuse a watch or reference folder that overlaps a library's folder.

    Watch and reference folders may overlap each other; neither may equal, sit
    inside or contain a library's folder, since the library would then index
    or move the same files the folder does (#1223). The reverse rule is
    :meth:`~pixlstash.hub.registry.LibraryRegistry.refuse_overlapping_watch_or_reference_folder`.
    Callers hold :data:`~pixlstash.hub.registry.FOLDER_OVERLAP_LOCK` from this
    check until their write commits; see :func:`holding_folder_overlap_lock`.

    Raises:
        HTTPException: 503 when the libraries could not be listed, 409 when
            *path* overlaps one of them.
    """
    for root in _roots_or_503(lambda: library_folders(server, vault), path):
        if path_is_within(path, root) or path_is_within(root, path):
            raise HTTPException(status_code=409, detail=FOLDER_OVERLAPS_LIBRARY_DETAIL)


def holding_folder_overlap_lock(func):
    """Run *func* holding :data:`~pixlstash.hub.registry.FOLDER_OVERLAP_LOCK`.

    For the watch and reference folder writes: the overlap check and the row it
    protects must be one step, or a concurrent library add passes this check
    and this write passes its check (#1223). ``functools.wraps`` keeps the
    signature FastAPI reads.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with FOLDER_OVERLAP_LOCK:
            return func(*args, **kwargs)

    return wrapper
