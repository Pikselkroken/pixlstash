import argparse
import os
import sqlite3


def reset_likeness_queue(db_path: str) -> None:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
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

        conn.commit()
        print(f"Enqueued {len(picture_ids)} pictures. Likeness will be regenerated.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset picture likeness and queue without touching embeddings."
    )
    parser.add_argument("db_path", help="Path to vault.db")
    args = parser.parse_args()
    reset_likeness_queue(args.db_path)


if __name__ == "__main__":
    main()
