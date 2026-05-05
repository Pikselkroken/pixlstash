import hashlib
import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from passlib.hash import bcrypt
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlmodel import Session, select

from pixlstash.database import DBPriority, VaultDatabase
from pixlstash.db_models import Character, PictureSet, Project, User, UserToken
from pixlstash.utils.service.system_utils import default_max_vram_gb


class LoginRequest(BaseModel):
    username: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Username is required",
    )
    password: Optional[str] = Field(
        default=None,
        min_length=8,
        description="Password must be at least 8 characters long",
    )
    token: Optional[str] = Field(
        default=None,
        description="API token for authentication",
    )


# Paths and prefixes that bypass authentication — also used by rate limiting.
AUTH_EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "/login",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/docs/oauth2-redirect",
        "/favicon.ico",
        "/",
        "/version",
        "/check-session",
        "/logout",
        "/Logo.png",
        "/Empty.png",
        "/EmptyTrash.png",
    }
)
AUTH_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/assets/",
    "/pictures/shared/",
    "/share/",
    "/docs/",
    "/redoc/",
)
AUTH_API_PREFIXES: tuple[str, ...] = ("/api/v1",)

# POST paths that are semantically read-only (large request bodies preclude GET).
# These are exempted from the "block non-GET for READ tokens" check.
READ_SAFE_POST_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/pictures/thumbnails",
    }
)

# GET paths that expose user settings and must not be accessible to READ-scoped tokens.
READ_BLOCKED_GET_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/users/me/config",
    }
)


def is_auth_excluded_path(path: str) -> bool:
    """Return True when *path* should bypass auth checks.

    Supports both legacy unversioned public paths and versioned API paths
    (e.g. ``/api/v1/login``).
    """
    if path in AUTH_EXCLUDED_PATHS or any(
        path.startswith(prefix) for prefix in AUTH_EXCLUDED_PREFIXES
    ):
        return True

    for api_prefix in AUTH_API_PREFIXES:
        if not path.startswith(api_prefix):
            continue
        stripped = path[len(api_prefix) :] or "/"
        if stripped in AUTH_EXCLUDED_PATHS or any(
            stripped.startswith(prefix) for prefix in AUTH_EXCLUDED_PREFIXES
        ):
            return True

    return False


@dataclass
class TokenScope:
    """Scope restriction carried on request.state for token-authenticated requests."""

    scope: str
    resource_type: Optional[str]
    resource_id: Optional[int]
    expires_at: Optional[datetime]
    include_attachments: bool = False


class AuthService:
    def __init__(
        self, db: VaultDatabase, server_config: dict, server_config_path: str, logger
    ):
        self._db = db
        self._server_config = server_config
        self._server_config_path = server_config_path
        self._logger = logger
        self.active_session_ids: dict[str, int] = {}
        self.user: Optional[User] = None
        self.password_hash: Optional[str] = None
        self.username: Optional[str] = None
        self._failed_login_attempts: int = 0
        self._login_lockout_until: float = 0.0
        # Cache of recently-verified tokens: digest(token_value) → (UserToken, expiry_monotonic)
        # Avoids a bcrypt.verify() call on every authenticated request.
        self._token_cache: dict[str, tuple[UserToken, float]] = {}
        self._TOKEN_CACHE_TTL = 300.0  # seconds

    def ensure_secure_when_required(self, request: Request):
        if self._server_config.get("require_ssl", False):
            if request.url.scheme != "https":
                raise HTTPException(
                    status_code=403,
                    detail="HTTPS is required for this operation.",
                )

    def _validate_bcrypt_password_length(self, password: Optional[str]):
        if password is None:
            return
        try:
            byte_length = len(password.encode("utf-8"))
        except Exception:
            byte_length = len(password)
        if byte_length > 72:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Password cannot be longer than 72 bytes. "
                    "Truncate or shorten the password and try again."
                ),
            )

    def get_user(self) -> Optional[User]:
        return self._db.run_task(
            lambda session: session.exec(select(User)).first(),
            priority=DBPriority.IMMEDIATE,
        )

    def ensure_user(self) -> User:
        def ensure_user(session: Session):
            user = session.exec(select(User)).first()
            if user:
                if getattr(user, "max_vram_gb", None) is None:
                    user.max_vram_gb = default_max_vram_gb()
                    session.add(user)
                    session.commit()
                    session.refresh(user)
                return user

            user = User(
                max_vram_gb=default_max_vram_gb(),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self._db.run_task(ensure_user, priority=DBPriority.IMMEDIATE)
        self.user = user
        self.password_hash = user.password_hash if user else None
        self.username = user.username if user else None
        return user

    def set_password_hash(self, hashed_password: str):
        def update_user(session: Session):
            user = session.exec(select(User)).first()
            if user is None:
                user = User(max_vram_gb=default_max_vram_gb())
            user.password_hash = hashed_password
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self._db.run_task(update_user, priority=DBPriority.IMMEDIATE)
        self.password_hash = user.password_hash
        self.user = user
        return user

    def set_username(self, username: str):
        def update_user(session: Session):
            user = session.exec(select(User)).first()
            if user is None:
                user = User(max_vram_gb=default_max_vram_gb())
            user.username = username
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self._db.run_task(update_user, priority=DBPriority.IMMEDIATE)
        self.username = user.username
        self.user = user
        return user

    def remove_password_hash(self):
        self._logger.info("Removing stored password hash from user database.")

        def clear_user(session: Session):
            user = session.exec(select(User)).first()
            if user is None:
                return None
            user.password_hash = None
            user.username = None
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self._db.run_task(clear_user, priority=DBPriority.IMMEDIATE)
        self.user = user
        self.password_hash = None
        self.username = None
        self.active_session_ids = {}
        if "PASSWORD_HASH" in self._server_config:
            del self._server_config["PASSWORD_HASH"]
        if "USERNAME" in self._server_config:
            del self._server_config["USERNAME"]
            with open(self._server_config_path, "w") as f:
                json.dump(self._server_config, f, indent=2)
        return user

    def _token_from_value(self, token_value: str) -> Optional[UserToken]:
        """Validate a raw token value using prefix-indexed lookup; return the
        matching UserToken or None.  Legacy tokens without a token_prefix are
        checked with full iteration as a backward-compatible fallback.

        Results are cached for _TOKEN_CACHE_TTL seconds to avoid a bcrypt call
        on every request (bcrypt is intentionally slow ~200 ms).
        """
        if not token_value:
            return None

        # Fast path: check in-memory cache first.
        digest = hashlib.sha256(token_value.encode()).hexdigest()
        now_mono = time.monotonic()
        cached = self._token_cache.get(digest)
        if cached is not None:
            token_obj, expires_mono = cached
            if now_mono < expires_mono:
                # Validate token hasn't been expired server-side either.
                if (
                    token_obj.expires_at is None
                    or token_obj.expires_at > datetime.utcnow()
                ):
                    return token_obj
            # Cache entry stale — remove and fall through to verification.
            self._token_cache.pop(digest, None)

        user = self.get_user()
        if user is None:
            return None

        prefix = token_value[:8]

        def fetch_candidates(session: Session, user_id: int, prefix: str):
            return session.exec(
                select(UserToken).where(
                    UserToken.user_id == user_id,
                    or_(
                        UserToken.token_prefix == prefix,
                        UserToken.token_prefix.is_(None),
                    ),
                )
            ).all()

        tokens = self._db.run_task(
            fetch_candidates, user.id, prefix, priority=DBPriority.IMMEDIATE
        )
        now = datetime.utcnow()
        for token in tokens:
            if token.expires_at is not None and token.expires_at < now:
                continue
            if bcrypt.verify(token_value, token.token_hash):

                def update_last_used(session: Session, token_id: int):
                    db_token = session.get(UserToken, token_id)
                    if db_token is not None:
                        db_token.last_used_at = datetime.utcnow()
                        session.add(db_token)
                        session.commit()

                self._db.run_task(
                    update_last_used, token.id, priority=DBPriority.IMMEDIATE
                )
                # Populate cache so subsequent requests skip bcrypt.
                self._token_cache[digest] = (token, now_mono + self._TOKEN_CACHE_TTL)
                # Evict entries beyond a reasonable cap to bound memory use.
                if len(self._token_cache) > 1000:
                    self._token_cache.pop(next(iter(self._token_cache)))
                return token
        return None

    def _user_id_from_bearer(self, request: Request) -> Optional[int]:
        """Validate a Bearer token from the Authorization header and return the user id."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token_value = auth_header[len("Bearer ") :]
        matched = self._token_from_value(token_value)
        if matched is not None:
            user = self.get_user()
            return user.id if user else None
        return None

    def _token_from_query_param(self, request: Request) -> Optional[UserToken]:
        """Validate a ?token= query parameter.  Only READ-scoped tokens are
        accepted this way — ALL-scoped tokens must not be placed in URLs."""
        token_value = request.query_params.get("token")
        if not token_value:
            return None
        matched = self._token_from_value(token_value)
        if matched is None:
            return None
        if matched.scope != "READ":
            return None
        return matched

    def get_user_id(self, request: Request) -> Optional[int]:
        user_id = getattr(request.state, "auth_user_id", None)
        if user_id is not None:
            return user_id
        session_id = request.cookies.get("session_id")
        if session_id:
            return self.active_session_ids.get(session_id)
        return self._user_id_from_bearer(request)

    def require_user_id(
        self, request: Request, detail: str = "Not authenticated"
    ) -> int:
        user_id = getattr(request.state, "auth_user_id", None)
        if user_id is not None:
            return user_id
        session_id = request.cookies.get("session_id")
        if session_id:
            user_id = self.active_session_ids.get(session_id)
            if user_id is not None:
                return user_id
        user_id = self._user_id_from_bearer(request)
        if user_id is not None:
            return user_id
        raise HTTPException(status_code=401, detail=detail)

    def get_user_for_request(self, request: Request) -> User:
        user_id = self.require_user_id(request)
        user = self._db.run_task(
            lambda session: session.get(User, user_id),
            priority=DBPriority.IMMEDIATE,
        )
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def change_password(self, request: Request, payload) -> dict:
        self.ensure_secure_when_required(request)
        user = self.get_user_for_request(request)

        self._validate_bcrypt_password_length(payload.current_password)
        self._validate_bcrypt_password_length(payload.new_password)

        if user.password_hash:
            if not payload.current_password:
                raise HTTPException(
                    status_code=400,
                    detail="Current password is required",
                )
            if not bcrypt.verify(payload.current_password, user.password_hash):
                raise HTTPException(status_code=401, detail="Invalid password")

        hashed_password = bcrypt.hash(payload.new_password)

        def update_user(session: Session, user_id: int):
            db_user = session.get(User, user_id)
            if db_user is None:
                self._logger.debug(
                    "User %s not found in DB when updating",
                    user_id,
                )
                raise HTTPException(
                    status_code=404, detail="User not found when updating"
                )
            db_user.password_hash = hashed_password
            session.add(db_user)
            session.commit()
            session.refresh(db_user)
            return db_user

        updated_user = self._db.run_task(
            update_user, user.id, priority=DBPriority.IMMEDIATE
        )
        self.user = updated_user
        self.password_hash = updated_user.password_hash
        self.username = updated_user.username
        self.active_session_ids = {}
        return {"status": "success"}

    def get_auth_info(self, request: Request) -> dict:
        self.ensure_secure_when_required(request)
        user = self.get_user_for_request(request)
        return {
            "username": user.username,
            "has_password": bool(user.password_hash),
        }

    def create_token(
        self,
        request: Request,
        description: Optional[str],
        scope: str = "ALL",
        resource_type: Optional[str] = None,
        resource_id: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        include_attachments: bool = False,
    ):
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        # Only the owner (full session or ALL-scope bearer) may create tokens
        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(
                status_code=403, detail="Scoped tokens cannot create new tokens"
            )

        if scope not in ("ALL", "READ"):
            raise HTTPException(status_code=400, detail="scope must be 'ALL' or 'READ'")
        if resource_type is not None and resource_type not in (
            "picture_set",
            "character",
            "project",
            "picture",
        ):
            raise HTTPException(
                status_code=400,
                detail="resource_type must be 'picture_set', 'character', 'project', or 'picture'",
            )

        # A date-only value (e.g. "2026-05-05") is parsed as midnight 00:00:00,
        # which would expire the token at the very start of that day.  Normalize
        # it to end-of-day so the token remains valid throughout the named day.
        if (
            expires_at is not None
            and expires_at.hour == 0
            and expires_at.minute == 0
            and expires_at.second == 0
        ):
            expires_at = expires_at.replace(hour=23, minute=59, second=59)

        token_value = secrets.token_urlsafe(32)
        token_hash = bcrypt.hash(token_value)
        token_prefix = token_value[:8]

        def _create_token(
            session: Session,
            user_id: int,
            token_hash: str,
            token_prefix: str,
            desc: Optional[str],
            scope: str,
            resource_type: Optional[str],
            resource_id: Optional[int],
            expires_at: Optional[datetime],
            include_attachments: bool,
        ):
            user = session.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            token = UserToken(
                user_id=user_id,
                token_hash=token_hash,
                token_prefix=token_prefix,
                created_at=datetime.utcnow(),
                description=desc,
                scope=scope,
                resource_type=resource_type,
                resource_id=resource_id,
                expires_at=expires_at,
                include_attachments=include_attachments,
            )
            session.add(token)
            session.commit()
            session.refresh(token)
            return token

        token = self._db.run_task(
            _create_token,
            user_id,
            token_hash,
            token_prefix,
            description,
            scope,
            resource_type,
            resource_id,
            expires_at,
            include_attachments,
            priority=DBPriority.IMMEDIATE,
        )

        return {
            "token": token_value,
            "token_id": token.id,
            "scope": token.scope,
            "resource_type": token.resource_type,
            "resource_id": token.resource_id,
            "expires_at": token.expires_at,
            "include_attachments": token.include_attachments,
        }

    def list_tokens(self, request: Request):
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        def fetch_tokens(session: Session, user_id: int):
            tokens = session.exec(
                select(UserToken)
                .where(UserToken.user_id == user_id)
                .order_by(UserToken.created_at.desc())
            ).all()
            result = []
            for token in tokens:
                resource_name = None
                if token.resource_type == "character" and token.resource_id is not None:
                    obj = session.get(Character, token.resource_id)
                    resource_name = obj.name if obj else None
                elif token.resource_type == "picture_set" and token.resource_id is not None:
                    obj = session.get(PictureSet, token.resource_id)
                    resource_name = obj.name if obj else None
                elif token.resource_type == "project" and token.resource_id is not None:
                    obj = session.get(Project, token.resource_id)
                    resource_name = obj.name if obj else None
                result.append({
                    "id": token.id,
                    "description": token.description,
                    "scope": token.scope,
                    "resource_type": token.resource_type,
                    "resource_id": token.resource_id,
                    "resource_name": resource_name,
                    "expires_at": token.expires_at,
                    "created_at": token.created_at,
                    "last_used_at": token.last_used_at,
                    "include_attachments": token.include_attachments,
                })
            return result

        return self._db.run_task(fetch_tokens, user_id, priority=DBPriority.IMMEDIATE)

    def delete_token(self, request: Request, token_id: int):
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        def remove_token(session: Session, user_id: int, token_id: int):
            token = session.get(UserToken, token_id)
            if token is None or token.user_id != user_id:
                raise HTTPException(status_code=404, detail="Token not found")
            session.delete(token)
            session.commit()
            return True

        self._db.run_task(
            remove_token, user_id, token_id, priority=DBPriority.IMMEDIATE
        )
        # Clear the token cache — we can't map token_id back to the digest key,
        # so flush all entries to ensure the deleted token is not reused.
        self._token_cache.clear()
        return {"status": "success", "deleted_id": token_id}

    def revoke_tokens_for_resource(
        self,
        request: Request,
        resource_type: str,
        resource_id: int,
    ):
        """Delete all tokens scoped to a specific resource owned by the user."""
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        # Only the owner (full session or ALL-scope bearer) may delete tokens.
        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(
                status_code=403, detail="Scoped tokens cannot revoke tokens"
            )

        def _revoke(session: Session, user_id: int, rt: str, rid: int) -> int:
            tokens = session.exec(
                select(UserToken).where(
                    UserToken.user_id == user_id,
                    UserToken.resource_type == rt,
                    UserToken.resource_id == rid,
                )
            ).all()
            count = len(tokens)
            for t in tokens:
                session.delete(t)
            session.commit()
            return count

        deleted = self._db.run_task(
            _revoke, user_id, resource_type, resource_id, priority=DBPriority.IMMEDIATE
        )
        self._token_cache.clear()
        return {"status": "success", "deleted_count": deleted}

    def get_shared_resource_ids(self, request: Request, resource_type: str):
        """Return the set of resource_ids for which the user has active READ tokens."""
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(status_code=403, detail="Not allowed for scoped tokens")

        def _fetch(session: Session, user_id: int, rt: str) -> list[int]:
            now = datetime.utcnow()
            tokens = session.exec(
                select(UserToken).where(
                    UserToken.user_id == user_id,
                    UserToken.resource_type == rt,
                    UserToken.scope == "READ",
                )
            ).all()
            return [
                t.resource_id
                for t in tokens
                if t.resource_id is not None
                and (t.expires_at is None or t.expires_at > now)
            ]

        ids = self._db.run_task(_fetch, user_id, resource_type, priority=DBPriority.IMMEDIATE)
        return {"resource_type": resource_type, "ids": ids}

    def batch_get_shared_picture_ids(
        self, request: Request, picture_ids: list[int]
    ):
        """Given a list of picture_ids, return which ones have active READ tokens."""
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(status_code=403, detail="Not allowed for scoped tokens")

        if not picture_ids:
            return {"shared_ids": []}

        def _fetch(session: Session, user_id: int, ids: list[int]) -> list[int]:
            now = datetime.utcnow()
            id_set = set(ids)
            tokens = session.exec(
                select(UserToken).where(
                    UserToken.user_id == user_id,
                    UserToken.resource_type == "picture",
                    UserToken.scope == "READ",
                    UserToken.resource_id.in_(list(id_set)),
                )
            ).all()
            return [
                t.resource_id
                for t in tokens
                if t.resource_id is not None
                and (t.expires_at is None or t.expires_at > now)
            ]

        shared = self._db.run_task(
            _fetch, user_id, picture_ids, priority=DBPriority.IMMEDIATE
        )
        return {"shared_ids": shared}

    def check_session(self, request: Request) -> JSONResponse:
        session_id = request.cookies.get("session_id")
        if session_id and session_id in self.active_session_ids:
            return JSONResponse(content={"status": "success"})
        raise HTTPException(status_code=401, detail="Invalid session")

    def login(self, request) -> Response:
        remaining = self._login_lockout_until - time.monotonic()
        if remaining > 0:
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Try again later.",
                headers={"Retry-After": str(int(remaining) + 1)},
            )
        try:
            response = self._do_login(request)
        except HTTPException as exc:
            if exc.status_code == 401:
                self._failed_login_attempts += 1
                if self._failed_login_attempts >= 5:
                    self._login_lockout_until = time.monotonic() + 60
                    self._logger.warning(
                        "5 failed login attempts — locked out for 60s."
                    )
                else:
                    self._logger.warning(
                        "Login failure #%d.", self._failed_login_attempts
                    )
            raise
        if self._failed_login_attempts:
            self._logger.info(
                "Login succeeded after %d failure(s). Resetting lockout.",
                self._failed_login_attempts,
            )
        self._failed_login_attempts = 0
        self._login_lockout_until = 0.0
        return response

    def _do_login(self, request) -> Response:
        if request.token:
            user = self.get_user()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            def fetch_tokens(session: Session, user_id: int):
                return session.exec(
                    select(UserToken).where(UserToken.user_id == user_id)
                ).all()

            tokens = self._db.run_task(
                fetch_tokens, user.id, priority=DBPriority.IMMEDIATE
            )
            matched_token = None
            for token in tokens:
                if bcrypt.verify(request.token, token.token_hash):
                    matched_token = token
                    break
            if matched_token is None:
                raise HTTPException(status_code=401, detail="Invalid token")

            def update_token_last_used(session: Session, token_id: int):
                db_token = session.get(UserToken, token_id)
                if db_token is None:
                    return None
                db_token.last_used_at = datetime.utcnow()
                session.add(db_token)
                session.commit()
                return db_token

            self._db.run_task(
                update_token_last_used,
                matched_token.id,
                priority=DBPriority.IMMEDIATE,
            )

            response = JSONResponse(content={"message": "Login successful."})
        else:
            if not request.username or not request.password:
                raise HTTPException(
                    status_code=400,
                    detail="Username and password are required",
                )

            user = self.get_user() or self.ensure_user()
            if not user.username or not user.password_hash:
                self._validate_bcrypt_password_length(request.password)
                hashed_password = bcrypt.hash(request.password)

                def set_credentials(session: Session):
                    db_user = session.exec(select(User)).first()
                    if db_user is None:
                        db_user = User(max_vram_gb=default_max_vram_gb())
                    db_user.username = request.username
                    db_user.password_hash = hashed_password
                    session.add(db_user)
                    session.commit()
                    session.refresh(db_user)
                    return db_user

                user = self._db.run_task(set_credentials, priority=DBPriority.IMMEDIATE)
                self.user = user
                self.username = user.username
                self.password_hash = user.password_hash
                response = JSONResponse(
                    content={"message": "Username and password set successfully."}
                )
            else:
                if request.username != user.username:
                    raise HTTPException(status_code=401, detail="Invalid username")
                self._validate_bcrypt_password_length(request.password)
                if not bcrypt.verify(request.password, user.password_hash):
                    raise HTTPException(status_code=401, detail="Invalid password")
                response = JSONResponse(content={"message": "Login successful."})

        session_id = str(uuid.uuid4())
        if not user or user.id is None:
            raise HTTPException(status_code=500, detail="User not found")
        self.active_session_ids[session_id] = user.id

        cookie_samesite = self._server_config.get("cookie_samesite", "Lax")
        cookie_secure = self._server_config.get("cookie_secure", False)
        if cookie_samesite == "None" and not cookie_secure:
            self._logger.warning(
                "cookie_samesite=None requires cookie_secure=True for cross-site cookies to work in browsers."
            )
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            samesite=cookie_samesite,
            secure=bool(cookie_secure),
        )
        return response

    def get_session_context(self, request: Request) -> dict:
        """Return the access scope for the current request.

        Used by the frontend to determine what the current token/session allows.
        """
        token_scope = getattr(request.state, "token_scope", None)
        user_id = getattr(request.state, "auth_user_id", None) or self.get_user_id(
            request
        )
        if token_scope is None:
            return {
                "is_owner": user_id is not None,
                "scope": "ALL",
                "resource_type": None,
                "resource_id": None,
                "expires_at": None,
            }
        return {
            "is_owner": False,
            "scope": token_scope.scope,
            "resource_type": token_scope.resource_type,
            "resource_id": token_scope.resource_id,
            "expires_at": token_scope.expires_at,
            "include_attachments": token_scope.include_attachments,
        }

    def check_registration(self) -> JSONResponse:
        user = self.get_user()
        if not user or not user.username or not user.password_hash:
            return JSONResponse(content={"needs_registration": True})
        return JSONResponse(content={"needs_registration": False})

    def logout(self, response: Response, request: Request):
        session_id = request.cookies.get("session_id")
        if session_id in self.active_session_ids:
            self.active_session_ids.pop(session_id, None)
        response.delete_cookie("session_id", path="/")
        return {"message": "Logged out successfully."}

    async def auth_middleware(
        self, request: Request, call_next, allow_origins, allow_origin_regex
    ):
        if request.method == "OPTIONS":
            return await call_next(request)

        if not is_auth_excluded_path(request.url.path):
            session_id = request.cookies.get("session_id")
            user_id = self.active_session_ids.get(session_id) if session_id else None

            if user_id is not None:
                # Cookie session — full owner access, no scope restriction
                request.state.auth_user_id = user_id
            else:
                # Try Bearer token, then fall back to ?token= query param
                matched_token: Optional[UserToken] = None
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token_value = auth_header[len("Bearer ") :]
                    matched_token = self._token_from_value(token_value)
                if matched_token is None:
                    matched_token = self._token_from_query_param(request)

                if matched_token is not None:
                    user = self.get_user()
                    user_id = user.id if user else None
                    if user_id:
                        request.state.auth_user_id = user_id
                        if matched_token.scope != "ALL":
                            request.state.token_scope = TokenScope(
                                scope=matched_token.scope,
                                resource_type=matched_token.resource_type,
                                resource_id=matched_token.resource_id,
                                expires_at=matched_token.expires_at,
                                include_attachments=matched_token.include_attachments,
                            )

                if user_id is None:
                    self._logger.error(
                        "Invalid session_id. It has expired and the client needs to log in again. When trying to access %s",
                        request.url.path,
                    )
                    origin = request.headers.get("origin")
                    headers = {
                        "Access-Control-Allow-Credentials": "true",
                    }
                    if origin and (
                        origin in allow_origins
                        or (allow_origin_regex and re.match(allow_origin_regex, origin))
                    ):
                        headers["Access-Control-Allow-Origin"] = origin

                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Not authenticated"},
                        headers=headers,
                    )

            # Block write operations for READ-scoped tokens.
            # Paths in READ_SAFE_POST_PATHS use POST for body size but are semantically read-only.
            token_scope = getattr(request.state, "token_scope", None)
            if token_scope is not None and token_scope.scope == "READ":
                if (
                    request.method not in ("GET", "HEAD", "OPTIONS")
                    and request.url.path not in READ_SAFE_POST_PATHS
                ):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Token is read-only"},
                    )
                if (
                    request.method == "GET"
                    and request.url.path in READ_BLOCKED_GET_PATHS
                ):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Token is read-only"},
                    )

        return await call_next(request)
