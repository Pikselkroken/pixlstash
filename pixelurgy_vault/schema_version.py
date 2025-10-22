class SchemaVersion:
    """
    Manages the schema version for the vault database.
    """

    def __init__(self, connection):
        self.connection = connection
        self._ensure_table()

    def _ensure_table(self):
        cursor = self.connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
            """
        )
        cursor.execute("SELECT COUNT(*) as count FROM schema_version")
        if cursor.fetchone()["count"] == 0:
            cursor.execute("INSERT INTO schema_version (version) VALUES (1)")
        self.connection.commit()

    def get_version(self) -> int:
        cursor = self.connection.cursor()
        cursor.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        return row["version"] if row else 1

    def set_version(self, version: int):
        cursor = self.connection.cursor()
        cursor.execute("UPDATE schema_version SET version = ?", (version,))
        self.connection.commit()
