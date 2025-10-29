import sqlite3
from typing import Optional, List
from dataclasses import dataclass


@dataclass
class Character:
    id: Optional[int]
    name: str
    description: Optional[str] = None


class Characters:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def __getitem__(self, character_id: int) -> Character:
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT id, name, description FROM characters WHERE id = ?", (character_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"Character {character_id} not found")
        return Character(id=row["id"], name=row["name"], description=row["description"])

    def add(self, character: Character):
        cursor = self.connection.cursor()
        cursor.execute(
            "INSERT INTO characters (name, description) VALUES (?, ?)",
            (character.name, character.description),
        )
        character.id = cursor.lastrowid
        self.connection.commit()

    def update(self, character: Character):
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE characters SET name = ?, description = ? WHERE id = ?",
            (character.name, character.description, character.id),
        )
        self.connection.commit()

    def delete(self, character_id: int):
        cursor = self.connection.cursor()
        cursor.execute(
            "DELETE FROM characters WHERE id = ?",
            (character_id,),
        )
        self.connection.commit()

    def list(self) -> List[Character]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT id, name, description FROM characters")
        rows = cursor.fetchall()
        return [
            Character(id=row["id"], name=row["name"], description=row["description"])
            for row in rows
        ]

    def find(self, name: Optional[str] = None) -> List[Character]:
        cursor = self.connection.cursor()
        if name:
            cursor.execute(
                "SELECT id, name, description FROM characters WHERE name = ?",
                (name,),
            )
        else:
            cursor.execute("SELECT id, name, description FROM characters")
        rows = cursor.fetchall()
        return [
            Character(id=row["id"], name=row["name"], description=row["description"])
            for row in rows
        ]
