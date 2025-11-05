from typing import Optional, List

from .character import CharacterModel
from .database import VaultDatabase


class Characters:
    def __init__(self, db: VaultDatabase):
        self._db = db

    def __getitem__(self, character_id: int) -> CharacterModel:
        row = self._db._execute(
            "SELECT id, name, description FROM characters WHERE id = ?", (character_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Character {character_id} not found")
        return CharacterModel(
            id=row["id"], name=row["name"], description=row["description"]
        )

    def add(self, character: CharacterModel):
        cur = self._db._execute(
            "INSERT INTO characters (name, description) VALUES (?, ?)",
            (character.name, character.description),
            commit=True,
        )
        character.id = cur.lastrowid

    def update(self, character: CharacterModel):
        self._db._execute(
            "UPDATE characters SET name = ?, description = ? WHERE id = ?",
            (character.name, character.description, character.id),
            commit=True,
        )

    def delete(self, character_id: int):
        self._db._execute(
            "DELETE FROM characters WHERE id = ?",
            (character_id,),
            commit=True,
        )

    def list(self) -> List[CharacterModel]:
        rows = self._db._query("SELECT id, name, description FROM characters")
        return [
            CharacterModel(
                id=row["id"], name=row["name"], description=row["description"]
            )
            for row in rows
        ]

    def find(self, name: Optional[str] = None) -> List[CharacterModel]:
        if name:
            rows = self._db._query(
                "SELECT id, name, description FROM characters WHERE name = ?", (name,)
            )
        else:
            rows = self._db._query("SELECT id, name, description FROM characters")
        return [
            CharacterModel(
                id=row["id"], name=row["name"], description=row["description"]
            )
            for row in rows
        ]

    def get_by_id(self, character_id: int) -> Optional[CharacterModel]:
        row = self._db._execute(
            "SELECT id, name, description FROM characters WHERE id = ?", (character_id,)
        ).fetchone()
        if not row:
            return None
        return CharacterModel(
            id=row["id"], name=row["name"], description=row["description"]
        )

    def create_from_dict(self, data: dict) -> CharacterModel:
        character = CharacterModel(
            name=data.get("name"),
            original_seed=data.get("original_seed"),
            original_prompt=data.get("original_prompt"),
            loras=data.get("loras", []),
            description=data.get("description"),
        )
        self.add(character)
        return character
