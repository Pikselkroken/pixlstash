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
