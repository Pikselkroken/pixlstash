import argparse
import os
import sqlite3


def reset_embeddings_and_likeness(db_path: str) -> None:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()

        print("Clearing image_embedding, text_embedding, and aesthetic_score...")
        cursor.execute(
            """
            UPDATE picture
            SET image_embedding = NULL,
                text_embedding = NULL,
                aesthetic_score = NULL
            """
        )
        print(f"Reset embeddings for {cursor.rowcount} pictures.")

        print("Clearing picture likeness tables...")
        cursor.execute("DELETE FROM picturelikeness")
        cursor.execute("DELETE FROM picturelikenessfrontier")

        print("Rebuilding picture likeness frontier...")
        cursor.execute("SELECT id FROM picture ORDER BY id")
        picture_ids = [row[0] for row in cursor.fetchall()]
        cursor.executemany(
            "INSERT INTO picturelikenessfrontier (picture_id_a, j_max) VALUES (?, ?)",
            [(pid, pid) for pid in picture_ids],
        )

        conn.commit()
        print(
            f"Inserted {len(picture_ids)} frontier rows. Likeness will be regenerated."
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset embeddings and picture likeness frontier for regeneration."
    )
    parser.add_argument("db_path", help="Path to vault.db")
    args = parser.parse_args()
    reset_embeddings_and_likeness(args.db_path)


if __name__ == "__main__":
    main()
