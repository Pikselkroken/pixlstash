import json

from typing import Optional, List

from .character import CharacterModel
from .database import VaultDatabase

import logging

logger = logging.getLogger(__name__)


class Characters:
    def __init__(self, db: VaultDatabase):
        self._db = db

    def __getitem__(self, character_id: int) -> CharacterModel:
        row = self._db._execute(
            "SELECT id, name, original_seed, original_prompt, loras, description FROM characters WHERE id = ?",
            (character_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Character {character_id} not found")

        return self._row_to_model(row)

    def add(self, character: CharacterModel):
        sql = "INSERT INTO characters (name, original_seed, original_prompt, loras, description) VALUES (?, ?, ?, ?, ?)"
        params = (
            character.name,
            character.original_seed,
            character.original_prompt,
            json.dumps(character.loras)
            if character.loras is not None
            else json.dumps([]),
            character.description,
        )
        logger.info(f"INSERT INTO characters SQL: {sql} | params: {params}")
        cur = self._db._execute(
            sql,
            params,
            commit=True,
        )
        character.id = cur.lastrowid

    def update(self, character: CharacterModel):
        self._db._execute(
            "UPDATE characters SET name = ?, original_seed = ?, original_prompt = ?, loras = ?, description = ? WHERE id = ?",
            (
                character.name,
                character.original_seed,
                character.original_prompt,
                json.dumps(character.loras)
                if character.loras is not None
                else json.dumps([]),
                character.description,
                character.id,
            ),
            commit=True,
        )

    def delete(self, character_id: int):
        self._db._execute(
            "DELETE FROM characters WHERE id = ?",
            (character_id,),
            commit=True,
        )

    def find(self, name: Optional[str] = None) -> List[CharacterModel]:
        if name:
            rows = self._db._query(
                "SELECT id, name, original_seed, original_prompt, loras, description FROM characters WHERE name = ?",
                (name,),
            )
        else:
            rows = self._db._query(
                "SELECT id, name, original_seed, original_prompt, loras, description FROM characters"
            )
        result = []
        for row in rows:
            result.append(self._row_to_model(row))
        return result

    def _row_to_model(self, row) -> CharacterModel:
        loras = row["loras"]
        if loras is not None and isinstance(loras, str):
            loras = json.loads(loras)
        return CharacterModel(
            id=row["id"],
            name=row["name"],
            original_seed=row["original_seed"],
            original_prompt=row["original_prompt"],
            loras=loras if loras is not None else [],
            description=row["description"],
        )
