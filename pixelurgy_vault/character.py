import uuid
from typing import List, Optional, Tuple


class Character:
    """
    Represents a character LoRA description.
    """

    def __init__(
        self,
        id: Optional[str] = None,
        name: Optional[str] = None,
        original_seed: Optional[int] = None,
        original_prompt: Optional[str] = None,
        lora_model: Optional[List[Tuple[str, float]]] = None,
        description: Optional[str] = None,
    ):
        """
        :param id: Unique identifier for the character
        :param name: Unique name or keyword for the character
        :param original_seed: Seed used for original generation
        :param original_prompt: Prompt used for original generation
        :param lora_model_name: List of tuples (model_name, fractional_ranking)
        """
        self.id = id if id else uuid.uuid4().hex
        self.name = name
        self.original_seed = original_seed
        self.original_prompt = original_prompt
        self.lora_model = lora_model  # List of (name, ranking)
        self.description = description

    def add_lora_model(self, model_name: str, ranking: float):
        self.lora_model.append((model_name, ranking))

    def get_top_lora(self, n: int = 1) -> List[Tuple[str, float]]:
        """
        Returns the top n LoRA models by ranking.
        """
        return sorted(self.lora_model, key=lambda x: x[1], reverse=True)[:n]
