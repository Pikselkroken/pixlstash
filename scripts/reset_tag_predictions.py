import argparse
import os
import sqlite3


def reset_tag_predictions(db_path: str) -> None:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM tag_prediction")
        count = cursor.fetchone()[0]
        print(f"Current tag predictions: {count}")

        cursor.execute("DELETE FROM tag_prediction")
        conn.commit()
        print(
            "tag_prediction table cleared. TagPredictionTask will regenerate predictions on next run."
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clear all tag predictions so TagPredictionTask will regenerate them."
    )
    parser.add_argument("db_path", help="Path to vault.db")
    args = parser.parse_args()
    reset_tag_predictions(args.db_path)


if __name__ == "__main__":
    main()
