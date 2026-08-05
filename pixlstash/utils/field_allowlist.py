"""Deny-by-default allowlist of servable field names for the generic by-name
field readers (issue #721).

Two routes hand back *any* attribute of their object by name:
``GET /pictures/{id}/{field}`` and ``GET /characters/{id}/{field}``. Both end in
``safe_model_dict(getattr(obj, field))``, and ``safe_model_dict`` recurses into
SQLModel instances, lists and ``CollectionAdapter``s — so an ORM **relationship**
name is served as whole related rows. ``select_fields=[field]`` does not bound
that, which is why a picture-scoped token could read ``/pictures/{id}/projects``
(project names it is 403'd on by name), ``/pictures/{id}/picture_sets`` and
``/pictures/{id}/characters``, and a character-scoped token could read
``/characters/{id}/project`` and ``/characters/{id}/pictures`` — the last serving
full ``Picture`` rows off the relationship, bypassing every projection and every
narrowing site in the codebase.

**What this module bounds, and what it does not.** The AuthzGate (§16.2) answers
"may this token reach this *object*". It runs before the handler and never sees
the response, so it cannot bound *which of the object's attributes* come back.
That is the gap this closes, and it closes it the same way the gate does its own
job: deny by default. The servable set is derived from the model's own column
namespace, so a future relationship is refused without anyone remembering to,
and a future column needs no code change here.

**This is response-shape validation, not authorization.** It must not grow into a
second scope ladder — per ``CLAUDE.md`` the gate owns object authorization and a
duplicate check is debt. ``require_servable_field`` therefore takes no request,
no token and no session, and is called *before* any database read.

**Known adjacent gap, deliberately not closed here.** Allowlisting the whole
column namespace still serves ``pending_character_id``, ``source_picture_id`` and
``reference_folder_id`` — cross-object ids that
``tests/test_architecture_guardrails.py::test_picture_metadata_fields_membership_is_pinned``
already records as known disclosures. Narrowing the column set itself is the
residual tracked in §16.6 under #719; it is a separate change because
``Picture.metadata_fields()`` is also the default ``select_fields`` of
``Picture.find`` and feeds the scoring and export paths.
"""

from typing import Iterable

from fastapi import HTTPException

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Names that are servable even though they are not columns of the model.
#
# Every member is a deliberate, reviewed exception, and the guardrail test
# ``tests/test_generic_field_reader_allowlist.py`` fails the build if this set
# grows without one. Keep it tiny; the point of the allowlist is that the default
# answer is "no".

#: ``faces`` is a ``Picture`` **relationship**, and it is the SPA's live face-box
#: overlay: ``frontend/src/api/pictures.js::listPictureFaces`` calls
#: ``GET /pictures/{id}/faces`` (consumed by ``ImageOverlay.vue``), and
#: ``tests/utils.py::wait_for_faces`` polls the same URL. There is **no dedicated
#: ``GET /pictures/{id}/faces`` route** to route those callers at — only
#: ``POST /pictures/{id}/face`` — so denying the name here would break a shipping
#: feature. It is kept servable pending the decision recorded on #721: give the
#: overlay a dedicated, projected endpoint and then drop this exception. The rows
#: it serves are ``Face`` rows of the caller's *own* picture (``picture_id``,
#: ``character_id``, ``bbox``, and the ``features`` embedding), which is a far
#: narrower disclosure than the project/set/character relationships this module
#: refuses — but it is not nothing, and it is the reason this set is not empty.
PICTURE_EXTRA_SERVABLE_FIELDS = frozenset({"faces"})

#: ``thumbnail`` is not a ``Character`` column at all — the handler generates a
#: 64x64 face crop and returns image bytes. It is a live frontend consumer
#: (``frontend/src/api/characters.js::getCharacterThumbnail``, and the server
#: hands the SPA ``/characters/{id}/thumbnail`` URLs itself), so it must stay
#: servable. ``faces`` is a ``Character`` relationship kept for the same reason
#: as its picture twin above (``tests/test_server.py`` reads it; there is no
#: dedicated ``GET /characters/{id}/faces``, only ``POST``/``DELETE``).
CHARACTER_EXTRA_SERVABLE_FIELDS = frozenset({"thumbnail", "faces"})


def servable_field_names(model, extra_servable: Iterable[str] = ()) -> frozenset[str]:
    """Return the field names a generic by-name reader may serve for *model*.

    Args:
        model: A SQLModel class exposing ``scalar_fields()`` (its column names).
        extra_servable: Reviewed non-column names to admit as well, e.g. a
            handler-synthesised ``thumbnail``.

    Returns:
        The frozen set of servable names: the model's own column namespace plus
        the declared exceptions. Every relationship not named in
        *extra_servable* is excluded, which is the point.
    """
    return frozenset(model.scalar_fields()) | frozenset(extra_servable)


def require_servable_field(
    model, field: str, extra_servable: Iterable[str] = ()
) -> None:
    """Refuse *field* unless it is servable for *model*.

    Call this **first**, before the object is loaded. Two reasons, both
    deliberate:

    1. **It cannot become an object-existence oracle.** Because no database read
       has happened, the refusal is identical whether the object exists or not:
       ``GET /pictures/999999/projects`` and ``GET /pictures/1/projects`` return
       byte-identical responses. (The cross-token case is already handled — the
       AuthzGate 403s an out-of-scope object before the handler runs at all,
       whatever the field name.)
    2. **A relationship and a typo are indistinguishable.** Both get the same
       status and the same body template, so the response does not enumerate the
       ORM relationship namespace.

    The status is **400, not 403 and not 404**, and the choice is load-bearing
    for the client contract:

    * ``403`` would be wrong twice over — this is not an authorization decision
      (the gate already allowed the object), and it collides with the gate's own
      403, so a client would read a mis-spelled field name as an auth failure.
    * ``404`` collides with "this object does not exist"
      (``{"detail": "Picture not found"}``), which is a genuinely different
      outcome a client must handle differently. Distinguishing them would mean
      string-matching ``detail``.
    * ``400`` says what is true: the ``{field}`` path segment is client-supplied
      input drawn from a finite set, and this value is not in it. It is emitted
      by neither reader for any other reason, so a client can branch on the
      status alone: 400 = not a readable field (render nothing, do not raise),
      404 = object gone, 403 = not yours, 5xx = server fault.

    The body echoes the caller's own input and nothing else, so it is constant
    with respect to *server state*.

    Args:
        model: A SQLModel class exposing ``scalar_fields()``.
        field: The ``{field}`` path segment as supplied by the caller.
        extra_servable: Reviewed non-column names to admit as well.

    Raises:
        HTTPException: 400 if *field* is not servable for *model*.
    """
    if field in servable_field_names(model, extra_servable):
        return
    logger.warning(
        "Refused generic field read: model=%s field=%r is not a servable field "
        "(not a column, and not a declared exception). Relationship names are "
        "refused on purpose - see issue #721.",
        model.__name__,
        field,
    )
    raise HTTPException(
        status_code=400,
        detail=f"Field '{field}' is not readable on this endpoint",
    )
