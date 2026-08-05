"""Deny-by-default allowlist of servable field names for the generic by-name
field readers (issue #721).

Two routes hand back *any* attribute of their object by name:
``GET /pictures/{id}/{field}`` and ``GET /characters/{id}/{field}``. Both end in
``safe_model_dict(getattr(obj, field))``, and ``safe_model_dict`` recurses into
SQLModel instances, lists and ``CollectionAdapter``s, so an ORM **relationship**
name is served as whole related rows. ``select_fields=[field]`` does not bound
that, which is why a picture-scoped token could read ``/pictures/{id}/projects``
(project names it is 403'd on by name), ``/pictures/{id}/picture_sets`` and
``/pictures/{id}/characters``, and a character-scoped token could read
``/characters/{id}/project`` and ``/characters/{id}/pictures``. The last serves
full ``Picture`` rows off the relationship, bypassing every projection and every
narrowing site in the codebase.

Relationships that a consumer genuinely needs get a **dedicated, projected
route** instead of an exception here. ``faces`` was the one such case and is now
served by ``GET /pictures/{id}/faces`` and ``GET /characters/{id}/faces``
(``routes/pictures/_faces.py`` and ``routes/characters.py``), which return the
same ``{"faces": [...]}`` wire shape minus the ``features`` embedding. Both
exception sets below are now down to the single synthetic ``thumbnail``.

**What this module bounds, and what it does not.** The AuthzGate (§16.2) answers
"may this token reach this *object*". It runs before the handler and never sees
the response, so it cannot bound *which of the object's attributes* come back.
That is the gap this closes, and it closes it the same way the gate does its own
job: deny by default. The servable set is derived from the model's own column
namespace, so a future relationship is refused without anyone remembering to,
and a future column needs no code change here.

**This is response-shape validation, not authorization.** It must not grow into a
second scope ladder: per ``CLAUDE.md`` the gate owns object authorization and a
duplicate check is debt. ``require_servable_field`` therefore takes no request,
no token and no session, and is called *before* any database read.

**Known adjacent gap, deliberately not closed here.** Allowlisting the whole
column namespace still serves ``pending_character_id``, ``source_picture_id`` and
``reference_folder_id``, cross-object ids that
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

#: Empty, and that is the goal state: every ``Picture`` relationship is refused.
#:
#: ``faces`` used to sit here, because the SPA's face-box overlay
#: (``frontend/src/api/pictures.js::listPictureFaces``) and
#: ``tests/utils.py::wait_for_faces`` both read it and there was no other route
#: serving it. It was removed once ``GET /pictures/{id}/faces`` shipped as a
#: dedicated, projected endpoint (``routes/pictures/_faces.py``), which serves
#: the same ``{"faces": [...]}`` wire shape minus the ``features`` embedding.
#: That is the pattern for anything that lands here: give the consumer a real
#: endpoint, then empty the exception back out.
PICTURE_EXTRA_SERVABLE_FIELDS: frozenset[str] = frozenset()

#: ``thumbnail`` is not a ``Character`` column at all: the handler generates a
#: 64x64 face crop and returns image bytes. It is a live frontend consumer
#: (``frontend/src/api/characters.js::getCharacterThumbnail``, and the server
#: hands the SPA ``/characters/{id}/thumbnail`` URLs itself), so it must stay
#: servable. It is *synthetic*, not a relationship: it discloses no related
#: rows, which is why it is the one member that does not need retiring.
#:
#: ``faces`` also used to sit here and was removed for the same reason as its
#: picture twin above: ``GET /characters/{id}/faces`` now serves it projected.
CHARACTER_EXTRA_SERVABLE_FIELDS = frozenset({"thumbnail"})


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
       byte-identical responses. (The cross-token case is already handled: the
       AuthzGate 403s an out-of-scope object before the handler runs at all,
       whatever the field name.)
    2. **A relationship and a typo are indistinguishable.** Both get the same
       status and the same body template, so the response does not enumerate the
       ORM relationship namespace.

    The status is **400, not 403 and not 404**, and the choice is load-bearing
    for the client contract:

    * ``403`` would be wrong twice over: this is not an authorization decision
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
