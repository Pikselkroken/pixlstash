import json
import re
import secrets
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from passlib.hash import bcrypt
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from pixlstash.database import DBPriority, VaultDatabase
from pixlstash.db_models import User, UserToken
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

    def get_user_id(self, request: Request) -> Optional[int]:
        session_id = request.cookies.get("session_id")
        if not session_id:
            return None
        return self.active_session_ids.get(session_id)

    def require_user_id(
        self, request: Request, detail: str = "Not authenticated"
    ) -> int:
        session_id = request.cookies.get("session_id")
        if not session_id:
            raise HTTPException(status_code=401, detail=detail)
        user_id = self.active_session_ids.get(session_id)
        if user_id is None:
            raise HTTPException(status_code=401, detail=detail)
        return user_id

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

    def create_token(self, request: Request, description: Optional[str]):
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        token_value = secrets.token_urlsafe(32)
        token_hash = bcrypt.hash(token_value)

        def create_token(
            session: Session, user_id: int, token_hash: str, desc: Optional[str]
        ):
            user = session.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            token = UserToken(
                user_id=user_id,
                token_hash=token_hash,
                created_at=datetime.utcnow(),
                description=desc,
            )
            session.add(token)
            session.commit()
            session.refresh(token)
            return token

        token = self._db.run_task(
            create_token,
            user_id,
            token_hash,
            description,
            priority=DBPriority.IMMEDIATE,
        )

        return {
            "token": token_value,
            "token_id": token.id,
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
            return tokens

        tokens = self._db.run_task(fetch_tokens, user_id, priority=DBPriority.IMMEDIATE)
        return [
            {
                "id": token.id,
                "description": token.description,
                "created_at": token.created_at,
                "last_used_at": token.last_used_at,
            }
            for token in tokens
        ]

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
        return {"status": "success", "deleted_id": token_id}

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
                tokens = session.exec(
                    select(UserToken).where(UserToken.user_id == user_id)
                ).all()
                return tokens

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
        excluded_paths = [
            "/login",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/docs/oauth2-redirect",
            "/favicon.ico",
            "/",
            "/version",
            "/version/latest",
            "/check-session",
            "/logout",
        ]
        excluded_prefixes = [
            "/assets/",
            "/pictures/shared/",
            "/docs/",
            "/redoc/",
        ]
        if request.method == "OPTIONS":
            return await call_next(request)

        if request.url.path not in excluded_paths and not any(
            request.url.path.startswith(prefix) for prefix in excluded_prefixes
        ):
            session_id = request.cookies.get("session_id")
            if session_id not in self.active_session_ids:
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
        return await call_next(request)
