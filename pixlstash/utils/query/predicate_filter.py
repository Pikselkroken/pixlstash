"""Declarative, serialisable predicate over :class:`Picture` rows.

PixlStash selects ``Picture`` rows by the same vocabulary (score range, smart-score
bucket, resolution bucket, ComfyUI model/LoRA membership, tag include/exclude, hidden
tags, tag-confidence thresholds, format, deleted/imported flags, face presence, path
prefix, …) in several independent places.  Historically the WHERE-clause logic — down
to byte-for-byte copies of raw ``text()`` ``EXISTS`` / ``json_each`` snippets — was
duplicated across all of them.

``PredicateFilter`` is the single source of truth for that logic.  It has three
consumers:

* **compile** — :meth:`predicates` / :meth:`apply` turn the declarative fields into
  SQLAlchemy WHERE clauses (the raw ``text()`` snippets are moved here verbatim, bound
  params intact).
* **match** — :meth:`matches` narrows the same predicate set to a single picture id.
  This is exactly the set predicate restricted to one row; there is no separate
  Python-side re-implementation of the SQL semantics.  Staging auto-triage consumes
  this.
* **parse** — :meth:`from_query_params` builds the filter from request query params
  (one parser, replacing the per-route copies).

The name is ``PredicateFilter`` (not ``Filter``) because "filter" already means an
image-manipulation plugin in this codebase (see ``pixlstash/image_plugins/``).

Membership filters (``set``/``character``/``project``) are intentionally *out of
scope*: they resolve to candidate-id sets and stay in ``filter_helpers.py``.  Compose
with a pre-resolved set via the caller's own ``Picture.id.in_(...)`` clause.
"""

import os
from typing import List, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, text
from sqlalchemy.sql.elements import ColumnElement

from pixlstash.db_models.picture import Picture


def comfyui_leaf_parts(
    models_filter: Optional[List[str]],
    loras_filter: Optional[List[str]],
) -> tuple[list[str], list[str], dict]:
    """Build the leaf ``json_each EXISTS`` SQL fragments for ComfyUI membership.

    Two parallel fragment lists are returned plus a shared bind-param dict:

    * ``self_parts``   – fragments testing the picture row itself.
    * ``member_parts`` – equivalent fragments testing an aliased stack-member row
      (``_m``), used by :meth:`Picture.find` for stack-leader expansion.

    Both lists use the same bind-param names so the dict applies to either.  This is
    the single definition of the ComfyUI leaf snippet; callers decide how to combine
    the fragments (``AND`` of separate clauses vs. ``OR`` within one clause, with or
    without stack expansion).
    """
    self_parts: list[str] = []
    member_parts: list[str] = []
    bind_params: dict = {}
    if models_filter:
        for i, m in enumerate(models_filter):
            self_parts.append(
                f"EXISTS (SELECT 1 FROM json_each(picture.comfyui_models) WHERE value = :cmf_{i})"
            )
            member_parts.append(
                f"EXISTS (SELECT 1 FROM json_each(_m.comfyui_models) WHERE value = :cmf_{i})"
            )
            bind_params[f"cmf_{i}"] = m
    if loras_filter:
        for i, m in enumerate(loras_filter):
            self_parts.append(
                f"EXISTS (SELECT 1 FROM json_each(picture.comfyui_loras) WHERE value = :clf_{i})"
            )
            member_parts.append(
                f"EXISTS (SELECT 1 FROM json_each(_m.comfyui_loras) WHERE value = :clf_{i})"
            )
            bind_params[f"clf_{i}"] = m
    return self_parts, member_parts, bind_params


class PredicateFilter(BaseModel):
    """A declarative, serialisable predicate over ``Picture`` rows.

    All fields are optional / defaulted.  The defaults describe the predicate used by
    a "give me the matching live pictures" query: non-deleted, no other restrictions
    — which is exactly what single-picture :meth:`matches` wants for auto-triage.

    Each builder site sets the subset of fields it needs and toggles the
    flag fields (``include_deleted`` / ``only_deleted`` / ``apply_deleted_filter`` /
    ``include_unimported``) so that the centralised compiler reproduces that site's
    emitted predicate exactly.
    """

    # --- intrinsic attribute predicates ---
    format: Optional[List[str]] = None
    min_score: Optional[int] = None
    max_score: Optional[int] = None
    smart_score_bucket: Optional[str] = None
    resolution_bucket: Optional[str] = None
    comfyui_models_filter: Optional[List[str]] = None
    comfyui_loras_filter: Optional[List[str]] = None
    tags_filter: Optional[List[str]] = None
    tags_rejected_filter: Optional[List[str]] = None
    hidden_tags_filter: Optional[List[str]] = None
    tags_confidence_above_filter: Optional[List[str]] = None
    tags_confidence_below_filter: Optional[List[str]] = None
    face_filter: Optional[str] = None
    file_path_prefix: Optional[str] = None
    # ``find()`` browses a folder and shows direct children only (separator-aware,
    # LIKE-escaped).  The stats sidebar instead aggregates the whole sub-tree under a
    # prefix.  ``True`` selects the children-only behaviour; ``False`` the sub-tree
    # ``startswith`` behaviour.
    file_path_prefix_children_only: bool = True
    import_source_folder: Optional[str] = None

    # --- scope / lifecycle flags ---
    include_deleted: bool = False
    only_deleted: bool = False
    # When ``False`` the caller applies the deleted predicate itself (e.g. woven into
    # a membership branch) and the compiler emits no ``deleted`` clause.
    apply_deleted_filter: bool = True
    include_unimported: bool = True

    def _smart_score_bucket_predicates(self) -> list[ColumnElement]:
        bucket = self.smart_score_bucket
        if bucket == "unscored":
            return [Picture.smart_score.is_(None)]
        if bucket == "1-2":
            return [Picture.smart_score.is_not(None), Picture.smart_score < 2.0]
        if bucket == "2-3":
            return [Picture.smart_score >= 2.0, Picture.smart_score < 3.0]
        if bucket == "3-4":
            return [Picture.smart_score >= 3.0, Picture.smart_score < 4.0]
        if bucket == "4-5":
            return [Picture.smart_score >= 4.0]
        return []

    def _resolution_bucket_predicates(self) -> list[ColumnElement]:
        bucket = self.resolution_bucket
        if bucket == "unknown":
            return [or_(Picture.width.is_(None), Picture.height.is_(None))]
        area = Picture.width * Picture.height
        not_null = [Picture.width.is_not(None), Picture.height.is_not(None)]
        if bucket == "lt1mp":
            return [*not_null, area < 1_000_000]
        if bucket == "1-4mp":
            return [*not_null, area >= 1_000_000, area < 4_000_000]
        if bucket == "4-8mp":
            return [*not_null, area >= 4_000_000, area < 8_000_000]
        if bucket == "8-16mp":
            return [*not_null, area >= 8_000_000, area < 16_000_000]
        if bucket == "16plus":
            return [*not_null, area >= 16_000_000]
        return []

    def _file_path_prefix_predicates(self) -> list[ColumnElement]:
        if self.file_path_prefix is None:
            return []
        if not self.file_path_prefix_children_only:
            # Sub-tree match used by the stats sidebar.
            return [Picture.file_path.startswith(self.file_path_prefix)]
        # Normalise to always end with a path separator so that a prefix like
        # "/ref/photos" does not accidentally match "/ref/photos2/a.jpg".  Support
        # both Unix ("/") and Windows ("\") separators.
        if self.file_path_prefix.endswith("/") or self.file_path_prefix.endswith("\\"):
            prefix = self.file_path_prefix
        else:
            prefix = self.file_path_prefix + os.sep
        # Escape LIKE special characters in the literal prefix.
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        # Only show direct children — exclude files that have another path separator
        # after the prefix (i.e. files in sub-directories).  Check for both "/" and
        # "\" to handle all platforms.
        return [
            Picture.file_path.like(escaped + "%", escape="\\"),
            ~Picture.file_path.like(escaped + "%/%", escape="\\"),
            ~Picture.file_path.like(escaped + "%\\\\%", escape="\\"),
        ]

    def predicates(self) -> list[ColumnElement]:
        """Compile this filter to a list of SQLAlchemy WHERE clauses.

        The clauses are intended to be ANDed together (``stmt.where(*predicates)``).
        ComfyUI membership is emitted as one clause per model/LoRA (AND semantics);
        ``Picture.find`` deliberately keeps its own OR + stack-expansion variant and
        therefore leaves ``comfyui_*_filter`` unset here.
        """
        preds: list[ColumnElement] = []

        if self.apply_deleted_filter:
            if self.only_deleted:
                preds.append(Picture.deleted.is_(True))
            elif not self.include_deleted:
                preds.append(Picture.deleted.is_(False))

        if not self.include_unimported:
            preds.append(Picture.imported_at.is_not(None))

        preds.extend(self._file_path_prefix_predicates())

        if self.import_source_folder:
            preds.append(Picture.import_source_folder == self.import_source_folder)

        if self.format:
            preds.append(Picture.format.in_(self.format))

        if self.min_score is not None:
            preds.append(Picture.score >= self.min_score)
        if self.max_score is not None:
            preds.append(Picture.score <= self.max_score)

        preds.extend(self._smart_score_bucket_predicates())
        preds.extend(self._resolution_bucket_predicates())

        if self.tags_filter:
            for i, tag in enumerate(self.tags_filter):
                preds.append(
                    text(
                        f"EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :tag_filter_{i})"
                    ).bindparams(**{f"tag_filter_{i}": tag})
                )

        if self.tags_rejected_filter:
            for i, tag in enumerate(self.tags_rejected_filter):
                preds.append(
                    text(
                        f"NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :rejected_tag_filter_{i})"
                    ).bindparams(**{f"rejected_tag_filter_{i}": tag})
                )

        if self.hidden_tags_filter:
            placeholders = ", ".join(
                f":ht_{i}" for i in range(len(self.hidden_tags_filter))
            )
            preds.append(
                text(
                    f"NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id"
                    f" AND LOWER(tag.tag) IN ({placeholders}))"
                ).bindparams(
                    **{f"ht_{i}": t for i, t in enumerate(self.hidden_tags_filter)}
                )
            )

        if self.tags_confidence_above_filter:
            for i, entry in enumerate(self.tags_confidence_above_filter):
                tag, threshold = entry.rsplit(":", 1)
                if float(threshold) <= 0.0:
                    # NOTE: the whole disjunction is wrapped in an extra outer pair of
                    # parentheses.  SQL ``AND`` binds tighter than ``OR``, so without
                    # the wrapper this clause leaks its ``OR`` when ANDed with the
                    # other predicates (e.g. the ``Picture.id == id`` narrowing in
                    # ``matches()``), letting the second branch escape every other
                    # filter.  The pre-refactor copies omitted this wrapper.
                    preds.append(
                        text(
                            f"(("
                            f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                            f" AND tag_prediction.tag = :ca_tag_{i} AND tag_prediction.confidence >= :ca_thresh_{i})"
                            f" AND NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ca_tag_{i})"
                            f") OR ("
                            f"EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ca_tag_{i})"
                            f" AND NOT EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id AND tag_prediction.tag = :ca_tag_{i})"
                            f"))"
                        ).bindparams(
                            **{f"ca_tag_{i}": tag, f"ca_thresh_{i}": float(threshold)}
                        )
                    )
                else:
                    preds.append(
                        text(
                            f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                            f" AND tag_prediction.tag = :ca_tag_{i} AND tag_prediction.confidence >= :ca_thresh_{i})"
                            f" AND NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ca_tag_{i})"
                        ).bindparams(
                            **{f"ca_tag_{i}": tag, f"ca_thresh_{i}": float(threshold)}
                        )
                    )

        if self.tags_confidence_below_filter:
            for i, entry in enumerate(self.tags_confidence_below_filter):
                tag, threshold = entry.rsplit(":", 1)
                preds.append(
                    text(
                        f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                        f" AND tag_prediction.tag = :cb_tag_{i} AND tag_prediction.confidence < :cb_thresh_{i})"
                        f" AND EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :cb_tag_{i})"
                    ).bindparams(
                        **{f"cb_tag_{i}": tag, f"cb_thresh_{i}": float(threshold)}
                    )
                )

        # ComfyUI membership: AND of one EXISTS per model/LoRA (used by
        # semantic_search / listing-candidate / grouped-misc sites).  ``find()`` does
        # not set these fields — it applies its own OR + stack-expansion variant.
        if self.comfyui_models_filter:
            for i, m in enumerate(self.comfyui_models_filter):
                preds.append(
                    text(
                        f"EXISTS (SELECT 1 FROM json_each(picture.comfyui_models) WHERE value = :cmf_{i})"
                    ).bindparams(**{f"cmf_{i}": m})
                )
        if self.comfyui_loras_filter:
            for i, m in enumerate(self.comfyui_loras_filter):
                preds.append(
                    text(
                        f"EXISTS (SELECT 1 FROM json_each(picture.comfyui_loras) WHERE value = :clf_{i})"
                    ).bindparams(**{f"clf_{i}": m})
                )

        if self.face_filter == "with_face":
            preds.append(
                text(
                    "EXISTS (SELECT 1 FROM face WHERE face.picture_id = picture.id AND face.face_index != -1)"
                )
            )
        elif self.face_filter == "without_face":
            preds.append(
                text(
                    "NOT EXISTS (SELECT 1 FROM face WHERE face.picture_id = picture.id AND face.face_index != -1)"
                )
            )

        return preds

    def apply(self, stmt):
        """Apply every compiled predicate to ``stmt`` and return the new statement."""
        for clause in self.predicates():
            stmt = stmt.where(clause)
        return stmt

    def matches(self, session, picture_id: int) -> bool:
        """Return ``True`` if the single picture ``picture_id`` satisfies this filter.

        This is the set predicate narrowed to one id — the hook consumed by staging
        auto-triage.  There is no separate Python-side evaluator; the SQL semantics
        are the same as the set queries.
        """
        from sqlmodel import select

        stmt = self.apply(select(Picture.id)).where(Picture.id == picture_id)
        return session.exec(stmt).first() is not None

    @classmethod
    def from_query_params(
        cls, request, *, children_only: bool = True
    ) -> "PredicateFilter":
        """Build a filter from the intrinsic-attribute query params on ``request``.

        Covers the vocabulary shared by the picture-listing routes.  Membership
        params (``set``/``character``/``project``) and pagination/sort are *not*
        read here — they are resolved separately by the caller.  Unspecified params
        simply leave their field at the default, so a route that never sends a given
        param is unaffected.
        """
        qp = request.query_params

        def _int_or_none(name: str) -> Optional[int]:
            raw = qp.get(name)
            if raw is None:
                return None
            try:
                return int(raw)
            except (TypeError, ValueError) as exc:
                # A malformed numeric query param is a client error, not a
                # server fault. Raise 422 with a clear message instead of
                # letting a bare int() bubble up as an unhandled 500.
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid {name}: must be an integer",
                ) from exc

        return cls(
            format=qp.getlist("format") or None,
            min_score=_int_or_none("min_score"),
            max_score=_int_or_none("max_score"),
            smart_score_bucket=qp.get("smart_score_bucket") or None,
            resolution_bucket=qp.get("resolution_bucket") or None,
            comfyui_models_filter=qp.getlist("comfyui_model") or None,
            comfyui_loras_filter=qp.getlist("comfyui_lora") or None,
            tags_filter=qp.getlist("tag") or None,
            tags_rejected_filter=qp.getlist("rejected_tag") or None,
            hidden_tags_filter=qp.getlist("hidden_tag") or None,
            tags_confidence_above_filter=qp.getlist("tag_confidence_above") or None,
            tags_confidence_below_filter=qp.getlist("tag_confidence_below") or None,
            face_filter=qp.get("face_filter") or None,
            file_path_prefix=qp.get("file_path_prefix") or None,
            file_path_prefix_children_only=children_only,
            import_source_folder=qp.get("import_source_folder") or None,
        )
