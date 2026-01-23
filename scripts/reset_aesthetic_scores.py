import sqlite3
import sys
import os

def reset_aesthetic_scores(db_path):
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    try:
        # Check table name
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='picture'")
        if cursor.fetchone():
            table = 'picture'
        else:
            table = 'pictures'
            
        print(f"Resetting aesthetic_score in table '{table}'...")
        cursor.execute(f"UPDATE {table} SET aesthetic_score = NULL")
        changes = conn.total_changes
        conn.commit()
        print(f"Done. Reset {changes} rows.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    db_path = "vault.db"
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    reset_aesthetic_scores(db_path)
