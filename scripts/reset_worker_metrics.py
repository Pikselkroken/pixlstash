import argparse
import os
import sqlite3


MEASURES = {
    "tags",
    "descriptions",
    "text_embeddings",
    "image_embeddings",
    "embeddings",
    "aesthetic_scores",
    "quality",
    "face_quality",
    "features",
    "likeness_queue",
    "likeness_parameters",
}


def reset_tags(cursor) -> None:
    print("Clearing tag tables...")
    cursor.execute("DELETE FROM face_tag")
    cursor.execute("DELETE FROM hand_tag")
    cursor.execute("DELETE FROM tag")


def reset_descriptions(cursor) -> None:
    print("Clearing picture descriptions...")
    cursor.execute("UPDATE picture SET description = NULL")


def reset_text_embeddings(cursor) -> None:
    print("Clearing text embeddings...")
    cursor.execute("UPDATE picture SET text_embedding = NULL")


def reset_image_embeddings(cursor) -> None:
    print("Clearing image embeddings and aesthetic scores...")
    cursor.execute("UPDATE picture SET image_embedding = NULL, aesthetic_score = NULL")


def reset_embeddings(cursor) -> None:
    print("Clearing image_embedding, text_embedding, and aesthetic_score...")
    cursor.execute(
        "UPDATE picture SET image_embedding = NULL, text_embedding = NULL, aesthetic_score = NULL"
    )


def reset_aesthetic_scores(cursor) -> None:
    print("Clearing aesthetic scores...")
    cursor.execute("UPDATE picture SET aesthetic_score = NULL")


def reset_quality(cursor) -> None:
    print("Clearing full-image quality rows...")
    cursor.execute("DELETE FROM quality WHERE face_id IS NULL")


def reset_face_quality(cursor) -> None:
    print("Clearing face quality rows...")
    cursor.execute("DELETE FROM quality WHERE face_id IS NOT NULL")


def reset_features(cursor) -> None:
    print("Clearing face and hand detections...")
    cursor.execute("DELETE FROM face_tag")
    cursor.execute("DELETE FROM hand_tag")
    cursor.execute("DELETE FROM face")
    cursor.execute("DELETE FROM hand")


def reset_likeness_queue(cursor) -> None:
    print("Clearing picture likeness tables...")
    cursor.execute("DELETE FROM picturelikeness")
    cursor.execute("DELETE FROM picturelikenessqueue")

    print("Rebuilding picture likeness queue...")
    cursor.execute("SELECT id FROM picture ORDER BY id")
    picture_ids = [row[0] for row in cursor.fetchall()]
    cursor.executemany(
        "INSERT OR IGNORE INTO picturelikenessqueue (picture_id, queued_at) VALUES (?, CURRENT_TIMESTAMP)",
        [(pid,) for pid in picture_ids],
    )
    print(f"Enqueued {len(picture_ids)} pictures. Likeness will be regenerated.")


def reset_likeness_parameters(cursor) -> None:
    print("Clearing likeness parameter vectors and size bins...")
    cursor.execute(
        "UPDATE picture SET likeness_parameters = NULL, size_bin_index = NULL"
    )


def apply_reset(cursor, measure: str) -> None:
    if measure == "tags":
        reset_tags(cursor)
    elif measure == "descriptions":
        reset_descriptions(cursor)
    elif measure == "text_embeddings":
        reset_text_embeddings(cursor)
    elif measure == "image_embeddings":
        reset_image_embeddings(cursor)
    elif measure == "embeddings":
        reset_embeddings(cursor)
    elif measure == "aesthetic_scores":
        reset_aesthetic_scores(cursor)
    elif measure == "quality":
        reset_quality(cursor)
    elif measure == "face_quality":
        reset_face_quality(cursor)
    elif measure == "features":
        reset_features(cursor)
    elif measure == "likeness_queue":
        reset_likeness_queue(cursor)
    elif measure == "likeness_parameters":
        reset_likeness_parameters(cursor)
    else:
        raise ValueError(f"Unknown measure '{measure}'")


def reset_worker_metrics(db_path: str, measures: list[str]) -> None:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        for measure in measures:
            apply_reset(cursor, measure)
        conn.commit()
        print("Reset complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset worker-related measures in the PixlStash database."
    )
    parser.add_argument("db_path", help="Path to vault.db")
    parser.add_argument(
        "-m",
        "--measure",
        action="append",
        required=True,
        choices=sorted(MEASURES),
        help="Measure to reset. Can be provided multiple times.",
    )
    args = parser.parse_args()
    reset_worker_metrics(args.db_path, args.measure)


if __name__ == "__main__":
    main()
