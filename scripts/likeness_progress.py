import argparse
import os
import sqlite3


def get_progress(db_path: str) -> None:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM picture")
        total_pictures = cursor.fetchone()[0] or 0

        cursor.execute("SELECT MAX(id) FROM picture")
        max_picture_id = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM picturelikenessfrontier")
        frontier_rows = cursor.fetchone()[0] or 0

        cursor.execute(
            "SELECT COUNT(*) FROM picturelikenessfrontier WHERE j_max < ?",
            (max_picture_id,),
        )
        remaining_a = cursor.fetchone()[0] or 0

        cursor.execute(
            "SELECT COALESCE(SUM(j_max - picture_id_a), 0) FROM picturelikenessfrontier"
        )
        completed_pairs = cursor.fetchone()[0] or 0

        total_pairs = (total_pictures * (total_pictures - 1)) // 2
        remaining_pairs = max(0, total_pairs - completed_pairs)

        cursor.execute("SELECT COUNT(*) FROM picture WHERE image_embedding IS NOT NULL")
        ready_embeddings = cursor.fetchone()[0] or 0

        print(f"Pictures: {total_pictures}")
        print(f"Frontier rows: {frontier_rows}")
        print(f"Embeddings ready: {ready_embeddings}/{total_pictures}")
        print(f"Pairs completed (approx): {completed_pairs}/{total_pairs}")
        print(f"Pairs remaining (approx): {remaining_pairs}")
        print(f"Pictures with remaining work: {remaining_a}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show picture likeness frontier progress."
    )
    parser.add_argument("db_path", help="Path to vault.db")
    args = parser.parse_args()
    get_progress(args.db_path)


if __name__ == "__main__":
    main()
