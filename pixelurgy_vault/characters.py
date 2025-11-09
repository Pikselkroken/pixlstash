import json

from typing import Optional, List, Union

from .character import CharacterModel
from .database import VaultDatabase

import logging

logger = logging.getLogger(__name__)


class Characters:
    def __init__(self, db: VaultDatabase):
        self._db = db

    def __getitem__(self, character_id: int) -> CharacterModel:
        row = self._db.execute(
            "SELECT id, name, original_seed, original_prompt, loras, description FROM characters WHERE id = ?",
            (character_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Character {character_id} not found")

        logger.debug(f"Fetched character row for ID {character_id}: {row}")
        model = self._row_to_model(row)
        logger.debug(f"Converted to CharacterModel: id={model.id}, name={model.name}")
        return model

    def add(self, characters: Union[CharacterModel, List[CharacterModel]]):
        """Add one or more characters. Supports both single character and batch operations."""
        if not isinstance(characters, list):
            characters = [characters]

        sql = "INSERT INTO characters (name, original_seed, original_prompt, loras, description) VALUES (?, ?, ?, ?, ?)"
        params_list = []
        for character in characters:
            params = (
                character.name,
                character.original_seed,
                character.original_prompt,
                json.dumps(character.loras)
                if character.loras is not None
                else json.dumps([]),
                character.description,
            )
            params_list.append(params)

        if len(params_list) == 1:
            logger.info(f"INSERT INTO characters SQL: {sql} | params: {params_list[0]}")
            cur = self._db.execute(sql, params_list[0], commit=True)
            characters[0].id = cur.lastrowid
        else:
            self._db.executemany(sql, params_list, commit=True)

    def update(self, characters: Union[CharacterModel, List[CharacterModel]]):
        """Update one or more characters. Supports both single character and batch operations."""
        if not isinstance(characters, list):
            characters = [characters]

        sql = "UPDATE characters SET name = ?, original_seed = ?, original_prompt = ?, loras = ?, description = ? WHERE id = ?"
        params_list = []
        for character in characters:
            params = (
                character.name,
                character.original_seed,
                character.original_prompt,
                json.dumps(character.loras)
                if character.loras is not None
                else json.dumps([]),
                character.description,
                character.id,
            )
            params_list.append(params)

        if len(params_list) == 1:
            self._db.execute(sql, params_list[0], commit=True)
        else:
            self._db.executemany(sql, params_list, commit=True)

    def delete(self, character_ids: Union[int, List[int]]):
        """Delete one or more characters. Supports both single ID and batch operations."""
        if not isinstance(character_ids, list):
            character_ids = [character_ids]

        self._db.executemany(
            "DELETE FROM characters WHERE id = ?",
            [(cid,) for cid in character_ids],
            commit=True,
        )

    def find(self, name: Optional[str] = None) -> List[CharacterModel]:
        if name:
            rows = self._db.query(
                "SELECT id, name, original_seed, original_prompt, loras, description FROM characters WHERE name = ?",
                (name,),
            )
        else:
            rows = self._db.query(
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
