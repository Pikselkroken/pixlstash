from pixelurgy_vault.schema_version import SchemaVersion

from .logging import get_logger


class VaultUpgrade:
    """
    Handles schema upgrades for the Vault database.

    Attributes:
        connection: SQLite connection object.
        schema_version: SchemaVersion instance for managing schema version.
    """

    def __init__(self, connection):
        """
        Initialize a VaultUpgrade instance.

        Args:
            connection: SQLite connection object.
        """
        self.logger = get_logger(__name__)
        self.connection = connection
        self.schema_version = SchemaVersion(connection)
        self.logger.debug(
            f"Current schema version: {self.schema_version.get_version()}"
        )

    def upgrade_if_necessary(self):
        """
        Perform schema upgrade if necessary. Adds is_reference to pictures if missing and bumps schema version.
        """
        cursor = self.connection.cursor()
        # Check if is_reference column exists
        cursor.execute("PRAGMA table_info(pictures)")
        columns = [row[1] for row in cursor.fetchall()]
        if "is_reference" not in columns:
            self.logger.info("Upgrading schema: adding is_reference to pictures table.")
            cursor.execute("ALTER TABLE pictures ADD COLUMN is_reference INTEGER DEFAULT 0 CHECK(is_reference BETWEEN 0 AND 1)")
            cursor.execute("UPDATE pictures SET is_reference = 0 WHERE is_reference IS NULL")
            self.connection.commit()
            # Bump schema version
            new_version = self.schema_version.get_version() + 1
            self.schema_version.set_version(new_version)
            self.logger.info(f"Schema upgraded to version {new_version}")

        # Upgrade picture_iterations table: add character_likeness and character_id if missing
        cursor.execute("PRAGMA table_info(picture_iterations)")
        columns = [row[1] for row in cursor.fetchall()]
        upgraded = False
        if "character_likeness" not in columns:
            self.logger.info("Upgrading schema: adding character_likeness to picture_iterations table.")
            cursor.execute("ALTER TABLE picture_iterations ADD COLUMN character_likeness FLOAT CHECK(character_likeness >= 0.0 AND character_likeness <= 1.0)")
            upgraded = True
        if "character_id" not in columns:
            self.logger.info("Upgrading schema: adding character_id to picture_iterations table.")
            cursor.execute("ALTER TABLE picture_iterations ADD COLUMN character_id TEXT")
            upgraded = True
        if upgraded:
            self.connection.commit()
            new_version = self.schema_version.get_version() + 1
            self.schema_version.set_version(new_version)
            self.logger.info(f"Schema upgraded to version {new_version}")
        else:
            self.logger.info("Vault database is the latest version. No upgrade necessary")
