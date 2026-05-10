"""Guest scoring routes.

READ-token users may submit star scores (0-5) for pictures.  Scores are stored
in the guest_session / guest_score tables and never touch picture.score.

POST /pictures/guest-scores  — write exception for READ tokens
GET  /pictures/guest-scores  — retrieve this session's scores (READ tokens)
"""

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.guest_score import GuestScore
from pixlstash.db_models.guest_session import GuestSession
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# 90-day cookie lifetime in seconds
_COOKIE_MAX_AGE = 7_776_000


def create_router(server) -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # GET /pictures/guest-scores
    # ------------------------------------------------------------------

    @router.get("/pictures/guest-scores")
    def get_guest_scores(request: Request):
        """Return all scores submitted by this guest session.

        Requires a READ-scoped token.  The guest_session cookie must be present
        and valid (resolved by auth_middleware into request.state.guest_session_id).

        Returns:
            A JSON object ``{"scores": {"<picture_id>": <score>, ...}}``.
        """
        token_scope = getattr(request.state, "token_scope", None)
        if token_scope is None or token_scope.scope != "READ":
            raise HTTPException(status_code=403, detail="Requires a READ-scoped token")

        session_id: str | None = getattr(request.state, "guest_session_id", None)
        if not session_id:
            return {"scores": {}}

        def fetch(session: Session):
            rows = session.exec(
                select(GuestScore).where(GuestScore.session_id == session_id)
            ).all()
            return {str(row.picture_id): row.score for row in rows}

        scores = server.vault.db.run_immediate_read_task(fetch)
        return {"scores": scores}

    # ------------------------------------------------------------------
    # POST /pictures/guest-scores
    # ------------------------------------------------------------------

    @router.delete("/pictures/guest-scores/session")
    def clear_guest_session(request: Request):
        """Clear the guest session cookies for this browser.

        Removes the ``guest_session`` and ``guest_session_active`` cookies so
        the browser starts a fresh anonymous session on the next page load.
        The scores stored in the database are retained (they may still be used
        for aggregate statistics); we simply sever the link between this browser
        and those scores.

        Requires a READ-scoped token.

        Returns:
            ``{"ok": true}``
        """
        token_scope = getattr(request.state, "token_scope", None)
        if token_scope is None or token_scope.scope != "READ":
            raise HTTPException(status_code=403, detail="Requires a READ-scoped token")

        response = JSONResponse({"ok": True})
        is_https = request.url.scheme == "https"
        cookie_kwargs = {"samesite": "lax", **({"secure": True} if is_https else {})}
        response.delete_cookie("guest_session", httponly=True, **cookie_kwargs)
        response.delete_cookie("guest_session_active", httponly=False, **cookie_kwargs)
        return response

    @router.post("/pictures/guest-scores")
    async def submit_guest_scores(request: Request):
        """Submit or update star scores for one or more pictures.

        This is the sole write endpoint accessible to READ-scoped tokens.

        Request body (JSON):
            session_id (str): Client-generated UUID (max 64 chars, ``[A-Za-z0-9_-]``).
            set_cookie (bool): When True the server sets persistent cookies.
            scores (dict[str, int]): Mapping of picture_id → score (0-5).
                At most 500 entries per request.

        Returns:
            ``{"ok": true}`` on success.

        Raises:
            400: Validation error (bad session_id, bad score value, too many entries).
            503: Too many concurrent active guest sessions (new session refused).
        """
        token_scope = getattr(request.state, "token_scope", None)
        if token_scope is None or token_scope.scope != "READ":
            raise HTTPException(status_code=403, detail="Requires a READ-scoped token")

        token_id: int = getattr(request.state, "token_id", None)
        if token_id is None:
            raise HTTPException(status_code=403, detail="No token_id on request state")

        body: dict[str, Any] = await request.json()

        # Validate session_id
        session_id = body.get("session_id", "")
        if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
            raise HTTPException(
                status_code=400,
                detail="session_id must be 1-64 characters [A-Za-z0-9_-]",
            )

        set_cookie: bool = bool(body.get("set_cookie", False))
        raw_scores: Any = body.get("scores", {})

        if not isinstance(raw_scores, dict):
            raise HTTPException(status_code=400, detail="scores must be an object")

        if len(raw_scores) > 500:
            raise HTTPException(
                status_code=400,
                detail="At most 500 scores may be submitted per request",
            )

        # Validate and coerce score entries
        validated_scores: dict[int, int] = {}
        for key, val in raw_scores.items():
            try:
                pic_id = int(key)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Picture id must be an integer, got: {key!r}",
                )
            if not isinstance(val, int) or not (0 <= val <= 5):
                raise HTTPException(
                    status_code=400,
                    detail=f"Score must be an integer 0-5, got {val!r} for picture {pic_id}",
                )
            validated_scores[pic_id] = val

        # Resolve config limits
        max_stored = int(server._server_config.get("guest_max_stored_sessions", 1000))
        max_concurrent = int(
            server._server_config.get("guest_max_concurrent_sessions", 100)
        )

        now = datetime.utcnow()

        def handle_session(session: Session) -> None:
            existing = session.get(GuestSession, session_id)

            if existing is None:
                # Brand-new session — check active concurrent limit first
                active_count = server.auth.count_active_guest_sessions()
                if active_count >= max_concurrent:
                    raise HTTPException(
                        status_code=503,
                        detail="Too many active guest sessions, please try again later",
                    )

                # FIFO eviction: delete oldest session if stored cap is reached
                total_count_row = session.exec(
                    select(GuestSession).where(text("1=1"))
                ).all()
                if len(total_count_row) >= max_stored:
                    oldest = session.exec(
                        select(GuestSession).order_by(GuestSession.created_at)
                    ).first()
                    if oldest is not None:
                        session.delete(oldest)
                        session.flush()

                # Insert new GuestSession
                new_session = GuestSession(
                    session_id=session_id,
                    token_id=token_id,
                    created_at=now,
                    last_active_at=now,
                )
                session.add(new_session)
                session.flush()
            else:
                # Returning session — just update last_active_at
                existing.last_active_at = now

            # Upsert scores using SQLite INSERT OR REPLACE
            for pic_id, score_val in validated_scores.items():
                session.exec(  # type: ignore[call-overload]
                    text(
                        "INSERT OR REPLACE INTO guest_score"
                        " (session_id, token_id, picture_id, score, scored_at)"
                        " VALUES (:sid, :tid, :pid, :score, :scored_at)"
                    ).bindparams(
                        sid=session_id,
                        tid=token_id,
                        pid=pic_id,
                        score=score_val,
                        scored_at=now.isoformat(),
                    )
                )

        def _run(session: Session):
            handle_session(session)
            session.commit()

        server.vault.db.run_task(_run, priority=DBPriority.IMMEDIATE)

        # Update in-memory active-session tracker
        server.auth.record_guest_activity(session_id)

        logger.info(
            "[guest-scores] POST session=%r set_cookie=%r scores=%r",
            session_id,
            set_cookie,
            validated_scores,
        )

        response = JSONResponse({"ok": True})

        if set_cookie:
            # Secure flag: set when HTTPS; omit for plain HTTP (local dev)
            is_https = request.url.scheme == "https"
            logger.info(
                "[guest-scores] Setting cookies for session=%r is_https=%r",
                session_id,
                is_https,
            )
            # HttpOnly cookie — the actual session identifier; JS cannot read it
            response.set_cookie(
                "guest_session",
                session_id,
                httponly=True,
                max_age=_COOKIE_MAX_AGE,
                samesite="lax",
                **({"secure": True} if is_https else {}),
            )
            # Non-HttpOnly sentinel — JS reads this to detect consent without
            # being able to read the session_id itself
            response.set_cookie(
                "guest_session_active",
                "1",
                httponly=False,
                max_age=_COOKIE_MAX_AGE,
                samesite="lax",
                **({"secure": True} if is_https else {}),
            )

        return response

    return router
