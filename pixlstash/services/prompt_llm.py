"""Rewrite a prompt with the LLM a workflow already loads as its text encoder.

Image models such as MiniMax H3 (Qwen3-VL), Z-Image (Qwen3) and FLUX.2
(Mistral) encode their prompt with a chat LLM. ComfyUI's core *Generate Text*
node (``TextGenerateV2``) runs that same encoder as an LLM, so a library with
such a workflow has an LLM without installing one: one loader node, the
generate node and a *Preview as Text* node are submitted to the owner's
ComfyUI, and the preview's text is the answer.
"""

from __future__ import annotations

import secrets
import time
from typing import Callable, Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.services.comfyui_service import (
    _extract_history_entry,
    _extract_history_status_and_error,
    _fetch_comfyui_history,
    _submit_comfyui_prompt,
)

logger = get_logger(__name__)

# The loaders whose CLIP a Generate Text node can run, and the inputs each may
# carry: the graph is the owner's ComfyUI's to run, but it is built here, so
# nothing else from the request reaches it.
LLM_LOADERS = {
    "CLIPLoader": {"clip_name", "type", "device"},
    "CLIPLoaderGGUF": {"clip_name", "type"},
}

EMBELLISH_INSTRUCTION = (
    "You rewrite prompts for a photorealistic image model. Rewrite the "
    "user's prompt as one richer paragraph of at most 150 words: keep every "
    "fact, every place, every count and every 'exactly one' it gives, and add "
    "concrete materials, textures, furnishings, layout and light that fit it. "
    "Add no people and no text on signs unless the prompt asks for them. "
    "Answer with the rewritten prompt only, no preamble, no quotes."
)
MAX_TOKENS = 400
WAIT_SECONDS = 600
POLL_SECONDS = 1.0


class PromptLlmUnavailable(ValueError):
    """The loader given cannot run as an LLM, or ComfyUI made no text."""


def generate_graph(loader: dict, prompt: str, instruction: str, seed: int) -> dict:
    """The three-node graph: the loader, Generate Text, Preview as Text."""
    class_type = loader.get("class_type")
    allowed = LLM_LOADERS.get(class_type)
    if allowed is None:
        raise PromptLlmUnavailable(f"{class_type} does not load an LLM text encoder.")
    inputs = {
        name: value
        for name, value in (loader.get("inputs") or {}).items()
        if name in allowed and isinstance(value, str)
    }
    if not inputs.get("clip_name"):
        raise PromptLlmUnavailable("The text encoder loader names no file.")
    return {
        "1": {"class_type": class_type, "inputs": inputs},
        "2": {
            "class_type": "TextGenerateV2",
            "inputs": {
                "clip": ["1", 0],
                "prompt": prompt,
                "max_length": MAX_TOKENS,
                "sampling_mode": "on",
                "sampling_mode.temperature": 0.7,
                "sampling_mode.top_k": 64,
                "sampling_mode.top_p": 0.95,
                "sampling_mode.min_p": 0.05,
                "sampling_mode.repetition_penalty": 1.05,
                "sampling_mode.presence_penalty": 0.0,
                "sampling_mode.seed": seed,
                "thinking": False,
                "use_default_template": True,
                "system_prompt": instruction,
            },
        },
        "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}},
    }


def preview_text(entry: dict) -> str:
    """The text a finished prompt's *Preview as Text* node showed."""
    for output in (entry.get("outputs") or {}).values():
        text = (output or {}).get("text")
        if isinstance(text, list) and text and isinstance(text[0], str):
            return text[0].strip()
        if isinstance(text, str):
            return text.strip()
    return ""


def embellish(
    base_url: str,
    loader: dict,
    prompt: str,
    prepare: Optional[Callable[[dict], object]] = None,
) -> str:
    """*prompt* rewritten richer by the LLM *loader* loads, on ComfyUI at
    *base_url*. *prepare* changes the graph in place before it is sent: a run
    loads a file this ComfyUI does not list off PixlStash's shelf, and so must
    this. Raises :class:`PromptLlmUnavailable` with the reason."""
    graph = generate_graph(
        loader, prompt, EMBELLISH_INSTRUCTION, secrets.randbelow(2**32)
    )
    if prepare is not None:
        prepare(graph)
    prompt_id = _submit_comfyui_prompt(base_url, graph).get("prompt_id")
    if not prompt_id:
        raise PromptLlmUnavailable("ComfyUI did not take the prompt.")
    # ponytail: polls history once a second while the request waits; the
    # dialog shows a spinner meanwhile. Follow it over the socket if a long
    # queue ever makes the wait a problem.
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        history = _fetch_comfyui_history(base_url, prompt_id)
        status, error = _extract_history_status_and_error(history, prompt_id)
        if error or status in {"error", "interrupted"}:
            logger.warning(
                "Embellishing a prompt failed on %s: %s", loader.get("inputs"), error
            )
            raise PromptLlmUnavailable(error or "The text run stopped.")
        entry = _extract_history_entry(history, prompt_id)
        if status == "success" or entry.get("outputs"):
            text = preview_text(entry)
            if not text:
                raise PromptLlmUnavailable("The LLM wrote nothing.")
            return text
        time.sleep(POLL_SECONDS)
    raise PromptLlmUnavailable("The LLM took too long to answer.")
