from sqlalchemy import Column, ForeignKey, Index, text
from sqlmodel import CheckConstraint, SQLModel, Field, Relationship, Session, select
from typing import List, Optional, Tuple

from .picture import Picture


class PictureLikeness(SQLModel, table=True):
    """
    Database model for the picture_likeness table.
    Stores likeness scores for each (picture, picture) combination.
    Note, this is NOT face likeness, but overall picture likeness.
    """

    picture_id_a: int = Field(
        sa_column=Column(
            "picture_id_a",
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        )
    )
    picture_id_b: int = Field(
        sa_column=Column(
            "picture_id_b",
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        )
    )
    likeness: float = Field(default=None)
    metric: str = Field(default=None)

    # Relationships
    picture_a: Optional["Picture"] = Relationship(
        back_populates="likeness_a",
        sa_relationship_kwargs={"foreign_keys": "[PictureLikeness.picture_id_a]"},
    )
    picture_b: Optional["Picture"] = Relationship(
        back_populates="likeness_b",
        sa_relationship_kwargs={"foreign_keys": "[PictureLikeness.picture_id_b]"},
    )

    # Table-level constraints and indexes
    __table_args__ = (
        # Enforce canonical ordering: a must be < b
        CheckConstraint("picture_id_a < picture_id_b", name="ck_picture_pair_order"),
        # Optional covering index to accelerate range lookups by a
        Index("ix_picture_likeness_a", "picture_id_a"),
        # Optional index by b if you often query by b
        Index("ix_picture_likeness_b", "picture_id_b"),
    )

    @staticmethod
    def canon_pair(pic_id_1: int, pic_id_2: int) -> tuple[int, int]:
        """Return (a, b) ordered pair for given picture IDs."""
        return (pic_id_1, pic_id_2) if pic_id_1 < pic_id_2 else (pic_id_2, pic_id_1)

    @classmethod
    def find(
        cls,
        session,
        picture_id_a: str = None,
        picture_id_b: str = None,
    ):
        """
        Flexible search:
        - If both picture_id_a and picture_id_b are None: return all entries
        - If only one is present: return all pairs containing that picture as a or b
        - If both are present: return the specific pair or None
        """
        query = select(cls)
        if picture_id_a and picture_id_b:
            # Always order so a < b
            a, b = sorted([picture_id_a, picture_id_b])
            query = query.where((cls.picture_id_a == a) & (cls.picture_id_b == b))
            result = session.exec(query).first()
            return result
        elif picture_id_a:
            query = query.where(
                (cls.picture_id_a == picture_id_a) | (cls.picture_id_b == picture_id_a)
            )
            return session.exec(query).all()
        elif picture_id_b:
            query = query.where(
                (cls.picture_id_a == picture_id_b) | (cls.picture_id_b == picture_id_b)
            )
            return session.exec(query).all()
        else:
            return session.exec(query).all()

    @classmethod
    def exists(cls, session, picture_id_a: int, picture_id_b: int) -> bool:
        """
        Check if a likeness entry exists for the given picture ID pair.
        """
        a, b = cls.canon_pair(picture_id_a, picture_id_b)
        query = select(cls).where((cls.picture_id_a == a) & (cls.picture_id_b == b))
        result = session.exec(query).first()
        return result is not None

    @classmethod
    def bulk_insert_ignore(cls, session, likeness_results):
        """
        Bulk insert PictureLikeness rows, ignoring duplicates (SQLite only).
        likeness_results: list of PictureLikeness objects or dicts.
        """
        if not likeness_results:
            return
        # Prepare rows as dicts
        rows = [
            {
                "picture_id_a": r.picture_id_a
                if hasattr(r, "picture_id_a")
                else r["picture_id_a"],
                "picture_id_b": r.picture_id_b
                if hasattr(r, "picture_id_b")
                else r["picture_id_b"],
                "likeness": r.likeness if hasattr(r, "likeness") else r["likeness"],
                "metric": r.metric if hasattr(r, "metric") else r["metric"],
            }
            for r in likeness_results
        ]
        session.execute(
            text("""
                INSERT OR IGNORE INTO picturelikeness (picture_id_a, picture_id_b, likeness, metric)
                VALUES (:picture_id_a, :picture_id_b, :likeness, :metric)
            """),
            rows,
        )

    @classmethod
    def prune_below_top_k(cls, session: Session, picture_id_a: int, top_k: int) -> None:
        """
        Prune PictureLikeness entries to keep only the top K likenesses per picture_id_a.
        Deletes entries beyond the TOP_K highest likeness scores for each picture_id_a.
        """
        session.execute(
            text("""
                WITH ranked AS (
                    SELECT
                        picture_id_a,
                        picture_id_b,
                        ROW_NUMBER() OVER (
                            PARTITION BY picture_id_a
                            ORDER BY likeness DESC, picture_id_b ASC
                        ) AS rn
                    FROM picturelikeness
                    WHERE picture_id_a = :a
                )
                DELETE FROM picturelikeness
                WHERE (picture_id_a, picture_id_b) IN (
                    SELECT picture_id_a, picture_id_b
                    FROM ranked
                    WHERE rn > :top_k
                );
            """),
            {"a": picture_id_a, "top_k": top_k},
        )


class PictureLikenessFrontier(SQLModel, table=True):
    """
    Database model for the picture_likeness_frontier table.
    Stores the current frontier of picture pairs to compute likeness for.
    """

    picture_id_a: int = Field(
        sa_column=Column(
            "picture_id_a",
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    j_max: int = Field(default=None)

    __table_args__ = (
        CheckConstraint("j_max >= picture_id_a", name="ck_frontier_order"),
        Index("ix_picture_frontier_a", "picture_id_a"),
    )

    @classmethod
    def ensure_all(cls, session: Session) -> None:
        """
        Ensure every Picture has a frontier row.
        ORM variant: loads missing ids then bulk-adds.
        For very large N, a single INSERT...SELECT raw SQL is faster,
        but at your scale ORM is fine.
        """
        # Get all picture ids
        picture_ids = [
            row.id for row in session.exec(select(Picture).order_by(Picture.id))
        ]
        # Existing frontier ids
        existing = set(session.exec(select(PictureLikenessFrontier.picture_id_a)))
        # Compute missing
        missing = [pid for pid in picture_ids if pid not in existing]
        if not missing:
            return
        # Create frontier rows with j_max = picture_id_a
        for pid in missing:
            session.add(PictureLikenessFrontier(picture_id_a=pid, j_max=pid))
        session.commit()

    @classmethod
    def max_picture_id(cls, session: Session) -> int:
        res = session.exec(select(Picture.id).order_by(Picture.id.desc())).first()
        return int(res) if res is not None else 0

    @classmethod
    def update(cls, session: Session, a: int, new_jmax: int) -> None:
        pf = session.get(PictureLikenessFrontier, a)
        if pf is None:
            # Create if missing, respecting invariant j_max >= a
            session.add(PictureLikenessFrontier(picture_id_a=a, j_max=max(a, new_jmax)))
        else:
            pf.j_max = max(a, new_jmax)
            session.add(pf)
        session.commit()

    @classmethod
    def range_to_compare(
        cls,
        session: Session,
        a: int,
        max_id: Optional[int] = None,
        batch_limit: int = 5000,
    ) -> Optional[Tuple[int, int]]:
        """
        Compute the next contiguous [start_b, end_b] for picture a.
        Ensures canonical a < b and respects frontier j_max.
        Returns None if nothing to do.
        """
        if max_id is None:
            max_id = cls.max_picture_id(session)
        pf = session.get(PictureLikenessFrontier, a)
        if pf is None:
            # Initialize frontier on the fly
            pf = PictureLikenessFrontier(picture_id_a=a, j_max=a)
            session.add(pf)
            session.commit()

        start_b = max(pf.j_max + 1, a + 1)
        if start_b > max_id:
            return None
        end_b = min(max_id, start_b + batch_limit - 1)
        return (start_b, end_b)

    @classmethod
    def get_next_a_candidate(
        cls, session: Session, quality_ready: callable
    ) -> Optional[int]:
        """
        Find the smallest picture_id_a whose frontier hasn't reached max_id
        AND whose Quality is ready.
        """
        max_id = PictureLikenessFrontier.max_picture_id(session)
        if not max_id:
            return None

        rows = session.exec(
            select(PictureLikenessFrontier)
            .where(PictureLikenessFrontier.j_max < max_id)
            .order_by(PictureLikenessFrontier.picture_id_a)
        ).all()

        for pf in rows:
            a = int(pf.picture_id_a)
            if quality_ready(session, a):
                return a

        return None  # no eligible 'a' with quality

    @classmethod
    def consecutive_prefix(cls, start_b: int, eligible_bs: List[int]) -> List[int]:
        """
        Given start_b and a set/list of eligible b values in the window,
        return the longest consecutive prefix starting at start_b.
        """
        if not eligible_bs:
            return []
        # Use a set for O(1) membership
        s = set(eligible_bs)
        result = []
        b = start_b
        while b in s:
            result.append(b)
            b += 1
        return result
