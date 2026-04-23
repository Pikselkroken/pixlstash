"""Remove picture rows whose reference folder no longer exists.

When a reference folder is deleted PixlStash normally removes the associated
picture rows too.  If folders were removed by an older version or directly from
the database those rows can be left behind with a reference_folder_id that
points to a non-existent folder.  This script finds and removes them.

Usage:
    python scripts/cleanup_orphaned_reference_pictures.py [path/to/vault.db]

The default db path is vault.db in the current working directory.
"""

import os
import sqlite3
import sys


def cleanup(db_path: str, dry_run: bool = False) -> None:
    if not os.path.exists(db_path):
        print(f"Error: database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # Find pictures whose reference_folder_id has no matching folder row.
        cur.execute(
            """
            SELECT p.id, p.file_path, p.reference_folder_id
            FROM picture AS p
            WHERE p.reference_folder_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM reference_folder rf
                  WHERE rf.id = p.reference_folder_id
              )
            """
        )
        orphans = cur.fetchall()

        if not orphans:
            print("No orphaned reference-folder pictures found.")
            return

        print(f"Found {len(orphans)} orphaned picture row(s):")
        for pic_id, file_path, rf_id in orphans:
            print(
                f"  picture id={pic_id}  reference_folder_id={rf_id}  path={file_path}"
            )

        if dry_run:
            print("\nDry-run mode — nothing was deleted.")
            return

        orphan_ids = [row[0] for row in orphans]
        # Delete associated tags first (no cascade in SQLite by default).
        cur.execute(
            f"DELETE FROM tag WHERE picture_id IN ({','.join('?' * len(orphan_ids))})",
            orphan_ids,
        )
        cur.execute(
            f"DELETE FROM picture WHERE id IN ({','.join('?' * len(orphan_ids))})",
            orphan_ids,
        )
        conn.commit()
        print(f"\nDeleted {len(orphan_ids)} picture row(s) and their tags.")
    finally:
        conn.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    db_path = args[0] if args else "vault.db"
    cleanup(db_path, dry_run=dry_run)
