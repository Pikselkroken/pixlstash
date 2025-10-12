from typing import List, Tuple


class Character:
    """
    Represents a character LoRA description.
    """

    def __init__(
        self,
        trigger_word: str,
        original_seed: int,
        original_prompt: str,
        lora_model_paths: List[Tuple[str, float]],
    ):
        """
        :param trigger_word: Unique ID or keyword for the character
        :param original_seed: Seed used for original generation
        :param original_prompt: Prompt used for original generation
        :param lora_model_paths: List of tuples (model_path, fractional_ranking)
        """
        self.trigger_word = trigger_word
        self.original_seed = original_seed
        self.original_prompt = original_prompt
        self.lora_model_paths = lora_model_paths  # List of (path, ranking)

    def add_lora_model(self, model_path: str, ranking: float):
        self.lora_model_paths.append((model_path, ranking))

    def get_top_lora(self, n: int = 1) -> List[Tuple[str, float]]:
        """
        Returns the top n LoRA models by ranking.
        """
        return sorted(self.lora_model_paths, key=lambda x: x[1], reverse=True)[:n]
