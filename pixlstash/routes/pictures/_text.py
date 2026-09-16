"""Text read out of a picture (#1197).

``GET /pictures/{id}/text`` serves the words with their boxes, marking the ones
a search matched. ``POST /pictures/{id}/text/read`` reads the picture again.

Both are ``PICTURE_SCOPED`` (id_param ``id``) in ``pixlstash/authz/registry.py``;
the authz gate checks scope before the handler runs.
"""

import json
from types import SimpleNamespace
from typing import Literal, Optional

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel
from pixlstash.database import ocr_query_terms, ocr_text_match, ocr_word_matches
from pixlstash.pixl_logging import get_logger
from pixlstash.services import picture_service
from pixlstash.tasks.ocr_task import OCR_MIN_TEXT_SCORE, OcrTask

logger = get_logger(__name__)


class PictureTextWord(BaseModel):
    text: str
    box: list[float]
    matched: bool = False


class PictureTextResponse(BaseModel):
    state: Literal["none", "pending", "read"]
    lines: list[list[PictureTextWord]] = []


def _picture_id(id: str) -> int:
    try:
        return int(id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid picture id") from exc


def register_routes(router, server):
    @router.get(
        "/pictures/{id}/text",
        summary="Get the text read out of a picture",
        description=(
            "Returns the words read out of the picture, a line at a time, each "
            "with its box as fractions [x, y, w, h] of the picture as displayed. "
            "`state` is `pending` while a picture that looks like it carries "
            "text is waiting to be read. With `query`, words the search matched "
            "carry `matched: true`."
        ),
        response_model=PictureTextResponse,
    )
    def get_picture_text(request: Request, id: str, query: Optional[str] = Query(None)):
        pic_id = _picture_id(id)

        row = picture_service.fetch_picture_text(server.vault.db, pic_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Picture not found")
        ocr_text, ocr_words, text_score, deleted = row
        if ocr_text is None:
            # Only a picture the finder will actually reach is waiting.
            pending = (
                not deleted
                and text_score is not None
                and text_score >= OCR_MIN_TEXT_SCORE
            )
            return {"state": "pending" if pending else "none", "lines": []}
        if not ocr_words:
            return {"state": "none", "lines": []}
        # Words are marked only when the search itself matched the picture, which
        # takes every query word: the Text tab and the pill's In text count agree.
        terms = (
            ocr_query_terms(query) if query and ocr_text_match(ocr_text, query) else ()
        )
        try:
            lines = [
                [
                    {
                        "text": str(word["text"]),
                        "box": word["box"],
                        "matched": any(
                            ocr_word_matches(term, str(word["text"])) for term in terms
                        ),
                    }
                    for word in line
                ]
                for line in json.loads(ocr_words)
            ]
        except (ValueError, TypeError, KeyError) as exc:
            logger.error(
                "Stored word boxes for picture %s are not lines of words: %s",
                pic_id,
                exc,
            )
            return {"state": "none", "lines": []}
        return {"state": "read", "lines": lines}

    @router.post(
        "/pictures/{id}/text/read",
        summary="Read the text in a picture again",
        description=(
            "Reads the text in the picture again in the background. The stored "
            "text is replaced only when the read succeeds. A `pictures_changed` "
            'event with `fields: ["ocr_text"]` follows when it is done.'
        ),
        response_model=PictureTextResponse,
    )
    def read_picture_text(request: Request, id: str):
        pic_id = _picture_id(id)
        engine = getattr(server.vault, "_engine", None)
        if engine is None:
            raise HTTPException(
                status_code=503, detail="Inference engine not available."
            )

        file_path = picture_service.fetch_picture_file_path(server.vault.db, pic_id)
        if file_path is None:
            raise HTTPException(status_code=404, detail="Picture not found")
        # The text is not cleared first: a failed read then keeps what was there.
        # ponytail: no dedupe, so repeated presses queue repeated reads with the
        # same result; add a claim if that ever costs more than a press.
        task = OcrTask(
            server.vault.db,
            engine,
            [SimpleNamespace(id=pic_id, file_path=file_path)],
            interactive=True,
        )
        if server.vault.submit_task(task) is None:
            raise HTTPException(status_code=503, detail="Task runner not available.")
        return {"state": "pending", "lines": []}
