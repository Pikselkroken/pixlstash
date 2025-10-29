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
        # --- CHARACTER ID INTEGER MIGRATION ---
        # Check if characters.id is not INTEGER PRIMARY KEY
        cursor.execute("PRAGMA table_info(characters)")
        char_cols = {row[1]: row for row in cursor.fetchall()}
        needs_char_id_migration = False
        if "id" in char_cols:
            col = char_cols["id"]
            # If not integer primary key
            if not (col[2].upper() == "INTEGER" and col[5] == 1):
                needs_char_id_migration = True
        if needs_char_id_migration:
            self.logger.info("Upgrading schema: migrating characters.id to INTEGER PRIMARY KEY AUTOINCREMENT and updating foreign keys.")
            # 1. Create new characters table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS characters_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    original_seed INTEGER,
                    original_prompt TEXT,
                    lora_model TEXT,
                    description TEXT
                )
            ''')
            # 2. Copy data, assign new integer ids
            cursor.execute("SELECT * FROM characters")
            old_chars = cursor.fetchall()
            name_to_newid = {}
            for char in old_chars:
                cursor.execute('''
                    INSERT INTO characters_new (name, original_seed, original_prompt, lora_model, description)
                    VALUES (?, ?, ?, ?, ?)
                ''', (char[1], char[2], char[3], char[4], char[5]))
                name_to_newid[char[1]] = cursor.lastrowid
            # 3. Migrate pictures.character_id
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pictures_new (
                    id TEXT PRIMARY KEY,
                    character_id INTEGER,
                    description TEXT,
                    tags TEXT,
                    created_at TEXT,
                    is_reference INTEGER DEFAULT 0 CHECK(is_reference BETWEEN 0 AND 1),
                    embedding BLOB,
                    face_embedding TEXT,
                    face_bbox TEXT,
                    FOREIGN KEY(character_id) REFERENCES characters_new(id)
                )
            ''')
            cursor.execute("SELECT * FROM pictures")
            for pic in cursor.fetchall():
                old_char_id = pic[1]
                new_char_id = name_to_newid.get(old_char_id) if old_char_id else None
                cursor.execute('''
                    INSERT INTO pictures_new (id, character_id, description, tags, created_at, is_reference, embedding, face_embedding, face_bbox)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (pic[0], new_char_id, pic[2], pic[3], pic[4], pic[5], pic[6], pic[7], pic[8] if len(pic) > 8 else None))
            # 4. Migrate picture_iterations.character_id
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS picture_iterations_new (
                    id TEXT PRIMARY KEY,
                    picture_id TEXT NOT NULL,
                    character_id INTEGER,
                    file_path TEXT NOT NULL,
                    format TEXT,
                    width INTEGER,
                    height INTEGER,
                    size_bytes INTEGER,
                    created_at TEXT,
                    is_master INTEGER DEFAULT 0 CHECK(is_master BETWEEN 0 AND 1),
                    derived_from TEXT,
                    transform_metadata TEXT,
                    thumbnail BLOB,
                    quality TEXT,
                    face_quality TEXT,
                    score INTEGER CHECK(score BETWEEN 0 AND 5),
                    character_likeness FLOAT CHECK(character_likeness >= 0.0 AND character_likeness <= 1.0),
                    pixel_sha TEXT,
                    FOREIGN KEY(picture_id) REFERENCES pictures_new(id),
                    FOREIGN KEY(character_id) REFERENCES characters_new(id)
                )
            ''')
            cursor.execute("SELECT * FROM picture_iterations")
            for it in cursor.fetchall():
                old_char_id = it[2]
                new_char_id = name_to_newid.get(old_char_id) if old_char_id else None
                cursor.execute('''
                    INSERT INTO picture_iterations_new (
                        id, picture_id, character_id, file_path, format, width, height, size_bytes, created_at, is_master, derived_from, transform_metadata, thumbnail, quality, face_quality, score, character_likeness, pixel_sha
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (it[0], it[1], new_char_id, it[3], it[4], it[5], it[6], it[7], it[8], it[9], it[10], it[11], it[12], it[13], it[14], it[15], it[16], it[17] if len(it) > 17 else None))
            # 5. Replace old tables
            cursor.execute("DROP TABLE picture_iterations")
            cursor.execute("ALTER TABLE picture_iterations_new RENAME TO picture_iterations")
            cursor.execute("DROP TABLE pictures")
            cursor.execute("ALTER TABLE pictures_new RENAME TO pictures")
            cursor.execute("DROP TABLE characters")
            cursor.execute("ALTER TABLE characters_new RENAME TO characters")
            self.connection.commit()
            new_version = self.schema_version.get_version() + 1
            self.schema_version.set_version(new_version)
            self.logger.info(f"Schema upgraded to version {new_version} (character id migration)")

        cursor.execute("PRAGMA table_info(pictures)")
        columns = [row[1] for row in cursor.fetchall()]
        upgraded = False
        if "is_reference" not in columns:
            self.logger.info("Upgrading schema: adding is_reference to pictures table.")
            cursor.execute(
                "ALTER TABLE pictures ADD COLUMN is_reference INTEGER DEFAULT 0 CHECK(is_reference BETWEEN 0 AND 1)"
            )
            cursor.execute(
                "UPDATE pictures SET is_reference = 0 WHERE is_reference IS NULL"
            )
            upgraded = True
        if "face_embedding" not in columns:
            self.logger.info(
                "Upgrading schema: adding face_embedding to pictures table."
            )
            cursor.execute("ALTER TABLE pictures ADD COLUMN face_embedding TEXT")
            upgraded = True
        if "face_bbox" not in columns:
            self.logger.info("Upgrading schema: adding face_bbox to pictures table.")
            cursor.execute("ALTER TABLE pictures ADD COLUMN face_bbox TEXT")
            upgraded = True
        if upgraded:
            self.connection.commit()
            new_version = self.schema_version.get_version() + 1
            self.schema_version.set_version(new_version)
            self.logger.info(f"Schema upgraded to version {new_version}")

        # Upgrade picture_iterations table: add character_likeness, character_id, and face_quality if missing
        cursor.execute("PRAGMA table_info(picture_iterations)")
        columns = [row[1] for row in cursor.fetchall()]
        upgraded = False
        if "character_likeness" not in columns:
            self.logger.info(
                "Upgrading schema: adding character_likeness to picture_iterations table."
            )
            cursor.execute(
                "ALTER TABLE picture_iterations ADD COLUMN character_likeness FLOAT CHECK(character_likeness >= 0.0 AND character_likeness <= 1.0)"
            )
            upgraded = True
        if "character_id" not in columns:
            self.logger.info(
                "Upgrading schema: adding character_id to picture_iterations table."
            )
            cursor.execute(
                "ALTER TABLE picture_iterations ADD COLUMN character_id TEXT"
            )
            upgraded = True
        if "face_quality" not in columns:
            self.logger.info(
                "Upgrading schema: adding face_quality to picture_iterations table."
            )
            cursor.execute(
                "ALTER TABLE picture_iterations ADD COLUMN face_quality TEXT"
            )
            upgraded = True
        if upgraded:
            self.connection.commit()
            new_version = self.schema_version.get_version() + 1
            self.schema_version.set_version(new_version)
            self.logger.info(f"Schema upgraded to version {new_version}")
        else:
            self.logger.info(
                "Vault database is the latest version. No upgrade necessary"
            )
