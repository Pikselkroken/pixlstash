from pixelurgy_vault.schema_version import SchemaVersion

from .logging import get_logger

class VaultUpgrade:
    """
    Handles schema upgrades for the Vault database.
    """
    def __init__(self, connection):
        self.logger = get_logger(__name__)

        self.connection = connection
        self.schema_version = SchemaVersion(connection)

        self.logger.debug(f"Current schema version: {self.schema_version.get_version()}")

    def upgrade_if_necessary(self):
        # For now, just log that no upgrade is needed
        self.logger.info("Vault database is the latest version. No upgrade necessary")
