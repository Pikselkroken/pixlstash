from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, create_engine, select

from pixlstash.db_models.picture import Picture
from pixlstash.db_models.tag import Tag
from pixlstash.tasks.tag_task import TagTask


def test_add_tags_bulk_skips_missing_picture_ids(tmp_path):
    db_path = tmp_path / "tag-task.db"
    engine = create_engine(f"sqlite:///{db_path}", echo=False, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        picture = Picture(file_path="existing-picture.jpg")
        session.add(picture)
        session.commit()
        session.refresh(picture)

        updates = [
            {"pic_id": picture.id, "tags": ["jewelry"]},
            {"pic_id": picture.id + 9999, "tags": ["face"]},
        ]

        updated_ids = TagTask._add_tags_bulk(session, updates)

        assert updated_ids == [picture.id]

        saved_tags = session.exec(
            select(Tag.tag).where(Tag.picture_id == picture.id)
        ).all()
        assert set(saved_tags) == {"jewelry"}

        all_tag_rows = session.exec(select(Tag)).all()
        assert len(all_tag_rows) == 1
