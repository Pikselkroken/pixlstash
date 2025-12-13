from sqlalchemy import Column, ForeignKey
from sqlmodel import SQLModel, Field, select


class PictureLikeness(SQLModel, table=True):
    """
    Database model for the picture_likeness table.
    Stores likeness scores for each (picture, picture) combination.
    Note, this is NOT face likeness, but overall picture likeness.
    """

    picture_id_a: str = Field(
        sa_column=Column(
            "picture_id_a",
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        )
    )
    picture_id_b: str = Field(
        sa_column=Column(
            "picture_id_b",
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        )
    )
    likeness: float = Field(default=None)
    metric: str = Field(default=None)

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
