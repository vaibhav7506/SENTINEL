"""Cookie sessions; bootstrap queries derive ownership only from verified identities."""

import asyncio
import hmac
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.saas.models import Account, AuditEvent, AuthSession, User
from app.saas.security import (
    Principal,
    digest,
    normalized_email,
    password_hash,
    raw_secret,
    verify_password,
)

router = APIRouter(tags=["browser authentication"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str) -> str:
        return normalized_email(value)


class RegistrationRequest(LoginRequest):
    account_name: str = Field(min_length=1, max_length=160)


def cookie_names(request: Request) -> tuple[str, str]:
    prefix = "__Host-" if request.app.state.settings.environment == "production" else ""
    return prefix + "sentinel_session", prefix + "sentinel_csrf"


def user_view(user: User, account: Account) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "account": {"id": str(account.id), "name": account.name},
    }


def audit(
    session: Any, account_id: Any, action: str, resource_id: Any = None, actor: Any = None
) -> None:
    session.add(
        AuditEvent(
            account_id=account_id,
            action=action,
            resource_id=str(resource_id) if resource_id is not None else None,
            actor_user_id=actor,
            details={},
        )
    )


async def issue_session(request: Request, response: Response, session: Any, user: User) -> None:
    now = datetime.now(UTC)
    raw = raw_secret("ses_")
    csrf = raw_secret("csrf_")
    lifetime = request.app.state.settings.session_lifetime_seconds
    session.add(
        AuthSession(
            account_id=user.account_id,
            user_id=user.id,
            token_hash=digest(raw),
            csrf_hash=digest(csrf),
            expires_at=now + timedelta(seconds=lifetime),
        )
    )
    user.last_login_at = now
    session_name, csrf_name = cookie_names(request)
    secure = request.app.state.settings.environment == "production"
    response.set_cookie(
        session_name, raw, max_age=lifetime, httponly=True, secure=secure, samesite="lax", path="/"
    )
    response.set_cookie(
        csrf_name,
        csrf,
        max_age=lifetime,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/auth/register", status_code=201)
async def register(
    payload: RegistrationRequest, request: Request, response: Response
) -> dict[str, Any]:
    if not payload.account_name.strip():
        raise HTTPException(422, "Account name is required")
    async with request.app.state.auth_hash_slots:
        encoded = await asyncio.to_thread(password_hash, payload.password)
    async with async_sessionmaker(request.app.state.engine, expire_on_commit=False)() as session:
        account = Account(name=payload.account_name.strip())
        session.add(account)
        await session.flush()
        user = User(account_id=account.id, email=payload.email, password_hash=encoded, role="OWNER")
        session.add(user)
        try:
            await session.flush()
            audit(session, account.id, "account.created", account.id, user.id)
            audit(session, account.id, "user.created", user.id, user.id)
            await issue_session(request, response, session, user)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(409, "Registration could not be completed") from None
        return user_view(user, account)


@router.post("/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    async with async_sessionmaker(request.app.state.engine, expire_on_commit=False)() as session:
        user = await session.scalar(
            select(User).where(User.email == payload.email, User.is_active.is_(True))
        )
        async with request.app.state.auth_hash_slots:
            if user:
                valid = await asyncio.to_thread(
                    verify_password, payload.password, user.password_hash
                )
            else:
                await asyncio.to_thread(password_hash, payload.password)
                valid = False
        if not valid or user is None:
            raise HTTPException(401, "Email or password is incorrect")
        account = await session.get(Account, user.account_id)
        if account is None:
            raise HTTPException(401, "Account unavailable")
        await issue_session(request, response, session, user)
        audit(session, user.account_id, "session.login", actor=user.id)
        await session.commit()
        return user_view(user, account)


@router.get("/auth/me")
async def me(request: Request, response: Response) -> dict[str, Any]:
    p: Principal = request.state.principal
    response.headers["Cache-Control"] = "no-store"
    return {
        "id": str(p.user_id),
        "email": p.email,
        "role": p.role,
        "account": {"id": str(p.account_id), "name": p.account_name},
    }


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    p: Principal = request.state.principal
    async with async_sessionmaker(request.app.state.engine)() as session:
        row = await session.get(AuthSession, p.session_id)
        if row is not None:
            row.revoked_at = datetime.now(UTC)
        audit(session, p.account_id, "session.logout", actor=p.user_id)
        await session.commit()
    for name in cookie_names(request):
        response.delete_cookie(
            name, path="/", secure=request.app.state.settings.environment == "production"
        )


def install_authentication(application: FastAPI, config: Settings) -> None:
    application.state.auth_hash_slots = asyncio.Semaphore(2)
    attempts: OrderedDict[str, deque[float]] = OrderedDict()
    public = {
        "/health",
        "/ready",
        "/metrics",
        "/auth/register",
        "/auth/login",
        "/agent/enroll",
        "/agent/metrics",
        "/agent/v1/metrics",
        "/agent/install.sh",
        "/agent/package.tar.gz",
    }

    @application.middleware("http")
    async def authenticate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and not path.startswith("/agent/"):
            origin = request.headers.get("origin")
            if origin is not None and origin not in config.saas_allowed_origins:
                return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin"})
        if path in {"/auth/register", "/auth/login"}:
            address = request.client.host if request.client else "unknown"
            if config.environment == "production":
                try:
                    allowed = await request.app.state.redis.eval(
                        "local n=redis.call('INCR',KEYS[1]); "
                        "if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
                        1,
                        "sentinel:auth:" + digest(address),
                    )
                except Exception:
                    return JSONResponse(
                        status_code=503,
                        content={"detail": "Authentication temporarily unavailable"},
                    )
                if allowed > 30:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Authentication rate limit"},
                        headers={"Retry-After": "60"},
                    )
            now = time.monotonic()
            bucket = attempts.setdefault(address, deque())
            attempts.move_to_end(address)
            while bucket and bucket[0] < now - 60:
                bucket.popleft()
            if len(bucket) >= 30:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Authentication rate limit"},
                    headers={"Retry-After": "60"},
                )
            bucket.append(now)
            if len(attempts) > 512:
                attempts.popitem(last=False)
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse(status_code=415, content={"detail": "JSON request required"})
        if path not in public:
            raw = request.cookies.get(cookie_names(request)[0], "")
            if not 32 <= len(raw) <= 128:
                return JSONResponse(status_code=401, content={"detail": "Authentication required"})
            try:
                async with async_sessionmaker(request.app.state.engine)() as session:
                    auth_now = datetime.now(UTC)
                    row = await session.scalar(
                        select(AuthSession).where(
                            AuthSession.token_hash == digest(raw),
                            AuthSession.revoked_at.is_(None),
                            AuthSession.expires_at > auth_now,
                        )
                    )
                    user = await session.get(User, row.user_id) if row else None
                    account = await session.get(Account, row.account_id) if row else None
                    if (
                        row is None
                        or user is None
                        or account is None
                        or not user.is_active
                        or row.account_id != user.account_id
                    ):
                        return JSONResponse(
                            status_code=401, content={"detail": "Authentication required"}
                        )
                    p = Principal(
                        user.id,
                        user.account_id,
                        user.email,
                        user.role,
                        row.id,
                        row.csrf_hash,
                        account.name,
                    )
            except Exception:
                request.app.state.database_errors.inc()
                return JSONResponse(
                    status_code=503, content={"detail": "Authentication unavailable"}
                )
            request.state.principal = p
            if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
                csrf = request.headers.get("x-csrf-token", "")
                if not csrf or not hmac.compare_digest(digest(csrf), p.csrf_hash):
                    return JSONResponse(
                        status_code=403, content={"detail": "CSRF verification required"}
                    )
        response = await call_next(request)
        if path not in {
            "/health",
            "/ready",
            "/metrics",
            "/agent/install.sh",
            "/agent/package.tar.gz",
        }:
            response.headers["Cache-Control"] = "no-store"
        return response
