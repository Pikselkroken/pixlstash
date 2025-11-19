from pixlvault.schema_version import SchemaVersion

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
        self.logger.info(f"Current schema version: {self.schema_version.get_version()}")

    def upgrade_if_necessary(self):
        """
        Perform schema upgrade if necessary. Bumps schema version.
        """
        current_version = self.schema_version.get_version()

        # Version 2: Add picture_sets and picture_set_members tables
        if current_version < 2:
            self.logger.info("Upgrading database schema to version 2 (picture_sets)...")
            self._upgrade_to_v2()
            self.schema_version.set_version(2)
            self.logger.info("Database schema upgraded to version 2")

        # Ensure every character has a reference picture set
        self._ensure_reference_picture_sets()
        # Version 3: Add reference_picture_likeness table
        if current_version < 3:
            self.logger.info(
                "Upgrading database schema to version 3 (reference_picture_likeness)..."
            )
            self._upgrade_to_v3()
            self.schema_version.set_version(3)
            self.logger.info("Database schema upgraded to version 3")

        # Version 4: Add sharpness, edge_density, noise_level columns to pictures
        if current_version < 4:
            self.logger.info(
                "Upgrading database schema to version 4 (quality columns)..."
            )
            self._upgrade_to_v4()
            self.schema_version.set_version(4)
            self.logger.info("Database schema upgraded to version 4")

        # Version 5: Add picture_likeness table
        if current_version < 5:
            self.logger.info(
                "Upgrading database schema to version 5 (picture_likeness)..."
            )
            self._upgrade_to_v5()
            self.schema_version.set_version(5)
            self.logger.info("Database schema upgraded to version 5")
            # Add partial index for facial_features == ''
            cursor = self.connection.cursor()
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pictures_facial_features_empty ON pictures(id) WHERE facial_features = '';
                """
            )
        if current_version < 6:
            self.logger.info(
                "Upgrading database schema to version 6 (likeness_work_queue)..."
            )
            self._upgrade_to_v6()
            self.schema_version.set_version(6)
            self.logger.info("Database schema upgraded to version 6")

    def _ensure_reference_picture_sets(self):
        self.logger.info("Ensuring reference picture sets for all characters...")
        cursor = self.connection.cursor()
        # Get all characters
        cursor.execute("SELECT id FROM characters")
        character_ids = [row[0] for row in cursor.fetchall()]
        for char_id in character_ids:
            # Check if reference picture set exists for this character
            cursor.execute(
                "SELECT id FROM picture_sets WHERE name = ? AND description = ?",
                ("reference_pictures", str(char_id)),
            )
            result = cursor.fetchone()
            if not result:
                # Create reference picture set for this character
                cursor.execute(
                    "INSERT INTO picture_sets (name, description) VALUES (?, ?)",
                    ("reference_pictures", str(char_id)),
                )
                self.logger.info(
                    f"Created reference picture set for character id={char_id}"
                )
        self.connection.commit()

    def _upgrade_to_v2(self):
        """Add picture_sets and picture_set_members tables."""
        self.logger.info("Creating picture_sets table...")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS picture_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                description TEXT
            )
        """)

        self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_picture_sets_name ON picture_sets(name)
        """)

        self.logger.info("Creating picture_set_members table...")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS picture_set_members (
                set_id INTEGER NOT NULL,
                picture_id TEXT NOT NULL,
                PRIMARY KEY (set_id, picture_id),
                FOREIGN KEY (set_id) REFERENCES picture_sets(id),
                FOREIGN KEY (picture_id) REFERENCES pictures(id)
            )
        """)

        self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_picture_set_members_set_id ON picture_set_members(set_id)
        """)

        self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_picture_set_members_picture_id ON picture_set_members(picture_id)
        """)

        self.connection.commit()
        self.logger.info("Picture sets tables created successfully")

    def _upgrade_to_v3(self):
        """Add reference_picture_likeness table for likeness scores."""
        self.logger.info("Creating reference_picture_likeness table...")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS reference_picture_likeness (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reference_picture_id TEXT NOT NULL,
                picture_id TEXT NOT NULL,
                face_index INTEGER,
                likeness REAL,
                metric TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(reference_picture_id, picture_id, face_index, metric),
                FOREIGN KEY (reference_picture_id) REFERENCES pictures(id),
                FOREIGN KEY (picture_id) REFERENCES pictures(id)
            )
        """)
        self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_reference_picture_likeness_reference_picture_id ON reference_picture_likeness(reference_picture_id)
        """)
        self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_reference_picture_likeness_picture_id ON reference_picture_likeness(picture_id)
        """)
        self.connection.commit()
        self.logger.info("reference_picture_likeness table created successfully")

    def _upgrade_to_v4(self):
        # Add partial index for face_bbox IS NOT NULL AND face_bbox != ''
        self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_pictures_face_bbox_not_null
            ON pictures(id)
            WHERE face_bbox IS NOT NULL AND face_bbox != '';
        """)
        cursor = self.connection.cursor()
        # Add columns if they don't exist
        columns_to_add = [
            "sharpness",
            "edge_density",
            "contrast",
            "brightness",
            "noise_level",
            "face_sharpness",
            "face_edge_density",
            "face_contrast",
            "face_brightness",
            "face_noise_level",
        ]
        cursor.execute("PRAGMA table_info(pictures)")
        columns = [row[1] for row in cursor.fetchall()]
        for col in columns_to_add:
            if col not in columns:
                cursor.execute(f"ALTER TABLE pictures ADD COLUMN {col} REAL")
                self.logger.info(f"Added column {col} to pictures table.")
        # Add full indexes for all quality measures
        for col in columns_to_add:
            self.connection.execute(
                f"CREATE INDEX IF NOT EXISTS idx_pictures_{col} ON pictures({col})"
            )
        self.connection.commit()

    def _upgrade_to_v5(self):
        """Add picture_likeness table for pairwise likeness scores."""
        self.logger.info("Creating picture_likeness table...")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS picture_likeness (
                picture_id_a TEXT NOT NULL,
                picture_id_b TEXT NOT NULL,
                likeness REAL,
                metric TEXT,
                UNIQUE (picture_id_a, picture_id_b)
            )
        """)
        self.connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_picture_likeness_pair ON picture_likeness(picture_id_a, picture_id_b)
        """)
        # Add partial index for facial_features IS NOT NULL
        self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_pictures_facial_features_not_null
            ON pictures(id)
            WHERE facial_features IS NOT NULL;
        """)
        self.connection.commit()
        self.logger.info("picture_likeness table created successfully")

    def _upgrade_to_v6(self):
        self.logger.info("Creating likeness_work_queue table...")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS likeness_work_queue (
                picture_id_a TEXT NOT NULL,
                picture_id_b TEXT NOT NULL,
                UNIQUE (picture_id_a, picture_id_b)
            )
        """)
        self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_lwq_pair ON likeness_work_queue(picture_id_a, picture_id_b)
        """)
        self.connection.commit()
        self.logger.info("likeness_work_queue table created successfully")

        # Populate likeness_work_queue for all existing valid pairs
        self.logger.info(
            "Populating likeness_work_queue for all existing picture pairs..."
        )
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO likeness_work_queue (picture_id_a, picture_id_b)
            SELECT a.id, b.id
            FROM pictures a
            JOIN pictures b ON a.id != b.id
            WHERE a.facial_features IS NOT NULL
              AND b.facial_features IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM picture_likeness pl
                WHERE pl.picture_id_a = a.id AND pl.picture_id_b = b.id
              )
        """)
        self.connection.commit()
        self.logger.info("likeness_work_queue populated for all existing pairs.")
