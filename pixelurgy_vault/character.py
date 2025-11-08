from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Self


# Dataclass version for schema and CRUD
@dataclass
class CharacterModel:
    __tablename__ = "characters"
    id: int = field(default=None, metadata={"primary_key": True})
    name: str = field(default=None, metadata={"include_in_embedding": True})
    original_seed: int = field(default=None)
    original_prompt: str = field(default=None, metadata={"include_in_embedding": True})
    loras: list = field(default_factory=list)
    description: str = field(default=None, metadata={"include_in_embedding": True})


class Character:
    """
    Represents a character/person real or fictional.

    Attributes:
        id (int): Unique integer identifier for the character (autoincremented by DB).
        name (str): Unique name or keyword for the character.
        original_seed (int): Seed used for original generation.
        original_prompt (str): Prompt used for original generation.
        loras (List[Tuple[str, float]]): List of tuples (model_name, fractional_ranking).
        description (str): Description of the character.
    """

    def __init__(
        self,
        id: Optional[int] = None,
        name: Optional[str] = None,
        original_seed: Optional[int] = None,
        original_prompt: Optional[str] = None,
        loras: Optional[List[Tuple[str, float]]] = None,
        description: Optional[str] = None,
    ):
        """
        Initialize a Character instance.

        Args:
            id (Optional[int]): Unique integer identifier for the character (autoincremented by DB).
            name (Optional[str]): Unique name or keyword for the character.
            original_seed (Optional[int]): Seed used for original generation.
            original_prompt (Optional[str]): Prompt used for original generation.
            loras (Optional[List[Tuple[str, float]]]): List of tuples (model_name, fractional_ranking).
            description (Optional[str]): Description of the character.
        """
        self.id = id
        self.name = name
        self.original_seed = original_seed
        self.original_prompt = original_prompt
        self.loras = loras  # List of (name, ranking)
        self.description = description

    def to_dict(self) -> dict:
        """Convert Character instance to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "original_seed": self.original_seed,
            "original_prompt": self.original_prompt,
            "loras": self.loras,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, dict: dict) -> Self:
        return cls(
            id=dict["id"],
            name=dict["name"],
            original_seed=dict.get("original_seed"),
            original_prompt=dict.get("original_prompt"),
            loras=dict.get("loras"),
            description=dict.get("description"),
        )
