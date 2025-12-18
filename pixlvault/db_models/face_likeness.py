from sqlalchemy import Column, ForeignKey
from sqlmodel import (
    CheckConstraint,
    Index,
    SQLModel,
    Field,
    Relationship,
    Session,
    select,
    text,
)
from typing import Optional, Tuple

from .face import Face


from sqlalchemy import Integer


class FaceLikeness(SQLModel, table=True):
    """
    Database model for the Face_likeness table.
    Stores likeness scores for each (Face, Face) combination.
    Note, this is NOT picture likeness, but individual face likeness.
    """

    face_id_a: int = Field(
        sa_column=Column(
            Integer, ForeignKey("face.id", ondelete="CASCADE"), primary_key=True
        )
    )
    face_id_b: int = Field(
        sa_column=Column(
            Integer, ForeignKey("face.id", ondelete="CASCADE"), primary_key=True
        )
    )
    likeness: float = Field(default=None, index=True)
    metric: str = Field(default=None)

    # Relationships
    face_a: Optional["Face"] = Relationship(
        back_populates="likeness_a",
        sa_relationship_kwargs={"foreign_keys": "[FaceLikeness.face_id_a]"},
    )
    face_b: Optional["Face"] = Relationship(
        back_populates="likeness_b",
        sa_relationship_kwargs={"foreign_keys": "[FaceLikeness.face_id_b]"},
    )

    # Table-level constraints and indexes
    __table_args__ = (
        # Enforce canonical ordering: a must be < b
        CheckConstraint("face_id_a < face_id_b", name="ck_face_pair_order"),
        # Optional covering index to accelerate range lookups by a
        Index("ix_face_likeness_a", "face_id_a"),
        # Optional index by b if you often query by b
        Index("ix_face_likeness_b", "face_id_b"),
    )

    @staticmethod
    def canon_pair(face_id_1: int, face_id_2: int) -> tuple[int, int]:
        """Return (a, b) ordered pair for given face IDs."""
        return (
            (face_id_1, face_id_2) if face_id_1 < face_id_2 else (face_id_2, face_id_1)
        )

    @classmethod
    def exists(cls, session, face_id_a: int, face_id_b: int) -> bool:
        """
        Check if a likeness entry exists for the given face ID pair.
        """
        a, b = cls.canon_pair(face_id_a, face_id_b)
        query = select(cls).where((cls.face_id_a == a) & (cls.face_id_b == b))
        result = session.exec(query).first()
        return result is not None

    @classmethod
    def bulk_insert_ignore(cls, session: Session, likeness_results):
        """
        Bulk insert FaceLikeness rows, ignoring duplicates (SQLite only).
        likeness_results: list of FaceLikeness objects or dicts.
        """
        if not likeness_results:
            return

        # Prepare rows as dicts
        rows = [
            {
                "face_id_a": r.face_id_a if hasattr(r, "face_id_a") else r["face_id_a"],
                "face_id_b": r.face_id_b if hasattr(r, "face_id_b") else r["face_id_b"],
                "likeness": r.likeness if hasattr(r, "likeness") else r["likeness"],
                "metric": r.metric if hasattr(r, "metric") else r["metric"],
            }
            for r in likeness_results
        ]

        session.execute(
            text("""
                INSERT OR IGNORE INTO facelikeness (face_id_a, face_id_b, likeness, metric)
                VALUES (:face_id_a, :face_id_b, :likeness, :metric)
            """),
            rows,
        )


class FaceLikenessFrontier(SQLModel, table=True):
    """
    Database model for the face_likeness_frontier table.
    Stores the current frontier of face pairs to compute likeness for.
    """

    face_id_a: int = Field(
        sa_column=Column(
            "face_id_a",
            ForeignKey("face.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    j_max: int = Field(default=None)

    __table_args__ = (
        CheckConstraint("j_max >= face_id_a", name="ck_frontier_order"),
        Index("ix_face_frontier_a", "face_id_a"),
    )

    @classmethod
    def ensure_all(cls, session: Session) -> None:
        """
        Ensure every Face has a frontier row.
        ORM variant: loads missing ids then bulk-adds.
        For very large N, a single INSERT...SELECT raw SQL is faster,
        but at your scale ORM is fine.
        """
        # Get all face ids
        face_ids = [
            row.id
            for row in session.exec(
                select(Face).where(Face.face_index != -1).order_by(Face.id)
            )
        ]
        # Existing frontier ids
        existing = set(session.exec(select(FaceLikenessFrontier.face_id_a)))
        # Compute missing
        missing = [fid for fid in face_ids if fid not in existing]
        if not missing:
            return
        # Create frontier rows with j_max = face_id_a
        for fid in missing:
            session.add(FaceLikenessFrontier(face_id_a=fid, j_max=fid))
        session.commit()

    @classmethod
    def max_face_id(cls, session: Session) -> int:
        res = session.exec(select(Face.id).order_by(Face.id.desc())).first()
        return int(res) if res is not None else 0

    @classmethod
    def update(cls, session: Session, a: int, new_jmax: int) -> None:
        pf = session.get(FaceLikenessFrontier, a)
        if pf is None:
            # Create if missing, respecting invariant j_max >= a
            session.add(FaceLikenessFrontier(face_id_a=a, j_max=max(a, new_jmax)))
        else:
            pf.j_max = max(a, new_jmax)
            session.add(pf)

    @classmethod
    def range_to_compare(
        cls,
        session: Session,
        a: int,
        max_id: Optional[int] = None,
        batch_limit: int = 5000,
    ) -> Optional[Tuple[int, int]]:
        """
        Compute the next contiguous [start_b, end_b] for face a.
        Ensures canonical a < b and respects frontier j_max.
        Returns None if nothing to do.
        """
        if max_id is None:
            max_id = cls.max_face_id(session)
        pf = session.get(FaceLikenessFrontier, a)
        if pf is None:
            # Initialize frontier on the fly
            pf = FaceLikenessFrontier(face_id_a=a, j_max=a)
            session.add(pf)
            session.commit()

        start_b = max(pf.j_max + 1, a + 1)
        if start_b > max_id:
            return None
        end_b = min(max_id, start_b + batch_limit - 1)
        return (start_b, end_b)

    @classmethod
    def smallest_a_with_work(cls, session: Session, max_id: int) -> Optional[int]:
        """
        Return the smallest face_id_a that has remaining work (j_max < max_face_id).
        Returns None if all work is done.
        """
        next_frontier = session.exec(
            select(cls.face_id_a)
            .where(cls.j_max < max_id)
            .order_by(cls.face_id_a)
            .limit(1)
        ).first()
        return next_frontier
