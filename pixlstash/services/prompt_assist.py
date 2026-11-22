"""The prompt assistant ("Photo look"): curated prompt text after the owner's words.

Presets are JSON files in ``pixlstash/data/prompt-presets/``: one per model
family that needs its own (``<family>.json``), and :data:`GENERIC`
(``_default.json``) for every other graph, an unknown or mixed family
included. A file says, per context (``photo`` for an ordinary run, ``sheet``
for a character sheet), whether a surface starts with the assist on
(``default_on``) and its categories in the order their text follows the
owner's words, each with its options and its default. An option may carry a
``finish``: a closing sentence added after every category's text, so the
camera choice decides how the picture reads as a whole. A run names its
choices in ``prompt_assist``; :func:`assemble` turns them into the parts it
adds, and :func:`~pixlstash.services.workflow_run_service.apply_prompts`, the
one writer, puts them after the owner's words and stamps them on the node
holding the literal (:data:`~pixlstash.utils.comfyui_utilities.PROMPT_ASSIST_META`).
Backend §25.31.

Ids are for good: an option id never names a different look, and retiring one
removes it. Adding an option is a data change. Raising ``version`` is how a
tuned text or a moved default is told apart in a picture's stamp. A default is
``none`` or an option whose ``evidence`` is ``tested``.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.known_base_models import FAMILY_LABELS

logger = get_logger(__name__)

PRESET_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "prompt-presets")
)
#: The presets every graph without a family file of its own gets.
GENERIC = "_default"
CONTEXTS = ("photo", "sheet")
EVIDENCE = ("tested", "official", "community", "untested")
#: A choice that adds nothing for its category; also a legal default.
NONE = "none"
MAX_TEXT_LENGTH = 400
#: Between the owner's words and the first part, and between parts.
JOINER = " "
STAMP_VERSION = 1

_ID_RE = re.compile(r"^[a-z0-9_]+$")
_SENTENCE_END = (".", "!", "?")
_OPTION_KEYS = {"id", "label", "text", "finish", "evidence", "note"}
_CATEGORY_KEYS = {"id", "label", "default", "options"}
# Render vocabulary: words that describe no thing a camera records and push a
# model toward the glossy render look the assistant exists to remove. Never in
# a preset; flagged, never refused, in text an owner types.
_RENDER_WORDS_RE = re.compile(
    r"\b(photo[- ]?realistic|hyper[- ]?realistic|ultra[- ]?realistic|8k|"
    r"masterpiece|best quality|ultra[- ]?detailed|octane render|unreal engine|"
    r"3d render|cgi)\b",
    re.IGNORECASE,
)


class PromptAssistError(ValueError):
    """A request's choice the presets do not have; *field* names it."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def render_words(text: str) -> list[str]:
    """The render vocabulary in *text*, lower-cased, sorted, each once."""
    return sorted({match.lower() for match in _RENDER_WORDS_RE.findall(text)})


def as_sentence(text: str) -> str:
    """*text* trimmed, its runs of space collapsed, closed as a sentence.

    What typed text gets so it joins the paragraph as a sentence of its own;
    a preset text must already be in this form.
    """
    text = " ".join(text.split())
    return text if not text or text.endswith(_SENTENCE_END) else f"{text}."


def is_layout_prompt(prompt: Optional[str]) -> bool:
    """Whether *prompt* is a JSON document (a MOSS or regions sheet layout),
    which prose appended to would break."""
    text = (prompt or "").strip()
    if not text.startswith(("{", "[")):
        return False
    try:
        return isinstance(json.loads(text), (dict, list))
    except ValueError:
        return False


def validate(document: dict, family: str) -> dict:
    """*document* when it is a valid presets file named *family*, else ``ValueError``."""
    problems: list[str] = []
    if document.get("family") != family or family not in {*FAMILY_LABELS, GENERIC}:
        problems.append(f"family {document.get('family')!r} is not {family!r}")
    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        problems.append(f"version {version!r} is not a positive integer")
    contexts = document.get("contexts")
    if not isinstance(contexts, dict) or not contexts or set(contexts) - set(CONTEXTS):
        problems.append(f"contexts must be a non-empty subset of {CONTEXTS}")
        contexts = {}
    for context, body in contexts.items():
        if not isinstance((body or {}).get("default_on"), bool):
            problems.append(f"{context}: default_on is not true or false")
        categories = (body or {}).get("categories")
        if not isinstance(categories, list) or not categories:
            problems.append(f"{context}: no categories")
            continue
        seen: set[str] = set()
        for category in categories:
            where = f"{context}.{category.get('id')}"
            problems += _id_problems(category.get("id"), seen, where)
            if set(category) - _CATEGORY_KEYS:
                problems.append(
                    f"{where}: unknown keys {set(category) - _CATEGORY_KEYS}"
                )
            if not isinstance(category.get("label"), str) or not category["label"]:
                problems.append(f"{where}: no label")
            options = category.get("options")
            if not isinstance(options, list):
                problems.append(f"{where}: options is not a list")
                continue
            option_ids: set[str] = set()
            for option in options:
                if not isinstance(option, dict):
                    problems.append(f"{where}: an option is not an object")
                    continue
                problems += _option_problems(option, option_ids, where)
            default = category.get("default")
            tested = {
                o.get("id")
                for o in options
                if isinstance(o, dict) and o.get("evidence") == "tested"
            }
            if default != NONE and default not in tested:
                problems.append(
                    f"{where}: default {default!r} is neither none nor a tested option"
                )
    if problems:
        raise ValueError(f"prompt presets {family}: " + "; ".join(problems))
    return document


def _id_problems(value, seen: set[str], where: str) -> list[str]:
    if not isinstance(value, str) or not _ID_RE.match(value) or value == NONE:
        return [f"{where}: id {value!r} is not [a-z0-9_]+ or is reserved"]
    if value in seen:
        return [f"{where}: id {value!r} is used twice"]
    seen.add(value)
    return []


def _option_problems(option: dict, seen: set[str], where: str) -> list[str]:
    where = f"{where}.{option.get('id')}"
    problems = _id_problems(option.get("id"), seen, where)
    if set(option) - _OPTION_KEYS:
        problems.append(f"{where}: unknown keys {set(option) - _OPTION_KEYS}")
    if not isinstance(option.get("label"), str) or not option["label"]:
        problems.append(f"{where}: no label")
    if option.get("evidence") not in EVIDENCE:
        problems.append(f"{where}: evidence {option.get('evidence')!r}")
    if "text" not in option and "finish" not in option:
        problems.append(f"{where}: neither text nor finish")
    for key in ("text", "finish"):
        text = option.get(key, "")
        if key not in option:
            continue
        if not isinstance(text, str) or not text or as_sentence(text) != text:
            problems.append(f"{where}: {key} is not one or more full sentences")
        elif len(text) > MAX_TEXT_LENGTH:
            problems.append(f"{where}: {key} is over {MAX_TEXT_LENGTH} characters")
        elif render_words(text):
            problems.append(f"{where}: render vocabulary {render_words(text)}")
    return problems


@lru_cache(maxsize=None)
def _load(name: str) -> Optional[dict]:
    """The validated presets file *name*, ``None`` where there is none.

    A shipped file that does not validate raises: it is a packaging bug the
    gated test exists to catch, not a state to run around.
    """
    path = os.path.join(PRESET_DIR, f"{name}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return validate(json.load(handle), name)


def presets(family: Optional[str]) -> dict:
    """The presets a graph of *family* gets: its own file, else :data:`GENERIC`."""
    own = _load(family) if family and _ID_RE.match(family) else None
    return own or _load(GENERIC)


def catalogue(family: Optional[str], context: str) -> Optional[dict]:
    """What a surface offers for *family* in *context*, ``None`` for nothing.

    ``{presets, preset_version, default_on, categories: [{id, label, default,
    options: [{id, label, text, finish}]}]}``: ``presets`` names the file
    (the family's, or ``_default``); ``text`` and ``finish`` are null where
    an option has none; evidence and notes stay in the file.
    """
    document = presets(family)
    body = document["contexts"].get(context)
    if body is None:
        return None
    return {
        "presets": document["family"],
        "preset_version": document["version"],
        "default_on": body["default_on"],
        "categories": [
            {
                "id": category["id"],
                "label": category["label"],
                "default": category["default"],
                "options": [
                    {
                        "id": option["id"],
                        "label": option["label"],
                        "text": option.get("text"),
                        "finish": option.get("finish"),
                    }
                    for option in category["options"]
                ],
            }
            for category in body["categories"]
        ],
    }


def assemble(family: Optional[str], context: str, choices: dict, prompt: str) -> dict:
    """The stamp for what a run adds after *prompt*: ``{v, family, presets,
    context, preset_version, joiner, parts: [{category, kind, id, text}]}``.

    *choices* maps a category to ``"none"``, ``{"id": option}`` or ``{"text":
    typed}``; a category left out takes its shipped default, so ``{}`` is the
    defaults. Each choice is a ``kind: "choice"`` part in the file's category
    order; a chosen option's ``finish`` follows every choice as a ``kind:
    "finish"`` part of its category. Typed text is used as given, trimmed and
    closed as a sentence (:func:`as_sentence`), with ``id`` null and no
    finish; empty adds nothing. A part whose text *prompt* already holds is
    left out, so a prompt that lost its stamp and came back whole is not
    doubled. An unknown category or option is a :class:`PromptAssistError`.
    """
    document = presets(family)
    categories = document["contexts"][context]["categories"]
    known = {category["id"] for category in categories}
    for name in choices:
        if name not in known:
            raise PromptAssistError(
                f"prompt_assist.choices.{name}",
                f"The {context} presets have no {name!r} category.",
            )
    picked, finishes = [], []
    for category in categories:
        choice = choices.get(category["id"], category["default"])
        if choice == NONE:
            continue
        if isinstance(choice, str):
            choice = {"id": choice}
        part = {"category": category["id"], "kind": "choice"}
        if choice.get("id") is not None:
            option = next(
                (o for o in category["options"] if o["id"] == choice["id"]), None
            )
            if option is None:
                raise PromptAssistError(
                    f"prompt_assist.choices.{category['id']}",
                    f"{category['label']} has no option {choice['id']!r}.",
                )
            if option.get("text"):
                picked.append({**part, "id": option["id"], "text": option["text"]})
            if option.get("finish"):
                finishes.append(
                    {
                        **part,
                        "kind": "finish",
                        "id": option["id"],
                        "text": option["finish"],
                    }
                )
        elif as_sentence(choice.get("text") or ""):
            picked.append({**part, "id": None, "text": as_sentence(choice["text"])})
    parts = []
    for part in picked + finishes:
        if part["text"] in prompt:
            logger.info(
                "Prompt assist: the %s %s text is already in the prompt, so it "
                "is not added again.",
                part["category"],
                part["kind"],
            )
            continue
        parts.append(part)
    return {
        "v": STAMP_VERSION,
        "family": family,
        "presets": document["family"],
        "context": context,
        "preset_version": document["version"],
        "joiner": JOINER,
        "parts": parts,
    }
