import uuid
from typing import List, Optional, Tuple


class Character:
    """
    Represents a character LoRA description.

    Attributes:
        id (int): Unique integer identifier for the character (autoincremented by DB).
        name (str): Unique name or keyword for the character.
        original_seed (int): Seed used for original generation.
        original_prompt (str): Prompt used for original generation.
        lora_model (List[Tuple[str, float]]): List of tuples (model_name, fractional_ranking).
        description (str): Description of the character.
    """

    def __init__(
        self,
        id: Optional[int] = None,
        name: Optional[str] = None,
        original_seed: Optional[int] = None,
        original_prompt: Optional[str] = None,
        lora_model: Optional[List[Tuple[str, float]]] = None,
        description: Optional[str] = None,
    ):
        """
        Initialize a Character instance.

        Args:
            id (Optional[int]): Unique integer identifier for the character (autoincremented by DB).
            name (Optional[str]): Unique name or keyword for the character.
            original_seed (Optional[int]): Seed used for original generation.
            original_prompt (Optional[str]): Prompt used for original generation.
            lora_model (Optional[List[Tuple[str, float]]]): List of tuples (model_name, fractional_ranking).
            description (Optional[str]): Description of the character.
        """
        self.id = id
        self.name = name
        self.original_seed = original_seed
        self.original_prompt = original_prompt
        self.lora_model = lora_model  # List of (name, ranking)
        self.description = description

    def add_lora_model(self, model_name: str, ranking: float):
        """
        Add a LoRA model to the character.

        Args:
            model_name (str): Name of the LoRA model.
            ranking (float): Fractional ranking for the model.
        """
        self.lora_model.append((model_name, ranking))
