# Script to clear all tags in the pictures table
import sqlite3
import sys


def clear_all_tags(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE pictures SET tags = NULL, embedding = NULL")
        conn.commit()
        print("All tags cleared.")
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <vault.db path>")
        sys.exit(1)
    clear_all_tags(sys.argv[1])
