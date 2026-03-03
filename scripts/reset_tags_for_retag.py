import argparse
import os
import sqlite3


def reset_tags_for_retag(db_path: str) -> None:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM tag")
        tag_count = cursor.fetchone()[0]

        print(f"Current tags: {tag_count}")

        print("Clearing face_tag, hand_tag, and tag tables...")
        cursor.execute("DELETE FROM tag")

        conn.commit()
        print("Tag tables cleared. TagWorker should regenerate tags.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clear all tags so TagWorker will regenerate them."
    )
    parser.add_argument("db_path", help="Path to vault.db")
    args = parser.parse_args()
    reset_tags_for_retag(args.db_path)


if __name__ == "__main__":
    main()
