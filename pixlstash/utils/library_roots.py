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
"""

from collections.abc import Iterable

from fastapi import HTTPException

from pixlstash.pixl_logging import get_logger
from pixlstash.services.config_service import get_import_folder_paths
from pixlstash.utils.path_utils import LibraryRootsUnavailable, path_is_within
from pixlstash.utils.reference_folder_validator import canonical_path

logger = get_logger(__name__)

#: Said once, to every caller, because the answer is the same one on each of
#: their screens: this folder already belongs to a library, choose another.
PATH_INSIDE_LIBRARY_DETAIL = (
    "That folder is part of your library - PixlStash reads it - so anything "
    "put there would be imported straight back in, or moved out from under "
    "the library that owns it. Choose a folder outside this library, its "
    "reference folders and its import folders, and outside any other "
    "library's folder."
)

#: The lists below are a *blocklist*, so one that cannot be read must refuse.
#: Treating a failed read as an empty list turns the check off silently and
#: permits exactly what it exists to refuse (#1177 item 59).
ROOTS_UNAVAILABLE_DETAIL = (
    "PixlStash could not check your reference folders, watched folders and "
    "libraries just now, so it cannot confirm this folder is outside them. "
    "Try again in a moment."
)


def library_content_roots(server, vault) -> list[str]:
    """Every directory this installation reads or writes as library content.

    Args:
        server: The running server, for the hub's library registry.
        vault: The vault whose reference and import folders to include. Callers
            hold a lease on one library; the registry supplies the rest.

    Returns:
        The root directories, in no particular order and not resolved. Use
        :func:`~pixlstash.utils.path_utils.path_is_within`, which resolves both
        sides, rather than comparing these as strings.

    Raises:
        LibraryRootsUnavailable: A list could not be read, so the answer is
            "we do not know" rather than "there are none".
    """
    roots = [getattr(vault, "image_root", None)]
    try:
        roots.extend(vault.reference_folder_roots())
        roots.extend(get_import_folder_paths(vault))
        # Every OTHER registered library is the same hazard as this one: a
        # library's folder IS its image_root (it is where `vault.db` lives), so
        # files written into one come back as new pictures the moment that
        # library is opened, and only the active lease's vault is open here.
        # Registered paths are all that is read - another library's reference
        # and watch folders live in ITS vault, and opening someone else's vault
        # to answer this question is a worse trade than the narrower check.
        #
        # `include_detached=True`: a detached library is not being read *today*,
        # but re-attaching it is one click and everything written into it then
        # imports as new pictures. One keyword, no extra read.
        roots.extend(
            library.path
            for library in server.library_registry.list_libraries(include_detached=True)
        )
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


def refuse_path_inside_a_library(
    path: str,
    server,
    vault,
    *,
    ignore_roots: Iterable[str] = (),
    status_code: int = 409,
) -> None:
    """Refuse *path* when it lies inside a directory the installation owns.

    Args:
        path: An absolute path a caller wants files written to or moved into.
        server: The running server.
        vault: The vault holding the active lease.
        ignore_roots: Roots that are *this caller's own* and so not a conflict -
            the reference folder being repointed, for instance. Compared after
            :func:`~pixlstash.utils.reference_folder_validator.canonical_path`,
            so an alias of the same directory is still ignored.
        status_code: The refusal's status. 409 (a conflict with something
            already registered) everywhere except the folder export, which has
            answered 400 since #1177 and whose callers read that.

    Raises:
        HTTPException: 503 when the roots could not be listed, *status_code*
            when *path* is inside one of them.
    """
    try:
        roots = library_content_roots(server, vault)
    except LibraryRootsUnavailable as exc:
        logger.warning(
            "Refusing %s: the roots this installation owns could not be "
            "listed, so it cannot be shown to be outside them: %s",
            path,
            exc,
        )
        raise HTTPException(status_code=503, detail=ROOTS_UNAVAILABLE_DETAIL) from exc

    ignored = {canonical_path(root) for root in ignore_roots if root}
    for root in roots:
        if canonical_path(root) in ignored:
            continue
        if path_is_within(path, root):
            raise HTTPException(
                status_code=status_code, detail=PATH_INSIDE_LIBRARY_DETAIL
            )
